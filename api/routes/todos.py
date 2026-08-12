"""待办模块：列表 / 统计 / 生成 / 状态变更（盘点 §5.6 待办映射表）。

generate_todos 为 LLM 同步调用（阻塞，阶段 2 保持同步，前端 loading 态；
阶段 4 迁入任务模型后改为「提交任务 + 返回 task_id」）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import require_auth
from api.schemas import TodoGenerateResult, TodoItem, TodoStats, TodoStatusUpdate
from services.todo_service import (
    generate_todos,
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


@notice_router.post("/{notice_id}/todos", response_model=TodoGenerateResult)
def generate_notice_todos(notice_id: int) -> TodoGenerateResult:
    """为指定通知生成待办（LLM 同步调用，替换旧 pending；失败由模板兜底）。"""
    result = generate_todos(notice_id)
    items: list[dict] = []
    if result["success"] and result["items"]:
        # 生成即落库（replace 语义），回填主键使响应符合 TodoItem 契约
        rows = {
            r["action"]: r
            for r in get_todos_by_notice(notice_id)
            if r.get("status") == "pending"
        }
        items = [rows[it["action"]] for it in result["items"] if it["action"] in rows]
    return TodoGenerateResult(
        success=result["success"],
        status=result["status"],
        items=[TodoItem(**it) for it in items],
        error=result["error"],
    )
