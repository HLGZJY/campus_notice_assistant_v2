"""冻结感知的应用根目录解析（打包支持）。

PyInstaller onedir 模式下，模块的 ``__file__`` 指向 ``_internal/`` 内部目录，
而用户数据（data/）、可写配置（config/）、前端产物（frontend/dist/）都应位于
exe 同级目录。所有运行时路径解析统一走本模块：

- 开发模式：项目根 = ``utils/app_paths.py`` 的上上级目录
- 冻结模式：项目根 = exe 所在目录（``sys.executable`` 的父目录）

新增运行时路径时一律从这里取，不要再写 ``Path(__file__).parent``。

B16 起（v0.2.0 数据目录三态，见 docs/DESKTOP-BATCH-PLAN.md §B16）：
数据目录从「仅 dev/frozen 两态」扩展为 **dev / portable / installed 三态**：

- ``dev``      ：源码运行 → 仓库根 ``data/``（开发基线，行为不变）
- ``portable`` ：冻结且 exe 同级存在 ``data/``（老布局 / 便携版，向后兼容优先）
- ``installed``：冻结且 exe 同级无 ``data/`` → 用户数据落 ``%APPDATA%/CampusNoticeAssistant/data``
                （程序与数据分离，升级可整目录替换）

迁移向导：``migrate_data_dir`` 只读复制 legacy（exe 同级 data）到 installed data，
校验后写 ``.migrated`` 标记，源数据保留、可重试、幂等（R8）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 数据目录名（各处统一引用）
DATA_DIR_NAME = "data"
# 便携模式显式标记（置于 exe 同级 data/ 下，强制走 portable）
PORTABLE_MARKER = ".portable"
# 迁移完成标记（置于 legacy 源目录 data/ 下，标识已迁移，避免重复复制）
MIGRATED_MARKER = ".migrated"
# installed 态用户数据根（%APPDATA%/CampusNoticeAssistant）
_INSTALLED_DIR_NAME = "CampusNoticeAssistant"


def get_app_root() -> Path:
    """返回应用根目录（开发 = 仓库根；冻结 = exe 所在目录）。"""
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _frozen_exe_dir() -> Path:
    """冻结模式下 exe 所在目录。"""
    return Path(sys.executable).resolve().parent


def _installed_data_dir() -> Path:
    """installed 态用户数据目录：%APPDATA%/CampusNoticeAssistant/data。

    %APPDATA% 缺失（极罕见）时回退到 exe 同级 data，避免启动失败。
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata).resolve() / _INSTALLED_DIR_NAME / DATA_DIR_NAME
    # 兜底：回退到 exe 同级（与 portable 相同，至少可用）
    return _frozen_exe_dir() / DATA_DIR_NAME


def get_data_mode() -> str:
    """返回数据目录三态：``dev`` / ``portable`` / ``installed``。

    判定优先级：
    1. 未冻结（源码运行）→ ``dev``；
    2. 冻结且 exe 同级存在 ``data/.portable`` 标记 或 存在 ``data/`` → ``portable``；
    3. 冻结且无 exe 同级 data → ``installed``（数据落 %APPDATA%）。
    """
    if not getattr(sys, "frozen", False):
        return "dev"
    exe_dir = _frozen_exe_dir()
    if (exe_dir / DATA_DIR_NAME / PORTABLE_MARKER).exists():
        return "portable"
    if (exe_dir / DATA_DIR_NAME).exists():
        return "portable"
    return "installed"


def get_data_dir() -> Path:
    """用户数据目录（SQLite / Chroma / 日志 / 体检报告）。

    三态解析：
    - ``dev``      → 仓库根 ``data``
    - ``portable`` → exe 同级 ``data``（向后兼容，优先读老数据）
    - ``installed``→ ``%APPDATA%/CampusNoticeAssistant/data``
    """
    mode = get_data_mode()
    if mode == "dev":
        return get_app_root() / DATA_DIR_NAME
    if mode == "portable":
        return _frozen_exe_dir() / DATA_DIR_NAME
    return _installed_data_dir()


