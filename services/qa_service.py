"""问答相关服务：封装 M4 问答与索引功能。"""
from __future__ import annotations

from typing import Optional

from core.qa import QAResult, ask_question
from storage.db import get_connection, get_notice_by_id


def _get_vector_index():
    """延迟导入 VectorIndex，避免在不需要向量功能时触发重依赖。"""
    from storage.vectorstore import VectorIndex

    return VectorIndex()


def ask(question: str) -> QAResult:
    """基于已索引通知回答用户提问。"""
    return ask_question(question)


def get_index_stats() -> dict:
    """返回向量索引统计信息。导入失败时返回错误信息，避免 UI 崩溃。"""
    try:
        index = _get_vector_index()
        return {
            "chunks": index.count(),
            "persist_dir": index.stats().get("persist_dir", ""),
        }
    except Exception as e:
        return {
            "chunks": 0,
            "persist_dir": "",
            "error": f"{type(e).__name__}: {e}",
        }


def index_notice(notice_id: int) -> dict:
    """将单条通知增量加入向量索引。"""
    conn = get_connection()
    try:
        notice = get_notice_by_id(conn, notice_id)
        if not notice:
            return {"success": False, "error": "通知不存在"}
        notice = dict(notice)
    finally:
        conn.close()

    if not notice.get("raw_content"):
        return {"success": False, "error": "通知无正文内容"}

    try:
        index = _get_vector_index()
        result = index.add_notice(notice)
        return {"success": True, "notice_id": notice_id, "chunks": result["chunks"]}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def rebuild_index(statuses: Optional[list[str]] = None, dry_run: bool = False) -> dict:
    """全量重建向量索引。

    Args:
        statuses: 只索引指定状态的通知，默认 extracted 和 partial
        dry_run: 只统计不写入
    """
    statuses = statuses or ["extracted", "partial"]
    conn = get_connection()
    try:
        placeholders = ", ".join("?" * len(statuses))
        rows = conn.execute(
            f"SELECT * FROM notices WHERE status IN ({placeholders}) AND raw_content IS NOT NULL",
            statuses,
        ).fetchall()
        notices = [dict(r) for r in rows]
    finally:
        conn.close()

    index = _get_vector_index()
    result = index.rebuild(notices, dry_run=dry_run)
    return {
        "notices": result["notices"],
        "chunks": result["chunks"],
        "dry_run": dry_run,
    }


def remove_notice(notice_id: int) -> dict:
    """从向量索引中删除某通知。"""
    index = _get_vector_index()
    removed = index.remove_notice(notice_id)
    return {"success": True, "notice_id": notice_id, "removed": removed}
