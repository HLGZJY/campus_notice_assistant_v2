"""桌面版自动备份（B17 / K8 配套）。

SQLite 开启 WAL 后，data/ 目录会出现 `-wal` / `-shm` 附属文件。若用「拷贝主库文件」
的方式备份，未 checkpoint 的最近提交会丢——所以本模块用 SQLite 官方备份 API
（``sqlite3.Connection.backup``）做一致性快照：它透明读取 WAL，无论当前处于何种
journal 状态都能产出一份完整、一致、可直接还原的主库文件，**备份无需关心 -wal/-shm
是否需要随附**（快照文件本身已含全部已提交数据）。

契约：
- ``create_backup``：对指定库做一致性快照到 ``data/backups/``，命名含时间戳，
  完成后按 ``keep`` 保留最近 N 份、清理更旧的（含其 -wal/-shm 残留）。
- ``weekly_backup``：按「最近一次备份时间」判断是否 ≥ 7 天，是则触发 ``create_backup``。
  通过 ``data/backups/.last_backup`` 时间戳文件记账，无备份历史时立即触发一次。
- 所有操作都在**临时快照副本**上完成，绝不触碰生产库连接，可安全在调度线程调用。

安全：备份目录默认 ``get_data_dir()/backups``；时间戳用 ``YYYYmmdd-HHMMSS``。
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from utils.app_paths import get_data_dir

logger = logging.getLogger("campus.backup")

# 每周备份间隔
WEEKLY_INTERVAL = timedelta(days=7)
# 默认保留份数
DEFAULT_KEEP = 3
# 备份子目录名
BACKUP_DIR_NAME = "backups"
# 最近一次备份时间戳文件
LAST_BACKUP_FILE = ".last_backup"
# 时间戳格式（用于备份文件名）
_TS_FMT = "%Y%m%d-%H%M%S"


def _default_backup_dir() -> Path:
    """备份目录：<data>/backups。"""
    return get_data_dir() / BACKUP_DIR_NAME


def _cleanup_aux(db_path: Path) -> None:
    """清理数据库的 -wal / -shm 附属文件残留。

    备份结束后主库可能遗留旧 -wal/-shm；若这些附属文件属于「已被快照吸收」的陈旧内容，
    保留只会造成混淆。这里做**安全清理**：仅当对应主库存在时才尝试删除，失败不阻断。
    """
    for suffix in ("-wal", "-shm"):
        aux = Path(str(db_path) + suffix)
        try:
            if aux.exists():
                aux.unlink()
                logger.info("清理备份残留附属文件：%s", aux.name)
        except OSError as exc:  # noqa: BLE001 - 清理失败不阻断备份
            logger.warning("清理 %s 失败（忽略）：%s", aux.name, exc)


def create_backup(
    db_path: Path | None = None,
    backup_dir: Path | None = None,
    keep: int = DEFAULT_KEEP,
) -> Path:
    """对指定 SQLite 库做一致性备份快照，返回备份文件路径。

    Args:
        db_path: 源库路径；None 时用 storage.db.DB_PATH（生产库 data/notices.db）。
        backup_dir: 备份目录；None 时用 <data>/backups。
        keep: 保留最近 N 份，更旧的连同 -wal/-shm 一起清理。

    Returns:
        Path：本次生成的备份文件路径。

    Raises:
        sqlite3.Error: 源库无法打开或备份失败。
        OSError: 备份目录不可写。
    """
    if db_path is None:
        # 延迟 import，避免顶层循环依赖（storage.db 不 import desktop.backup）
        from storage.db import DB_PATH

        db_path = DB_PATH

    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"源库不存在：{src}")

    backup_dir = backup_dir or _default_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 快照文件名：notices-YYYYMMDD-HHMMSS.db
    stamp = datetime.now().strftime(_TS_FMT)
    dest = backup_dir / f"{src.stem}-{stamp}.db"

    # 用 SQLite backup API 做一致性快照：透明吸收 WAL，产出的主库文件可直接还原。
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    # 清理快照文件自身的 -wal/-shm（备份 API 产出的是干净主库，不应有附属文件）
    _cleanup_aux(dest)

    _prune(backup_dir, keep)
    _record_last_backup(backup_dir)
    logger.info("已生成备份：%s（保留 %d 份）", dest.name, keep)
    return dest


def _prune(backup_dir: Path, keep: int) -> None:
    """按时间保留最近 keep 份备份，清理更旧的备份文件及其 -wal/-shm 残留。"""
    backups = sorted(
        (p for p in backup_dir.glob("*.db") if p.name != LAST_BACKUP_FILE),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in backups[keep:]:
        _cleanup_aux(old)
        try:
            old.unlink()
            logger.info("清理旧备份：%s", old.name)
        except OSError as exc:  # noqa: BLE001
            logger.warning("清理旧备份 %s 失败（忽略）：%s", old.name, exc)


def _record_last_backup(backup_dir: Path) -> None:
    """写最近备份时间戳。写失败可容忍（下次按文件时间兜底）。"""
    try:
        (backup_dir / LAST_BACKUP_FILE).write_text(
            datetime.now().isoformat(timespec="seconds"), encoding="utf-8"
        )
    except OSError:
        pass


def _last_backup_time(backup_dir: Path) -> datetime | None:
    """读取最近一次备份时间；读不到返回 None。"""
    marker = backup_dir / LAST_BACKUP_FILE
    try:
        return datetime.fromisoformat(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def weekly_backup(
    db_path: Path | None = None,
    backup_dir: Path | None = None,
    keep: int = DEFAULT_KEEP,
) -> bool:
    """每周自动备份入口：距上次备份 ≥ 7 天（或无备份历史）则执行备份。

    由调度器周期性调用（如每日检查）；通过时间戳文件判频，避免在应用未运行期间
    错过整周后因 CronTrigger 误触发而重复备份。

    Returns:
        bool：本次是否实际执行了备份。
    """
    backup_dir = backup_dir or _default_backup_dir()
    last = _last_backup_time(backup_dir)
    if last is not None and datetime.now() - last < WEEKLY_INTERVAL:
        return False
    create_backup(db_path=db_path, backup_dir=backup_dir, keep=keep)
    return True


def restore_backup(backup_file: Path, dest: Path) -> None:
    """从备份文件还原到指定目标库（D-43 人工验收：备份可恢复）。

    用 SQLite backup API 把备份文件复制到目标路径；目标已存在则覆盖（先移除
    -wal/-shm 避免残留干扰）。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_aux(dest)
    if dest.exists():
        dest.unlink()

    src_conn = sqlite3.connect(str(backup_file))
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    logger.info("已从备份还原：%s -> %s", backup_file.name, dest.name)


if __name__ == "__main__":
    # 命令行入口：python -m desktop.backup [keep]
    keep = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_KEEP
    created = create_backup(keep=keep)
    print(f"备份完成：{created}")