def get_config_dir() -> Path:
    """可写配置目录（app.yaml / schools/ / source_catalog.yaml）。

    B16 只对「数据目录」三态化（标题与迁移目标均为 data）；配置仍随应用根
    （冻结 = exe 同级 config），与 build.py 布局严格对齐。见模块文档说明。
    """
    return get_app_root() / "config"


def get_frontend_dist() -> Path:
    """前端构建产物目录（frontend/dist）。"""
    return get_app_root() / "frontend" / "dist"


def get_env_path() -> Path:
    """.env 文件路径（API key，安装后用户可编辑）。"""
    return get_app_root() / ".env"


def resolve_legacy_data_dir() -> Path | None:
    """返回老版本布局的 data 目录（exe 同级 ``data``）。

    用于迁移向导识别「老用户数据」：
    - 冻结模式：exe 同级存在 ``data/`` → 返回该路径（作为迁移源）；
    - 冻结模式且无 exe 同级 data，或未冻结（dev）→ 返回 ``None``（无 legacy 可迁）。
    """
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = _frozen_exe_dir()
    legacy = exe_dir / DATA_DIR_NAME
    return legacy if legacy.exists() else None


def migrate_data_dir(
    source: Path | None = None,
    target: Path | None = None,
) -> str:
    """只读复制 legacy 数据到 installed 数据目录（R8 迁移向导核心）。

    契约：
    - **只读源**：只读复制，绝不删除/改写源目录，失败可回退旧目录继续用；
    - **校验**：复制后逐文件比对大小（至少保证数量一致 + 源目录已存在的文件 size 一致）；
    - **幂等**：目标已有数据 / 源已带 ``.migrated`` 标记 → 返回 ``already_migrated``，不重复；
    - **可重试**：中途失败抛 ``OSError``，源保持不变，下次可重跑。

    Returns:
        ``"migrated"``        本次完成迁移
        ``"already_migrated"`` 已迁移（幂等命中），无需重复
        ``"no_legacy"``       无源数据，无需迁移
        ``"same_dir"``        源 == 目标（portable 直用），无需迁移
    """
    source = source or resolve_legacy_data_dir()
    target = target or get_data_dir()

    if source is None or not source.exists():
        return "no_legacy"
    if source.resolve() == target.resolve():
        return "same_dir"

    # 幂等：源已带迁移标记 → 视为已完成
    if (source / MIGRATED_MARKER).exists():
        return "already_migrated"
    # 幂等：目标已存在且非空（迁移过 / 已有新数据）→ 视为已完成
    if target.exists() and any(target.iterdir()):
        return "already_migrated"

    # 复制（只读源，不删除任何源文件）
    target.mkdir(parents=True, exist_ok=True)
    for src_path in sorted(source.rglob("*")):
        rel = src_path.relative_to(source)
        dst = target / rel
        if src_path.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            # shutil.copy2 保留元数据；文件已存在且同大小则跳过（幂等 + 续传）
            if dst.exists() and dst.stat().st_size == src_path.stat().st_size:
                continue
            import shutil

            shutil.copy2(src_path, dst)

    # 校验：目标文件数量 ≥ 源文件数量，且源已有的文件在目标中 size 一致
    src_files = [p for p in source.rglob("*") if p.is_file() and p.name != MIGRATED_MARKER]
    for src_path in src_files:
        rel = src_path.relative_to(source)
        dst = target / rel
        if not dst.exists() or dst.stat().st_size != src_path.stat().st_size:
            raise OSError(f"迁移校验失败：{rel} 复制不完整（{dst} 缺失或大小不符）")

    # 写迁移标记（源目录，标识已迁移）
    try:
        (source / MIGRATED_MARKER).write_text("migrated\n", encoding="utf-8")
    except OSError:
        # 标记写失败不阻断（源只读场景下可容忍；数据已复制完成）
        pass

    return "migrated"


def get_version() -> str:
    """读取应用版本号（根目录 VERSION 文件，单行）。

    读不到时返回 "0.0.0"（开发环境无 VERSION 文件不打断启动）。
    """
    version_file = get_app_root() / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"
