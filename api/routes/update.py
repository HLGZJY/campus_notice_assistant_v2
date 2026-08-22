"""检查更新路由：GET /update/check。

打包发布方案 Step 5 的后端侧：GitHub Releases 检查 + 版本比对。
静默失败契约（不抛 5xx，错误信息收敛在 error 字段）见 update_service。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import require_auth
from api.schemas import UpdateCheckResult
from services.update_service import check_for_update

router = APIRouter(
    prefix="/update",
    tags=["update"],
    dependencies=[Depends(require_auth)],
)


@router.get("/check", response_model=UpdateCheckResult)
def get_update_check() -> dict:
    """检查 GitHub Releases 是否有新版本（静默失败，不影响主功能）。"""
    return check_for_update()
