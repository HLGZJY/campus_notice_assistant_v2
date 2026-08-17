"""token 计量相关服务：封装 token_usage 表的查询（配置页展示用）。"""
from __future__ import annotations

from storage.db import get_connection, get_token_usage_summary as _get_token_usage_summary

TASK_LABELS = {
    "extraction": "结构化提取",
    "qa": "智能问答",
    "todo": "待办生成",
    "embedding": "Embedding",
    "test": "连通性测试",
}


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