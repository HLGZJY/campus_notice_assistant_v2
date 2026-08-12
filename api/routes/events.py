"""埋点模块（阶段 7）：前端 fire-and-forget 上报 POST /events。

写入逻辑归 tracking_service.track_event（独立短连接、整体 try/except，
绝不上抛、不阻塞主流程）；本路由只做转发，返回 ok 布尔。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import require_auth
from api.schemas import EventCreateRequest, EventCreateResult
from services.tracking_service import track_event

router = APIRouter(
    prefix="/events",
    tags=["events"],
    dependencies=[Depends(require_auth)],
)


@router.post("", response_model=EventCreateResult)
def report_event(body: EventCreateRequest) -> EventCreateResult:
    """上报一条埋点事件（不阻塞：写入失败仅返回 ok=false）。"""
    return EventCreateResult(
        ok=track_event(body.event_type, ref_id=body.ref_id, note=body.note)
    )
