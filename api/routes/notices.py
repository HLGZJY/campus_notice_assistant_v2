"""通知只读模块：列表 / 详情 / 统计 / 来源 / 类型（盘点 §5.6 映射表）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import require_auth
from api.schemas import NoticeDetail, NoticeSummary, StatusCounts
from services.notice_service import (
    get_notice_detail,
    get_notices,
    get_notice_types,
    get_sources,
    get_status_counts,
)

router = APIRouter(
    prefix="/notices",
    tags=["notices"],
    dependencies=[Depends(require_auth)],
)


@router.get("/status-counts", response_model=StatusCounts)
def status_counts() -> StatusCounts:
    """按状态统计通知数量。"""
    return StatusCounts(**get_status_counts())


@router.get("", response_model=list[NoticeSummary])
def list_notices(
    status: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    notice_type: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    is_action: Optional[bool] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[NoticeSummary]:
    """多条件查询通知列表（过滤参数直接透传 services，盘点 §5.2-8）。"""
    rows = get_notices(
        status=status,
        source=source,
        notice_type=notice_type,
        keyword=keyword,
        is_action=is_action,
        limit=limit,
    )
    return [
        NoticeSummary(
            **{k: (r.get(k) or []) if k == "keywords" else r.get(k) for k in NoticeSummary.model_fields}
        )
        for r in rows
    ]


@router.get("/sources", response_model=list[str])
def list_sources() -> list[str]:
    """全部数据源名称。"""
    return get_sources()


@router.get("/types", response_model=list[str])
def list_types() -> list[str]:
    """全部通知类型。"""
    return get_notice_types()


@router.get("/{notice_id}", response_model=NoticeDetail)
def notice_detail(notice_id: int) -> NoticeDetail:
    """通知详情（含正文与解析后的关键日期）。"""
    row = get_notice_detail(notice_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"通知 {notice_id} 不存在")
    data = {k: row.get(k) for k in NoticeDetail.model_fields}
    data["key_dates"] = row.get("key_dates") or []
    data["keywords"] = data.get("keywords") or []
    return NoticeDetail(**data)
