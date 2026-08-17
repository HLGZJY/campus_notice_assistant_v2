"""用量模块：GET /usage/tokens —— token 计量汇总（盘点 §5.6 用量映射表 + 阶段 7 遗留项）。

前端 Token 用量 Tab 依赖此端点；分组为任务 × 供应商 × 模型，中文标签由
usage_service.TASK_LABELS 提供（单一事实源）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import require_auth
from api.schemas import TokenUsageSummary
from services.usage_service import get_token_usage_summary

router = APIRouter(
    prefix="/usage",
    tags=["usage"],
    dependencies=[Depends(require_auth)],
)


@router.get("/tokens", response_model=TokenUsageSummary)
def get_tokens(days: int = Query(7, ge=1, le=365)) -> dict:
    """近 N 天 token 用量汇总（按任务 × 供应商 × 模型分组 + 总计）。"""
    return get_token_usage_summary(days=days)
