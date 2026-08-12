"""token 计量相关服务：封装 token_usage 表的查询（配置页展示用）。"""
from __future__ import annotations

from storage.db import get_connection, get_token_usage_summary as _get_token_usage_summary

TASK_LABELS = {
    "extraction": "结构化提取",
    "qa": "智能问答",
    "todo": "待办生成",
    "embedding": "Embedding",
}


def get_token_usage_summary(days: int = 7) -> dict:
    """近 N 天 token 计量汇总（按任务 × 模型分组 + 总计）。"""
    conn = get_connection()
    try:
        return _get_token_usage_summary(conn, days=days)
    finally:
        conn.close()
