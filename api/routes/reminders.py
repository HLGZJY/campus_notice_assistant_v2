"""提醒模块：列表 / 统计 / 待办数 / 已读忽略（盘点 §5.6 提醒映射表）。

scan_reminders 为定时任务（幂等），阶段 6 接入 scheduler，不在本模块暴露。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import require_auth
from api.schemas import ReminderItem, ReminderStats, ReminderStatusUpdate
from services.reminder_service import (
    count_pending_reminders,
    get_reminder_stats,
    get_reminders,
    mark_reminder,
)

router = APIRouter(
    prefix="/reminders",
    tags=["reminders"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=list[ReminderItem])
def list_reminders(
    status: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1, le=1000),
) -> list[ReminderItem]:
    """提醒列表（可按状态过滤 / 限制条数，带通知标题与待办动作文案）。"""
    return [ReminderItem(**r) for r in get_reminders(status=status, limit=limit)]


@router.get("/stats", response_model=ReminderStats)
def reminder_stats() -> ReminderStats:
    """提醒状态统计。"""
    return ReminderStats(**get_reminder_stats())


@router.get("/pending-count", response_model=int)
def pending_count() -> int:
    """待处理提醒数（首页红点）。"""
    return count_pending_reminders()


@router.post("/{reminder_id}/status", response_model=dict)
def update_reminder_status(reminder_id: int, body: ReminderStatusUpdate) -> dict:
    """更新提醒状态：pending / read / ignored。read/ignored 记 read_at。"""
    try:
        ok = mark_reminder(reminder_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"提醒 {reminder_id} 不存在")
    return {"ok": ok, "id": reminder_id, "status": body.status}
