"""管理相关服务：通知 / 待办 / 索引的 CRUD 与批量操作。

M6 新增：
  - 删除通知（级联删除 todos + Chroma chunks）
  - 重新提取（重置状态 → 调用提取流程）
  - 按来源 / 状态批量删除
  - 全量重建 Chroma 索引
"""
from __future__ import annotations

import logging
from typing import Optional

from services.notice_service import extract_notice
from storage.db import (
    delete_notice as _delete_notice,
    delete_notices_by_source as _delete_notices_by_source,
    delete_notices_by_status as _delete_notices_by_status,
    delete_reminders_for_todo,
    get_connection,
    get_notice_by_id,
    reset_notice_status,
)

logger = logging.getLogger(__name__)


def _get_vector_index():
    """延迟导入，避免不需要向量功能时触发重依赖。"""
    from storage.vectorstore import VectorIndex

    return VectorIndex()


# ---------- 单条通知 CRUD ----------

def delete_notice(notice_id: int) -> dict:
    """删除单条通知，并级联删除其待办与 Chroma chunks。"""
    conn = get_connection()
    try:
        notice = get_notice_by_id(conn, notice_id)
        if not notice:
            return {"ok": False, "error": "通知不存在"}

        # 1. 从向量索引删除
        try:
            index = _get_vector_index()
            index.remove_notice(notice_id)
        except Exception as e:
            logger.warning("删除 notice_id=%s 的向量 chunk 失败: %s", notice_id, e)

        # 2. 从 SQLite 删除（含关联 todos）
        count = _delete_notice(conn, notice_id)
        return {"ok": count > 0, "deleted_notices": count}
    except Exception as e:
        logger.exception("删除通知失败 notice_id=%s", notice_id)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()


def re_extract_notice(notice_id: int, auto_index: bool = True) -> dict:
    """重置通知状态并重新执行结构化提取。"""
    conn = get_connection()
    try:
        notice = get_notice_by_id(conn, notice_id)
        if not notice:
            return {"ok": False, "error": "通知不存在"}
        if not notice.get("raw_content"):
            return {"ok": False, "error": "通知无正文内容"}

        reset_notice_status(conn, notice_id, status="raw")
    finally:
        conn.close()

    # 调用提取服务（会自己开连接）
    return extract_notice(notice_id, auto_index=auto_index)


# ---------- 批量删除 ----------

def batch_delete_by_source(source: str) -> dict:
    """按来源批量删除通知（级联清理向量索引）。"""
    conn = get_connection()
    try:
        ids, count = _delete_notices_by_source(conn, source)

        # 级联清理 Chroma（仅当有实际删除时）
        failed_chunks = []
        if ids:
            index = _get_vector_index()
            for nid in ids:
                try:
                    index.remove_notice(nid)
                except Exception as e:
                    failed_chunks.append((nid, str(e)))

        result = {"ok": True, "deleted_notices": count, "deleted_ids": ids}
        if failed_chunks:
            result["chunk_warnings"] = failed_chunks[:5]
        return result
    except Exception as e:
        logger.exception("按来源批量删除失败: %s", source)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()


def batch_delete_by_status(status: str) -> dict:
    """按状态批量删除通知（级联清理向量索引）。"""
    conn = get_connection()
    try:
        ids, count = _delete_notices_by_status(conn, status)

        failed_chunks = []
        if ids:
            index = _get_vector_index()
            for nid in ids:
                try:
                    index.remove_notice(nid)
                except Exception as e:
                    failed_chunks.append((nid, str(e)))

        result = {"ok": True, "deleted_notices": count, "deleted_ids": ids}
        if failed_chunks:
            result["chunk_warnings"] = failed_chunks[:5]
        return result
    except Exception as e:
        logger.exception("按状态批量删除失败: %s", status)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()


# ---------- 索引管理 ----------

def rebuild_index(statuses: Optional[list[str]] = None, dry_run: bool = False) -> dict:
    """全量重建 Chroma 索引。"""
    from services.qa_service import rebuild_index as _rebuild_index

    try:
        return _rebuild_index(statuses=statuses, dry_run=dry_run)
    except Exception as e:
        logger.exception("重建索引失败")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def get_index_stats() -> dict:
    """获取向量索引统计。"""
    from services.qa_service import get_index_stats as _get_index_stats

    return _get_index_stats()


# ---------- 待办增强删除 ----------

def delete_todo(todo_id: int) -> dict:
    """删除单条待办（级联删除其提醒，模块 3.2）。"""
    conn = get_connection()
    try:
        delete_reminders_for_todo(conn, todo_id)
        cur = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        conn.commit()
        return {"ok": cur.rowcount > 0, "deleted": cur.rowcount}
    except Exception as e:
        logger.exception("删除待办失败 todo_id=%s", todo_id)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()
