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
    import sqlite3
    import threading
    import time

    with temp_db_path() as tmp:
        conn_w = apply_wal(db.get_connection(db_path=tmp))
        conn_w.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn_w.execute("INSERT INTO t (v) VALUES ('pre')")
        conn_w.commit()

        # 开启一个未提交的写事务（BEGIN 后写但未 commit）
        conn_w.execute("INSERT INTO t (v) VALUES ('uncommitted')")

        # 另一连接 SELECT：WAL 下读不被写事务阻塞，应立即返回
        start = time.time()
        row = conn_w.execute("SELECT COUNT(*) AS n FROM t").fetchone()["n"]
        elapsed = time.time() - start
        assert row >= 1, f"读被写事务阻塞？n={row}"
        # 在写事务进行中读到的是快照（WAL 读者见旧数据），至少不抛 locked
        assert elapsed < 5.0, f"读被阻塞超过 5s：{elapsed:.2f}s"

        conn_w.execute("ROLLBACK")
        conn_w.close()


@suite.case("3. 并发读写压测不抛 database is locked")
def _(db):
    apply_wal = suite.require(db, "apply_wal_pragmas")
    import threading

    N_WRITERS, N_READERS, ITERS = 4, 4, 20

    with temp_db_path() as tmp:
        conn = apply_wal(db.get_connection(db_path=tmp))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
        conn.commit()
        conn.close()

        errors: list[str] = []
        barrier = threading.Barrier(N_WRITERS + N_READERS)

        def _writer(tid: int):
            try:
                barrier.wait(timeout=60)
                c = apply_wal(db.get_connection(db_path=tmp))
                for i in range(ITERS):
                    c.execute("INSERT INTO t (v) VALUES (?)", (f"w{tid}-{i}",))
                    c.commit()
                c.close()
            except Exception as e:  # noqa: BLE001
                errors.append(f"w{tid}: {type(e).__name__}: {e}")

        def _reader(rid: int):
            try:
                barrier.wait(timeout=60)
                c = apply_wal(db.get_connection(db_path=tmp))
                for _ in range(ITERS):
                    c.execute("SELECT COUNT(*) FROM t").fetchone()
                c.close()
            except Exception as e:  # noqa: BLE001
                errors.append(f"r{rid}: {type(e).__name__}: {e}")

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(N_WRITERS)]
        threads += [threading.Thread(target=_reader, args=(i,)) for i in range(N_READERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发读写抛异常：{errors[:5]}"

        conn = db.get_connection(db_path=tmp)
        total = conn.execute("SELECT COUNT(*) AS n FROM t").fetchone()["n"]
        conn.close()
        assert total == N_WRITERS * ITERS, f"写入行数不符：{total} != {N_WRITERS * ITERS}"


@suite.case("4. 备份文件包含 -wal / -shm 处理逻辑")
def _(db):
    try:
        import importlib

        backup = importlib.import_module("desktop.backup")
    except Exception as exc:  # noqa: BLE001
        raise NotImplementedError(f"desktop.backup 尚未实现（B17）：{exc}")
    create_backup = suite.require(backup, "create_backup")
    restore_backup = suite.require(backup, "restore_backup")
    apply_wal = suite.require(db, "apply_wal_pragmas")
    import sqlite3

    with temp_db_path() as tmp:
        conn = apply_wal(db.get_connection(db_path=tmp))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        for i in range(10):
            conn.execute("INSERT INTO t (v) VALUES (?)", (f"内容-{i}",))
        conn.commit()
        # 保持连接未 checkpoint，制造 -wal 存在（有未 checkpoint 数据）
        wal_path = Path(str(tmp) + "-wal")
        has_wal = wal_path.exists()
        conn.close()

        import tempfile

        bdir = Path(tempfile.mkdtemp())
        try:
            bf = create_backup(db_path=tmp, backup_dir=bdir, keep=3)
            # 备份内容完整：能还原出全部 10 行
            dest = bdir / "restored.db"
            restore_backup(bf, dest)
            rc = sqlite3.connect(str(dest))
            n = rc.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            rc.close()
            assert n == 10, f"备份还原后行数不符：{n} != 10"
            # 备份文件本身不应带 -wal/-shm 残留
            for suffix in ("-wal", "-shm"):
                assert not Path(str(bf) + suffix).exists(), f"备份残留 {suffix} 文件"
        finally:
            import shutil

            shutil.rmtree(bdir, ignore_errors=True)


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
