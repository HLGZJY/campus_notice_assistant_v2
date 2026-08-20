"""数据源中心：公共目录浏览 / 选用 / 移除 / 预览（阶段 8）。

- GET  /source-center              总览：分类树 + 目录条目（含 adopted 状态）
- GET  /source-center/{id}/preview  样例数据（抓取列表页前 N 条标题，只读）
- POST /source-center/{id}/adopt    选用 → 追加到个人数据源（list_url 判重幂等）
- POST /source-center/{id}/remove   移除 → 从个人数据源按 list_url 删除（幂等）

联动：选用/移除写入口与「系统配置-数据源」页同一路径（ConfigStore.save_sources），
两页读写同一份个人数据源 YAML，自动双向同步；本模块不做长耗时操作，无需任务化。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from api.deps import require_auth
from api.schemas import (
    SourceCenterAdoptRequest,
    SourceCenterAdoptResult,
    SourceCenterOverview,
    SourceCenterPreview,
    SourceCenterPreviewByUrlRequest,
    SourceCenterPreviewByUrlResult,
)
from services import source_center_service

router = APIRouter(
    prefix="/source-center",
    tags=["source-center"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=SourceCenterOverview)
def source_center_overview() -> SourceCenterOverview:
    """数据源中心总览：学校信息 + 分类树 + 目录条目（adopted 按 list_url 联动）。"""
    return SourceCenterOverview(**source_center_service.get_overview())


@router.post("/preview-url", response_model=SourceCenterPreviewByUrlResult)
def source_center_preview_by_url(body: SourceCenterPreviewByUrlRequest) -> SourceCenterPreviewByUrlResult:
    """按 URL 预览样例数据（「我的数据源」卡片点击链接预览用，不要求 URL 在公共目录中）。

    网络不可达/解析失败返回 ok=false + error（HTTP 200，前端展示降级信息）。
    """
    result = source_center_service.preview_url(body.url, limit=body.limit)
    return SourceCenterPreviewByUrlResult(**result)


@router.get("/{source_id}/preview", response_model=SourceCenterPreview)
def source_center_preview(
    source_id: str,
    limit: int = Query(default=10, ge=1, le=30),
) -> SourceCenterPreview:
    """预览样例数据：抓取该来源列表页，返回前 N 条标题/链接/日期（不落库）。

    网络不可达/解析失败返回 ok=false + error（HTTP 200，前端展示降级信息）。
    """
    result = source_center_service.preview_source(source_id, limit=limit)
    if not result.get("ok") and "数据源不存在" in (result.get("error") or ""):
        raise HTTPException(status_code=404, detail=result["error"])
    return SourceCenterPreview(**result)


@router.post("/{source_id}/adopt", response_model=SourceCenterAdoptResult)
def source_center_adopt(
    source_id: str,
    body: SourceCenterAdoptRequest | None = Body(default=None),
) -> SourceCenterAdoptResult:
    """选用数据源：追加到个人数据源（按 list_url 判重，重复选用幂等返回）。

    body 可选：携带抓取参数（url_pattern / max_pages / max_age_days 等）时，
    选用即按该参数写入并立即生效，无需再到「系统配置-数据源」页重复保存。
    """
    overrides = body.model_dump(exclude_none=True) if body else None
    result = source_center_service.adopt_source(source_id, overrides=overrides)
    if not result.get("ok"):
        raise HTTPException(status_code=404 if "不存在" in (result.get("error") or "") else 400, detail=result.get("error"))
    return SourceCenterAdoptResult(**result)


@router.post("/{source_id}/remove", response_model=SourceCenterAdoptResult)
def source_center_remove(source_id: str) -> SourceCenterAdoptResult:
    """移除数据源：按 list_url 从个人数据源删除（未选用也幂等成功）。"""
    result = source_center_service.remove_source(source_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404 if "不存在" in (result.get("error") or "") else 400, detail=result.get("error"))
    return SourceCenterAdoptResult(**result)
