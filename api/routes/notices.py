"""通知模块：只读（列表分页/详情/统计/来源/类型/元信息）+ 数据管理（删除/重置/重提取/批量）。

管理端点复用 services/admin_service.py（级联删除待办、提醒、订阅命中与 Chroma 向量）。
批量删除/重置、单条重新提取为长耗时/批量操作，按阶段 4 约定走「提交任务 → 202 task_id → 轮询」。

路由顺序约定（Starlette 顺序匹配）：
  /notices/meta、/notices/batch-delete、/notices/batch-reset 等精确路径
  必须先于 /notices/{notice_id} 注册，否则会被通配段捕获而 422。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import require_auth
from api.routes.tasks import get_task_manager
from api.schemas import (
    ExtractPreviewItem,
    ExtractPreviewResponse,
    NoticeBatchFilter,
    NoticeBatchRequest,
    NoticeDetail,
    NoticeMeta,
    NoticeMutationResult,
    NoticePage,
    NoticeResetRequest,
    NoticeSummary,
    StatusCounts,
    TaskCreateResult,
)
from services import admin_service
from services.notice_service import (
    extract_preview,
    get_notice_detail,
    get_notice_meta,
    get_notices,
    get_notice_types,
    get_sources,
    get_status_counts,
)
from storage.db import get_connection, get_notice_by_id, reset_notice_status

router = APIRouter(
    prefix="/notices",
    tags=["notices"],
    dependencies=[Depends(require_auth)],
)


@router.get("/status-counts", response_model=StatusCounts)
def status_counts() -> StatusCounts:
    """按状态统计通知数量。"""
    return StatusCounts(**get_status_counts())


@router.get("/meta", response_model=NoticeMeta)
def notice_meta() -> NoticeMeta:
    """状态/类型中文标签映射（筛选下拉与标签渲染的翻译单一事实源）。"""
    return NoticeMeta(**get_notice_meta())


@router.get("/sources", response_model=list[str])
def list_sources() -> list[str]:
    """全部数据源名称。"""
    return get_sources()


@router.get("/types", response_model=list[str])
def list_types() -> list[str]:
    """全部通知类型（存储值，展示用 /notices/meta 的 label）。"""
    return get_notice_types()


@router.get("", response_model=NoticePage)
def list_notices(
    status: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    notice_type: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    is_action: Optional[bool] = Query(default=None),
    published_from: Optional[str] = Query(default=None),
    published_to: Optional[str] = Query(default=None),
    published_before: Optional[str] = Query(default=None),
    crawled_from: Optional[str] = Query(default=None),
    crawled_to: Optional[str] = Query(default=None),
    sort_by: str = Query(default="published", pattern="^(published|crawled)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> NoticePage:
    """多条件分页查询通知列表（含时间范围筛选，返回分页信封；sort_by 控制排序字段）。"""
    data = get_notices(
        status=status,
        source=source,
        notice_type=notice_type,
        keyword=keyword,
        is_action=is_action,
        published_from=published_from,
        published_to=published_to,
        published_before=published_before,
        crawled_from=crawled_from,
        crawled_to=crawled_to,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
    )
    items = [
        NoticeSummary(
            **{k: (r.get(k) or []) if k == "keywords" else r.get(k) for k in NoticeSummary.model_fields}
        )
        for r in data["items"]
    ]
    return NoticePage(
        items=items,
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
    )


@router.post("/extract-preview", response_model=ExtractPreviewResponse)
def extract_preview_route() -> ExtractPreviewResponse:
    """提取前预览（dry-run）：展示将提取/跳过明细及原因，供勾选后提交 notice_ids。"""
    result = extract_preview()
    return ExtractPreviewResponse(
        passed=[ExtractPreviewItem(**item) for item in result["passed"]],
        skipped=[ExtractPreviewItem(**item) for item in result["skipped"]],
    )


@router.post("/batch-delete", status_code=202, response_model=TaskCreateResult)
def batch_delete_notices(request: Request, body: NoticeBatchFilter) -> TaskCreateResult:
    """按筛选条件批量删除通知（异步任务：级联清理向量索引）。"""
    params = body.model_dump(exclude_none=True)
    task_id = get_task_manager(request).submit("batch_delete", params)
    return TaskCreateResult(task_id=task_id, type="batch_delete", status="queued")


@router.post("/batch-reset", status_code=202, response_model=TaskCreateResult)
def batch_reset_notices(request: Request, body: NoticeBatchRequest) -> TaskCreateResult:
    """按筛选条件批量重置通知状态（异步任务，供重新提取）。"""
    params = body.model_dump(exclude_none=True)
    task_id = get_task_manager(request).submit("batch_reset", params)
    return TaskCreateResult(task_id=task_id, type="batch_reset", status="queued")


@router.delete("/{notice_id}", response_model=NoticeMutationResult)
def delete_notice(notice_id: int) -> NoticeMutationResult:
    """删除单条通知（级联删除待办/提醒/订阅命中 + Chroma 向量）。"""
    result = admin_service.delete_notice(notice_id)
    if not result.get("ok"):
        if result.get("error") == "通知不存在":
            raise HTTPException(status_code=404, detail=result["error"])
        return NoticeMutationResult(ok=False, error=result.get("error"))
    return NoticeMutationResult(
        ok=True,
        id=notice_id,
        deleted_notices=result.get("deleted_notices", 0),
    )


@router.post("/{notice_id}/reset", response_model=NoticeMutationResult)
def reset_notice(notice_id: int, body: NoticeResetRequest) -> NoticeMutationResult:
    """重置单条通知状态（如 failed→raw，供重新提取）。"""
    conn = get_connection()
    try:
        ok = reset_notice_status(conn, notice_id, body.status)
    finally:
        conn.close()
    if not ok:
        raise HTTPException(status_code=404, detail=f"通知 {notice_id} 不存在")
    return NoticeMutationResult(ok=True, id=notice_id)


@router.post("/{notice_id}/re-extract", status_code=202, response_model=TaskCreateResult)
def re_extract_notice(request: Request, notice_id: int) -> TaskCreateResult:
    """重置并重新提取单条通知（异步任务，LLM 调用在 worker 执行）。"""
    conn = get_connection()
    try:
        notice = get_notice_by_id(conn, notice_id)
    finally:
        conn.close()
    if notice is None:
        raise HTTPException(status_code=404, detail=f"通知 {notice_id} 不存在")
    if not notice.get("raw_content"):
        raise HTTPException(status_code=400, detail="通知无正文内容，无法重新提取")
    task_id = get_task_manager(request).submit(
        "re_extract_notice", {"notice_id": notice_id, "auto_index": True}
    )
    return TaskCreateResult(task_id=task_id, type="re_extract_notice", status="queued")


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