"""阶段 A2 验收：SQLite 并发安全加固（离线）。

覆盖验收信号：
  1. get_connection 连接可跨线程使用（check_same_thread=False）。
  2. 多线程并发写同一库不抛 "database is locked"（timeout=30.0 兜底），行数一致。
  3. get_task_connection 同任务上下文复用同一连接；不同任务上下文各持专属连接；
     close_task_connection 复位。

用法：python test_db_concurrency.py
"""
import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import storage.db
from storage.db import close_task_connection, get_connection, get_task_connection

TMP_DB = Path(__file__).parent / "data" / "test_db_concurrency.db"

storage.db.DB_PATH = TMP_DB

THREADS = 8
INSERTS_PER_THREAD = 50


def cleanup():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


def run():
    cleanup()
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    print("== 1. 连接可跨线程使用（check_same_thread=False）==")
    conn = get_connection()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS scratch (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT)"
    )
    conn.commit()
    cross_thread = {}

    def _read_in_thread():
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM scratch").fetchone()
            cross_thread["n"] = row["n"]
            cross_thread["ok"] = True
        except Exception as e:
            cross_thread["ok"] = False
            cross_thread["err"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=_read_in_thread)
    t.start()
    t.join()
    check(
        "子线程复用主线程连接查询成功",
        cross_thread.get("ok") is True,
        f"n={cross_thread.get('n')} err={cross_thread.get('err')}",
    )

    print("== 2. 多线程并发写：不抛 locked，行数一致 ==")
    conn.execute("DELETE FROM scratch")
    conn.commit()
    conn.close()

    errors: list[str] = []
    barrier = threading.Barrier(THREADS)

    def _writer(tid: int):
        try:
            barrier.wait(timeout=60)
            c = get_connection()
            for i in range(INSERTS_PER_THREAD):
                c.execute("INSERT INTO scratch (note) VALUES (?)", (f"t{tid}-{i}",))
            c.commit()
            c.close()
        except Exception as e:  # noqa: BLE001
            errors.append(f"t{tid}: {type(e).__name__}: {e}")

    threads = [threading.Thread(target=_writer, args=(t,)) for t in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) AS n FROM scratch").fetchone()["n"]
    conn.close()
    check("并发写无异常", not errors, f"errors={errors[:3]}")
    check(
        "行数与写入量一致",
        total == THREADS * INSERTS_PER_THREAD,
        f"total={total} expected={THREADS * INSERTS_PER_THREAD}",
    )

    print("== 3. get_task_connection：同上下文复用，close 复位，并发任务隔离 ==")
    conn_a = None

    async def _first():
        nonlocal conn_a
        c1 = get_task_connection()
        c2 = get_task_connection()
        conn_a = (id(c1), id(c2), c1 is c2)
        close_task_connection()

    asyncio.run(_first())
    check(
        "同上下文内复用同一连接",
        conn_a[2] is True,
        f"{conn_a[0]} vs {conn_a[1]}",
    )

    c_x = get_task_connection()
    c_y = get_task_connection()
    check("close 前同上下文复用", c_x is c_y, f"{c_x} vs {c_y}")
    close_task_connection()
    c_z = get_task_connection()
    check(
        "close 后重新获取得到新连接",
        c_x is not c_z,
        f"x={c_x} z={c_z}",
    )
    close_task_connection()

    results: dict[str, int] = {}

    async def _worker(name: str):
        c = get_task_connection()
        results[name] = id(c)
        await asyncio.sleep(0.01)
        close_task_connection()

    async def _gather():
        await asyncio.gather(_worker("a"), _worker("b"))

    asyncio.run(_gather())
    check(
        "并发任务各持专属连接",
        bool(results.get("a")) and bool(results.get("b")) and results["a"] != results["b"],
        f"a={results.get('a')} b={results.get('b')}",
    )

    cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()