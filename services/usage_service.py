"""token 计量相关服务：封装 token_usage 表的查询（配置页展示用）。"""
from __future__ import annotations

from typing import Optional

from storage.db import (
    get_connection,
    get_token_usage_by_notice_ids,
    get_token_usage_summary as _get_token_usage_summary,
)

TASK_LABELS = {
    "extraction": "结构化提取",
    "qa": "智能问答",
    "todo": "待办生成",
    "embedding": "Embedding",
    "test": "连通性测试",
}

# 参与 token 反查的 LLM 类任务（与 api/tasks/lock.py LLM_TASK_TYPES 对齐）
_LLM_TASK_TYPES = frozenset({"extract_batch", "generate_todos", "re_extract_notice"})


def get_token_usage_summary(days: int = 7) -> dict:
    """近 N 天 token 计量汇总（按任务 × 供应商 × 模型分组 + 总计）。

    返回行的 task_label 为中文标签单一事实源（前端不重复维护）。
    """
    conn = get_connection()
    try:
        summary = _get_token_usage_summary(conn, days=days)
    finally:
        conn.close()
    for row in summary["rows"]:
        row["task_label"] = TASK_LABELS.get(row["task"], row["task"])
    return summary


def _extract_notice_ids(task: dict) -> Optional[list[int]]:
    """按任务类型从 result/params 中提取关联的 notice_id 列表。"""
    task_type = task.get("type")
    params = task.get("params") or {}
    result = task.get("result") or {}

    if task_type in ("generate_todos", "re_extract_notice"):
        nid = params.get("notice_id")
        return [nid] if nid is not None else None

    if task_type == "extract_batch":
        details = (result.get("summary") or {}).get("details") or []
        ids = [d["id"] for d in details if d.get("id") is not None and d.get("status") != "skipped"]
        return ids or None

    return None


def get_token_usage_for_task(task: dict) -> Optional[dict]:
    """任务级 token 反查：按 notice_ids + 任务时间窗聚合 token_usage。

    仅对 success 的 LLM 类任务（extract_batch/generate_todos/re_extract_notice）反查；
    其它类型或非 success 状态返回 None。
    """
    if task.get("status") != "success":
        return None
    if task.get("type") not in _LLM_TASK_TYPES:
        return None

    notice_ids = _extract_notice_ids(task)
    if not notice_ids:
        return None

    created_at = task.get("created_at")
    updated_at = task.get("updated_at")
    if not created_at or not updated_at:
        return None

    conn = get_connection()
    try:
        return get_token_usage_by_notice_ids(conn, notice_ids, created_at, updated_at)
    finally:
        conn.close()