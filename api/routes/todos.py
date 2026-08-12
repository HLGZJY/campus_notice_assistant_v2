"""待办模块：列表 / 统计 / 生成 / 状态变更（盘点 §5.6 待办映射表）。

阶段 4 迁入任务模型：generate_todos 为 LLM 长耗时调用，改为「提交任务 + 返回 202
task_id」，前端轮询 GET /tasks/{id}；通知不存在的 404 在路由同步校验立即返回。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import require_auth
from api.routes.tasks import get_task_manager
from api.schemas import TaskCreateResult, TodoItem, TodoStats, TodoStatusUpdate
from storage.db import get_connection, get_notice_by_id
from services.todo_service import (
    get_todo_stats,
    get_todos,
    get_todos_by_notice,
    mark_todo,
)

router = APIRouter(
    prefix="/todos",
    tags=["todos"],
    dependencies=[Depends(require_auth)],
)

notice_router = APIRouter(
    prefix="/notices",
    tags=["todos"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=list[TodoItem])
def list_todos(status: Optional[str] = Query(default=None)) -> list[TodoItem]:
    """待办列表（可按状态过滤，无截止的排在最后）。"""
    return [TodoItem(**r) for r in get_todos(status=status)]


@router.get("/stats", response_model=TodoStats)
def todo_stats() -> TodoStats:
    """待办状态统计。"""
    return TodoStats(**get_todo_stats())


@router.post("/{todo_id}/status", response_model=dict)
def update_todo_status(todo_id: int, body: TodoStatusUpdate) -> dict:
    """更新待办状态：pending / done / skipped。done 时记 completed_at。"""
    try:
        ok = mark_todo(todo_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"待办 {todo_id} 不存在")
    return {"ok": ok, "id": todo_id, "status": body.status}


@notice_router.get("/{notice_id}/todos", response_model=list[TodoItem])
def list_notice_todos(notice_id: int) -> list[TodoItem]:
    """某个通知关联的待办。"""
    return [TodoItem(**r) for r in get_todos_by_notice(notice_id)]


@notice_router.post("/{notice_id}/todos", status_code=202, response_model=TaskCreateResult)
def generate_notice_todos(request: Request, notice_id: int) -> TaskCreateResult:
    """为指定通知生成待办（异步任务，202 返回 task_id 供轮询）。

    通知不存在的 404 路由同步校验立即返回；LLM 调用在 worker 线程执行，
    完成后经 GET /tasks/{id} 获取 TodoGenerateResult 形状的结果。
    """
    conn = get_connection()
    try:
        notice = get_notice_by_id(conn, notice_id)
    finally:
        conn.close()
    if notice is None:
        raise HTTPException(status_code=404, detail=f"通知 {notice_id} 不存在")
    task_id = get_task_manager(request).submit("generate_todos", {"notice_id": notice_id})
    return TaskCreateResult(task_id=task_id, type="generate_todos", status="queued")
