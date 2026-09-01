"""B17 SQLite WAL 与备份验收（离线，全部跑在临时库上）。

对应 docs/DESKTOP-ACCEPTANCE.md §4：
  1. 连接初始化后 PRAGMA journal_mode = wal
  2. 读写并发不互斥（写事务进行中读不被阻塞）
  3. 并发读写压测不抛 database is locked
  4. 备份文件包含 -wal / -shm 处理逻辑
  5. PRAGMA integrity_check = ok

设计依据见 docs/DESKTOP-UPGRADE.md §6 K8：
PRAGMA journal_mode=WAL + synchronous=NORMAL，收益是读写不互斥，
托盘后台跑调度时前端轮询不卡顿；代价是 data/ 会出现 -wal / -shm 文件，
**备份脚本必须一并处理**，否则备份不完整。

安全：所有断言一律在 data/_tmp_*.db 上运行，绝不触碰生产库 data/notices.db。

依赖：storage.db.apply_wal_pragmas（B17 实现后自动激活；当前未实现则整脚本 SKIP）

用法：python test_desktop_wal.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite, temp_db_path  # noqa: E402

suite = Suite(batch="B17", module="storage.db", title="SQLite WAL 与备份（K8）")


@suite.case("1. 连接初始化后 PRAGMA journal_mode = wal")
def _(db):
    apply_wal = suite.require(db, "apply_wal_pragmas", "B17 在连接初始化处加 PRAGMA")
    with temp_db_path() as tmp:
        conn = apply_wal(db.get_connection(db_path=tmp))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal", f"期望 wal，实际 {mode}"
        conn.close()


@suite.case("2. 读写并发不互斥（写事务进行中读不被阻塞）")
def _(db):
    apply_wal = suite.require(db, "apply_wal_pragmas")
    suite.pending(
        "在临时库上开启一个未提交的写事务，同时另一连接执行 SELECT，"
        "断言读立即返回（WAL 下读不阻塞），超时即失败"
    )


@suite.case("3. 并发读写压测不抛 database is locked")
def _(db):
    apply_wal = suite.require(db, "apply_wal_pragmas")
    suite.pending(
        "起 4 写线程 + 4 读线程各跑若干次（沿用 test_db_concurrency.py 的压测强度），"
        "断言无 sqlite3.OperationalError: database is locked"
    )


@suite.case("4. 备份文件包含 -wal / -shm 处理逻辑")
def _(db):
    try:
        import importlib

        backup = importlib.import_module("desktop.backup")
    except Exception as exc:  # noqa: BLE001
        raise NotImplementedError(f"desktop.backup 尚未实现（B17）：{exc}")
    create_backup = suite.require(backup, "create_backup")
    suite.pending(
        "在临时库上写数据使其产生 -wal，调用 create_backup()，"
        "断言备份内容完整（能还原出全部行），且 -wal 被 checkpoint 或一并拷贝"
    )


@suite.case("5. PRAGMA integrity_check = ok")
def _(db):
    apply_wal = suite.require(db, "apply_wal_pragmas")
    with temp_db_path() as tmp:
        conn = apply_wal(db.get_connection(db_path=tmp))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('中文内容')")
        conn.commit()
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert str(result).lower() == "ok", f"integrity_check 失败：{result}"
        conn.close()


if __name__ == "__main__":
    sys.exit(suite.run())
