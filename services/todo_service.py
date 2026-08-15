"""待办相关服务：封装 M3 待办功能。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from core.todo import generate_todos_for_notice as _generate_todos_for_notice
from storage.db import (
    _UNSET,
    get_connection,
    get_todo_by_id,
    get_todos as _get_todos,
    resolve_reminders_for_todo,
    set_todo_status,
    update_todo as _update_todo,
)


def _valid_due_at(s: str) -> bool:
    """截止时间可解析即合法（兼容带/不带时刻的 ISO）。"""
    try:
        datetime.fromisoformat(s)
        return True
    except ValueError:
        pass
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def get_todos(status: Optional[str] = None) -> list[dict]:
    """查询待办列表，按截止时间升序排列。"""
    conn = get_connection()
    try:
        rows = _get_todos(conn, status=status)
        return [dict(r) for r in rows]
    finally:
        conn.close()


def generate_todos(notice_id: int) -> dict:
    """为指定通知生成待办。"""
    try:
        outcome = _generate_todos_for_notice(notice_id, replace=True, dry_run=False)
        return {
            "success": outcome.status == "generated",
            "status": outcome.status,
            "items": [item.model_dump() for item in outcome.items],
            "error": outcome.error,
        }
    except Exception as e:
        return {
            "success": False,
            "status": "failed",
            "items": [],
            "error": f"{type(e).__name__}: {e}",
        }


def mark_todo(todo_id: int, status: str) -> bool:
    """更新待办状态：pending / done / skipped。"""
    if status not in {"pending", "done", "skipped"}:
        raise ValueError(f"无效状态: {status}")
    conn = get_connection()
    try:
        return set_todo_status(conn, todo_id, status)
    finally:
        conn.close()


def update_todo(
    todo_id: int,
    action: Optional[str] = None,
    due_at: object = _UNSET,
    notes: object = _UNSET,
) -> Optional[dict]:
    """更新待办部分字段（action / due_at / notes）。

    语义：action 传 None 表示不修改；due_at / notes 传 _UNSET 表示不修改，
    显式传 None 表示清空为 NULL。due_at 变更时将该待办旧的待处理提醒收敛为
    已读，避免与新截止时间矛盾的临期提醒滞留。
    返回更新后的待办 dict；待办不存在返回 None。
    """
    if action is not None and not action.strip():
        raise ValueError("待办内容不能为空")
    conn = get_connection()
    try:
        if get_todo_by_id(conn, todo_id) is None:
            return None
        kw: dict = {}
        if action is not None:
            kw["action"] = action.strip()
        if due_at is not _UNSET:
            if due_at is not None and not _valid_due_at(due_at):
                raise ValueError(f"无效截止时间: {due_at}")
            kw["due_at"] = due_at
        if notes is not _UNSET:
            kw["notes"] = notes
        _update_todo(conn, todo_id, **kw)
        if "due_at" in kw:
            resolve_reminders_for_todo(conn, todo_id, status="read")
        return get_todo_by_id(conn, todo_id)
    finally:
        conn.close()


def get_todo_stats() -> dict:
    """统计待办状态数量。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM todos GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["n"] for r in rows}
        return {
            "pending": stats.get("pending", 0),
            "done": stats.get("done", 0),
            "skipped": stats.get("skipped", 0),
            "total": sum(stats.values()),
        }
    finally:
        conn.close()


def get_todos_by_notice(notice_id: int) -> list[dict]:
    """查询某个通知关联的待办。"""
    conn = get_connection()
    try:
        rows = _get_todos(conn, notice_id=notice_id)
        return [dict(r) for r in rows]
    finally:
        conn.close()
