"""待办相关服务：封装 M3 待办功能。"""
from __future__ import annotations

from typing import Optional

from core.todo import generate_todos_for_notice as _generate_todos_for_notice
from storage.db import get_connection, get_todos as _get_todos, set_todo_status


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
