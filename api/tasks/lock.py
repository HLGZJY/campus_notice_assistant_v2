"""任务锁键生成（提交时去重用）。

按 task_type 分派，从 params 中抽取业务维度生成稳定 lock_key。
TaskManager.submit 调 compute_lock_key 后传给 create_task_or_get_existing，
后者按 (type, lock_key) 查询是否已有 queued/running 任务，有则幂等返回。
"""
from __future__ import annotations

import hashlib
from typing import Optional

# 参与 token 反查的 LLM 类任务（routes/tasks.py enrich 时也用）
LLM_TASK_TYPES = frozenset({"extract_batch", "generate_todos", "re_extract_notice"})


def _filter_signature(params: dict) -> str:
    """从批量任务的筛选参数生成稳定签名（用于 batch_delete/batch_reset 去重）。"""
    keys = (
        "status", "source", "notice_type",
        "published_from", "published_to", "published_before",
        "crawled_from", "crawled_to",
    )
    parts = [f"{k}={params[k]}" for k in keys if params.get(k) is not None]
    if not parts:
        return "all"
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def compute_lock_key(task_type: str, params: dict) -> Optional[str]:
    """按任务类型从 params 生成锁键。

    返回 None 表示不参与去重（历史/未知类型）。本项目 11 类任务均有锁键。
    """
    params = params or {}
    notice_id = params.get("notice_id")

    if task_type in ("generate_todos", "re_extract_notice"):
        return f"notice:{notice_id}" if notice_id is not None else None

    if task_type == "crawl_source":
        source_name = params.get("source_name")
        return f"source:{source_name}" if source_name else None

    if task_type == "crawl_all":
        sources = params.get("sources")
        if sources:
            return "crawl_all:" + ",".join(sorted(sources))
        return "crawl_all:default"

    if task_type == "extract_batch":
        notice_ids = params.get("notice_ids")
        if notice_ids:
            return "extract:" + ",".join(sorted(str(i) for i in notice_ids))
        return "extract:raw"

    if task_type == "subscription_add":
        keyword = params.get("keyword", "")
        notice_type = params.get("notice_type") or "all"
        return f"sub_add:{keyword}:{notice_type}"

    if task_type == "subscription_update":
        sub_id = params.get("subscription_id")
        return f"sub_update:{sub_id}" if sub_id is not None else None

    if task_type == "match_all":
        return "match_all:default"

    if task_type == "rebuild_index":
        return "rebuild:default"

    if task_type == "batch_delete":
        return "batch_del:" + _filter_signature(params)

    if task_type == "batch_reset":
        return "batch_reset:" + _filter_signature(params)

    return None
