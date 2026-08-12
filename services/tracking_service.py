"""事件埋点服务（W4 模块 4.1）。

埋点写入为确定性代码、纯本地落库：track_event 每次自开独立短连接，整体
try/except 兜底——任何异常只记日志返回 False，绝不上抛，不阻塞主流程。
"""
from __future__ import annotations

import logging
from typing import Optional

from storage.db import (
    count_events_by_type as _count_events_by_type,
    get_connection,
    get_event_stats as _get_event_stats,
    get_recent_events as _get_recent_events,
    insert_event as _insert_event,
)

logger = logging.getLogger(__name__)

# 事件类型常量
EVENT_PAGE_VIEW = "page_view"
EVENT_QA_ASK = "qa_ask"
EVENT_TODO_GENERATE = "todo_generate"
EVENT_TODO_DONE = "todo_done"
EVENT_SERVICE_CLICK = "service_button_click"

ALL_EVENT_TYPES = (
    EVENT_PAGE_VIEW,
    EVENT_QA_ASK,
    EVENT_TODO_GENERATE,
    EVENT_TODO_DONE,
    EVENT_SERVICE_CLICK,
)


def track_event(
    event_type: str, ref_id: Optional[int] = None, note: Optional[str] = None
) -> bool:
    """写入一条埋点事件，返回是否成功。

    不阻塞主流程：任何异常（连接失败、写库失败等）都被捕获，仅记日志并返回
    False，不影响调用方业务逻辑。
    """
    try:
        conn = get_connection()
        try:
            _insert_event(conn, event_type, ref_id=ref_id, note=note)
        finally:
            conn.close()
        return True
    except Exception:  # noqa: BLE001 —— 埋点绝不打断主流程
        logger.exception("埋点写入失败 event_type=%s ref_id=%s", event_type, ref_id)
        return False


def count_events(event_type: str, days: Optional[int] = None) -> int:
    """按事件类型计数（可选只统计最近 N 天）。"""
    conn = get_connection()
    try:
        return _count_events_by_type(conn, event_type, days=days)
    finally:
        conn.close()


def get_event_stats(days: Optional[int] = None) -> dict:
    """按事件类型统计数量（含 total 合计），可选只统计最近 N 天。"""
    conn = get_connection()
    try:
        return _get_event_stats(conn, days=days)
    finally:
        conn.close()


def get_recent_events(limit: int = 100, event_type: Optional[str] = None) -> list[dict]:
    """查询最近 N 条埋点事件（可选按类型过滤）。"""
    conn = get_connection()
    try:
        return _get_recent_events(conn, limit=limit, event_type=event_type)
    finally:
        conn.close()
