"""阶段 4 异步任务模型的验收验证：任务级断点续跑语义（离线，不依赖 HTTP/LLM/网络）。

覆盖验收信号：
  1. 提交 extract_batch 任务 → 单步驱动 _run_one 时被模拟 kill → 任务留在 running；
  2. 「重启」新建 TaskManager → start() 把遗留 queued/running 任务标记 failed
     （error=进程重启中断，记录保留，可重新提交）；
  3. 重新提交 → _run_one 完成 → 每条通知恰 1 次提取计费（token_usage 按 notice_id 分组）；
  4. 状态流转 queued→running→(killed)→failed→running→success、progress 单调、
     result_json 可解析、未知任务类型提交抛 ValueError、单 worker 串行、业务异常标 failed。

用法：python test_tasks.py
"""
import asyncio
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

import storage.db
from core.extractor import ExtractionOutcome
from core.models import NoticeExtraction
from storage.db import get_connection, log_llm_usage

TMP_DB = Path(__file__).parent / "data" / "test_tasks.db"

storage.db.DB_PATH = TMP_DB


class SimulatedKill(BaseException):
    """模拟进程被杀：继承 BaseException，让 `except Exception` 无法吞掉（硬中断）。"""


class FakeExtractor:
    """假提取器：按通知计数调用、每次成功调用写一条计费记录，并可模拟中途被 kill。"""

    def __init__(self, kill_after=None):
        self.kill_after = kill_after  # 已完成多少次调用后，下一次调用模拟被杀
        self.completed = 0
        self._kill_fired = False
        self.calls_by_notice: dict[int, int] = defaultdict(int)

    async def extract_one(
        self,
        title: str,
        content: str,
        published_at=None,
        crawled_at=None,
        notice_id=None,
    ) -> ExtractionOutcome:
        nid = notice_id
        self.calls_by_notice[nid] += 1

        # 模拟崩溃：完成 kill_after 次后，下一次调用直接被"杀掉"（不计费、不写库）
        if (
            self.kill_after is not None
            and not self._kill_fired
            and self.completed >= self.kill_after
        ):
            self._kill_fired = True
            raise SimulatedKill(f"模拟进程在提取 notice_id={nid} 时被杀")

        self.completed += 1
        # 对齐真实 _call：只有调用成功才记账
        conn = get_connection()
        try:
            log_llm_usage(
                conn,
                task="extraction",
                model="fake-model",
                input_tokens=100,
                output_tokens=50,
                success=True,
                retry_count=0,
                notice_id=nid,
            )
        finally:
            conn.close()
        ext = NoticeExtraction(notice_type="competition", title=title, summary="模拟提取结果")
        return ExtractionOutcome(status="extracted", extraction=ext, error=None)


def reset_db():
    """删除临时库，下次 get_connection() 自动重建 SCHEMA（含 tasks / token_usage 表）。"""
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


def insert_notices(conn, rows):
    """批量插入 raw 通知。rows: list[(url, source, title)]"""
    for url, source, title in rows:
        conn.execute(
            """INSERT INTO notices (url, source, title, raw_content, crawled_at, status)
               VALUES (?, ?, ?, ?, ?, 'raw')""",
            (url, source, title, f"{title} 正文内容", "2026-01-01T00:00:00"),
        )
    conn.commit()


async def _drive(manager):
    """单步驱动 worker 认领并执行一个任务（不依赖后台轮询循环）。"""
    await manager._run_one()


