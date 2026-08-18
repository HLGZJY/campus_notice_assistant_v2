"""进程内 asyncio 任务管理器（阶段 4）。

- 任务状态落库（tasks 表），重启可恢复：start() 时把遗留 queued/running 任务
  标记为 failed（记录保留，用户可重新提交），复用 scheduler_log 的"落库即恢复"语义。
- 单 worker 串行执行：_worker_loop 轮询 DB 队列，同一时刻只跑一个任务，
  天然规避 SQLite 单写者 / ConfigStore 写权唯一 / Chroma 单 collection 的并发冲突。
- 阻塞业务函数经 asyncio.to_thread 运行：服务层内部各自的 asyncio.run 会在
  子线程里自建事件循环，不会与 API 进程的事件循环嵌套冲突。
- 进度由业务函数的 progress_cb 回调，在 worker 线程内直接开独立连接写库。

生命周期：api/main.py 的 lifespan 创建实例并 await start()/stop()；
路由经 request.app.state.task_manager 获取。测试可注入 deps（如 fake extractor）。
"""
from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Callable, Optional

from api.tasks.lock import compute_lock_key
from api.tasks.workers import WORKERS
from storage.db import (
    claim_next_task,
    complete_task,
    create_task_or_get_existing,
    fail_task,
    get_connection,
    get_task as db_get_task,
    list_tasks as db_list_tasks,
    recover_interrupted_tasks,
    update_task_progress,
)

logger = logging.getLogger(__name__)

# 与 storage.db.recover_interrupted_tasks 内联文案保持一致
RECOVER_ERROR = "进程重启中断，任务未完成，请重新提交"


class TaskManager:
    """异步任务管理器（单进程，FastAPI lifespan 创建并启动）。"""

    def __init__(self, poll_interval: float = 0.5, deps: Optional[dict] = None):
        self._poll_interval = poll_interval
        self._deps = deps or {}
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

    # ---------- 对外生命周期 ----------

    async def start(self) -> None:
        """启动：先恢复遗留任务，再拉起后台 worker 循环。"""
        recovered = self._recover_interrupted()
        if recovered:
            logger.info("任务管理器启动：恢复 %d 个遗留任务为 failed（可重新提交）", recovered)
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("任务管理器已启动")

    async def stop(self) -> None:
        """停止 worker 循环（遗留 running 任务由下次 start 恢复为 failed）。"""
        self._running = False
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 —— 停止路径吞掉取消异常
                pass
            self._worker_task = None
        logger.info("任务管理器已停止")

    # ---------- 提交 / 查询（同步，路由线程池可直接调用） ----------

    def submit(self, task_type: str, params: Optional[dict] = None) -> int:
        """提交一个 queued 任务，返回 task_id。未知 type 抛 ValueError。

        幂等去重：若已有 (type, lock_key) 相同且 queued/running 的任务，直接返回其 id。
        """
        if task_type not in WORKERS:
            raise ValueError(f"未知任务类型: {task_type}")
        lock_key = compute_lock_key(task_type, params or {})
        conn = get_connection()
        try:
            return create_task_or_get_existing(conn, task_type, params, lock_key)
        finally:
            conn.close()

    def get(self, task_id: int) -> Optional[dict]:
        """按 ID 查询任务（含解析后的 params/result）。"""
        conn = get_connection()
        try:
            return db_get_task(conn, task_id)
        finally:
            conn.close()

    def list(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        """最近任务列表。"""
        conn = get_connection()
        try:
            return db_list_tasks(conn, limit=limit, status=status)
        finally:
            conn.close()

    # ---------- 恢复 ----------

    def _recover_interrupted(self) -> int:
        """把上次进程遗留的 queued/running 任务标记为 failed（记录保留）。"""
        conn = get_connection()
        try:
            return recover_interrupted_tasks(conn)
        finally:
            conn.close()

    # ---------- worker ----------

    async def _worker_loop(self) -> None:
        """后台轮询：认领一个 queued 任务并执行，空则休眠。"""
        while self._running:
            await self._run_one()
            if self._running:
                await asyncio.sleep(self._poll_interval)

    async def _run_one(self) -> None:
        """认领并执行一个任务（可测缝：测试可单步驱动）。

        普通 Exception 捕获并标记任务失败（worker 继续）；
        BaseException（如测试注入的 SimulatedKill）不捕获，向上传播模拟进程崩溃。
        """
        conn = get_connection()
        try:
            task = claim_next_task(conn)
        finally:
            conn.close()
        if task is None:
            return

        task_id = task["id"]
        task_type = task["type"]
        worker_fn = WORKERS.get(task_type)
        if worker_fn is None:
            self._fail(task_id, f"未知任务类型: {task_type}")
            return

        try:
            result = await asyncio.to_thread(
                worker_fn, task, partial(self._progress_cb, task_id), self._deps
            )
        except Exception as e:  # noqa: BLE001 —— 业务失败标记任务失败，worker 继续
            logger.exception("任务 %s(%d) 执行失败", task_type, task_id)
            self._fail(task_id, f"{type(e).__name__}: {e}")
            return
        self._complete(task_id, result if isinstance(result, dict) else {"result": result})

    def _complete(self, task_id: int, result: dict) -> None:
        conn = get_connection()
        try:
            complete_task(conn, task_id, result)
        finally:
            conn.close()

    def _fail(self, task_id: int, error: str) -> None:
        conn = get_connection()
        try:
            fail_task(conn, task_id, error)
        finally:
            conn.close()

    def _progress_cb(self, task_id: int, frac: float) -> None:
        """业务 worker 的进度回调（worker 线程内直接写库，失败不影响任务主体）。"""
        conn = get_connection()
        try:
            update_task_progress(conn, task_id, frac)
        except Exception as e:  # noqa: BLE001
            logger.warning("写入任务进度失败 task_id=%s: %s", task_id, e)
        finally:
            conn.close()
