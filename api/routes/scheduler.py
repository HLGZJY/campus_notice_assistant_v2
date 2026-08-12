"""调度器状态（阶段 6）：只读端点，供前端展示与验收观察。

调度器由 api/main.py lifespan 拉起（start_scheduler），实例挂在 app.state.scheduler；
`enabled=false` 或 APP_ENV=test 时为 None。本模块只读，不参与配置写入。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request

from api.deps import require_auth
from api.schemas import SchedulerStatus
from storage.db import get_connection, get_recent_scheduler_log

router = APIRouter(
    prefix="/scheduler",
    tags=["scheduler"],
    dependencies=[Depends(require_auth)],
)


@router.get("/status", response_model=SchedulerStatus)
def get_status(request: Request) -> dict:
    """调度器运行状态：enabled/running/jobs/interval + 最近 scheduler_log 记录。"""
    sched: Optional[object] = getattr(request.app.state, "scheduler", None)
    if sched is None:
        return {
            "enabled": False,
            "running": False,
            "interval_minutes": None,
            "jobs": [],
            "recent_runs": [],
        }

    info = sched.get_status()
    conn = get_connection()
    try:
        runs = get_recent_scheduler_log(conn, limit=10)
    finally:
        conn.close()
    return {"enabled": True, **info, "recent_runs": runs}