def run():
    reset_db()
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    from api.tasks.manager import TaskManager

    print("== 0. tasks 表由 SCHEMA 自动创建 ==")
    conn = get_connection()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    check(
        "tasks 列齐全",
        {"id", "type", "params_json", "status", "progress", "result_json", "error", "created_at", "updated_at"}
        <= cols,
        f"cols={sorted(cols)}",
    )
    conn.close()

    # ---------- Part 1：任务级断点续跑 ----------
    print("\n== 1. 任务级断点续跑：kill → 重启恢复 failed → 重提完成 ==")
    conn = get_connection()
    insert_notices(
        conn,
        [(f"https://tasks.example/notice/{i}.htm", "测试来源", f"通知{i}") for i in range(1, 11)],
    )
    conn.close()

    mgr1 = TaskManager(deps={"extractor": FakeExtractor(kill_after=2)})

    # 1.1 提交 → queued
    task_id = mgr1.submit("extract_batch", {"limit": 100, "auto_index": False})
    rec = mgr1.get(task_id)
    check("提交后 status=queued", rec["status"] == "queued", f"status={rec['status']}")
    check("提交后 progress=0", rec["progress"] == 0, f"progress={rec['progress']}")

    # 1.2 单步驱动 _run_one，被 SimulatedKill 中断 → 任务留 running
    killed = False
    try:
        asyncio.run(_drive(mgr1))
    except SimulatedKill:
        killed = True
    check("第一次 _run_one 被模拟 kill 中断", killed)

    rec = mgr1.get(task_id)
    check("kill 后任务留 running（记录未丢）", rec["status"] == "running", f"status={rec['status']}")
    check("kill 后 progress>0 且 <1", 0 < rec["progress"] < 1, f"progress={rec['progress']}")

    # 1.3 「重启」：新建 TaskManager → start() 恢复遗留任务为 failed
    mgr2 = TaskManager()

    async def _recover_and_check(mgr):
        await mgr.start()
        rec = mgr.get(task_id)
        check("重启后遗留任务标记 failed", rec["status"] == "failed", f"status={rec['status']}")
        check(
            "failed error=进程重启中断文案",
            rec["error"] == "进程重启中断，任务未完成，请重新提交",
            f"error={rec['error']}",
        )
        check(
            "恢复后记录保留（params 原样）",
            rec["params"] == {"limit": 100, "auto_index": False},
            f"params={rec['params']}",
        )
        await mgr.stop()

    asyncio.run(_recover_and_check(mgr2))

    # 1.4 重新提交 → _run_one 完成 → success（progress 单调由 spy 记录）
    mgr3 = TaskManager(deps={"extractor": FakeExtractor()})
    task_id2 = mgr3.submit("extract_batch", {"limit": 100, "auto_index": False})

    import api.tasks.manager as manager_mod

    recorded: list[float] = []
    orig_update = manager_mod.update_task_progress

    def spy(conn, tid, frac):
        recorded.append(frac)
        return orig_update(conn, tid, frac)

    manager_mod.update_task_progress = spy
    try:
        asyncio.run(_drive(mgr3))
    finally:
        manager_mod.update_task_progress = orig_update

    rec = mgr3.get(task_id2)
    check("重提任务 status=success", rec["status"] == "success", f"status={rec['status']}")
    check("success progress=1.0", rec["progress"] == 1.0, f"progress={rec['progress']}")
    check(
        "result_json 可解析为 dict（含 processed/summary）",
        isinstance(rec["result"], dict)
        and rec["result"].get("processed") == 8
        and isinstance(rec["result"].get("summary"), dict),
        f"result={rec['result']}",
    )
    check(
        "progress 单调非递减且收敛到 1.0",
        bool(recorded) and recorded == sorted(recorded) and recorded[-1] == 1.0,
        f"recorded={recorded}",
    )

    conn = get_connection()
    billing = {
        r["notice_id"]: r["c"]
        for r in conn.execute(
            "SELECT notice_id, COUNT(*) AS c FROM token_usage WHERE task='extraction' GROUP BY notice_id"
        ).fetchall()
    }
    conn.close()
    check(
        "计费：同一批 10 条通知各只有 1 条提取计费记录（重启不重复计费）",
        billing == {nid: 1 for nid in range(1, 11)},
        f"billing={billing}",
    )

    # ---------- Part 2：未知任务类型 ----------
    print("\n== 2. 未知任务类型提交被拒绝 ==")
    try:
        TaskManager().submit("nonexistent_type")
        rejected = False
    except ValueError:
        rejected = True
    check("未知 type 抛 ValueError（路由转 400）", rejected)

    # ---------- Part 3：单 worker 串行 ----------
    print("\n== 3. 单 worker 串行：同一时刻只处理一个任务 ==")
    reset_db()
    conn = get_connection()
    insert_notices(
        conn,
        [(f"https://tasks.example/notice/{i}.htm", "测试来源", f"通知{i}") for i in range(1, 4)],
    )
    conn.close()

    mgr4 = TaskManager(deps={"extractor": FakeExtractor()})
    t1 = mgr4.submit("extract_batch", {"limit": 100, "auto_index": False})
    t2 = mgr4.submit("extract_batch", {"limit": 100, "auto_index": False})
    check("第二个任务提交后排队 queued", mgr4.get(t2)["status"] == "queued", f"status={mgr4.get(t2)['status']}")

    asyncio.run(_drive(mgr4))
    check("驱动第一次后 t1 success", mgr4.get(t1)["status"] == "success", f"status={mgr4.get(t1)['status']}")
    check(
        "t2 仍 queued（单 worker 串行，未并发抢跑）",
        mgr4.get(t2)["status"] == "queued",
        f"status={mgr4.get(t2)['status']}",
    )

    asyncio.run(_drive(mgr4))
    check("驱动第二次后 t2 success", mgr4.get(t2)["status"] == "success", f"status={mgr4.get(t2)['status']}")

    # ---------- Part 4：业务异常 → failed（worker 不崩，队列继续） ----------
    print("\n== 4. 业务异常标记任务 failed ==")
    import api.tasks.workers as workers_mod

    def boom_worker(task, progress_cb, deps):
        raise RuntimeError("boom")

    workers_mod.WORKERS["boom"] = boom_worker
    try:
        mgr5 = TaskManager()
        boom_id = mgr5.submit("boom")
        asyncio.run(_drive(mgr5))
        rec = mgr5.get(boom_id)
        check("boom 任务 status=failed", rec["status"] == "failed", f"status={rec['status']}")
        check("failed error 含异常类型与信息", "RuntimeError" in (rec["error"] or "") and "boom" in (rec["error"] or ""), f"error={rec['error']}")
        check("failed 任务记录保留（result 为 None）", rec["result"] is None, f"result={rec['result']}")
    finally:
        workers_mod.WORKERS.pop("boom", None)

    cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


def cleanup():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    run()
