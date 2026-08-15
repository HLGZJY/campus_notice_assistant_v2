"""订阅模块：CRUD + 两步式 preview/confirm 语义（盘点 §5.6 订阅映射表）。

两步式平移（原 ui/two_step.py 的 request_action / render_confirmation）：
  - 第一步 `POST /subscriptions/preview`：只读预览影响面（会命中 N 条通知 + 样例标题），不写库；
  - 第二步 `POST /subscriptions` / `PUT /subscriptions/{id}` / …：前端确认后写库。
  - 前端以确认弹窗 + 路由状态替代 Streamlit 的 session_state。

阶段 4（异步任务化）：
  - 长耗时写操作（新增/编辑回填、全库重匹配）迁入任务模型：路由同步校验
    （400/404 立即返回），通过后提交任务返回 202 {task_id}，前端轮询
    GET /tasks/{id} 获取 backfill 结果。任务类型见 api/tasks/workers.py。

注意：`POST /notices/match-map`、`GET /notices/matched-ids`、`GET /notices/count`
须先于 notices 路由的 `GET /notices/{notice_id}` 注册（否则会被通配段捕获），
见 api/main.py 的 include 顺序注释。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import require_auth
from api.routes.tasks import get_task_manager
from api.schemas import (
    MatchMapRequest,
    MatchMapResult,
    NoticePage,
    NoticeSummary,
    SubscriptionCreateRequest,
    SubscriptionItem,
    SubscriptionMutationResult,
    SubscriptionPreview,
    SubscriptionPreviewRequest,
    SubscriptionStats,
    SubscriptionToggleRequest,
    SubscriptionUpdateRequest,
    TaskCreateResult,
)
from services.subscription_service import (
    count_all_notices,
    delete_subscription_record,
    get_matched_notice_ids_set,
    get_matched_notices_for_subscription,
    get_match_map,
    get_subscription_record,
    get_subscription_stats_ui,
    get_subscriptions_for_ui,
    preview_subscription_matches,
    validate_subscription_input,
)

router = APIRouter(
    prefix="/subscriptions",
    tags=["subscriptions"],
    dependencies=[Depends(require_auth)],
)

# 浏览页复用：/notices 下的只读查询（命中筛选 / 影响面预览）
notice_router = APIRouter(
    prefix="/notices",
    tags=["subscriptions"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=list[SubscriptionItem])
def list_subscriptions() -> list[SubscriptionItem]:
    """订阅列表（含各订阅命中数 / 类型标签）。"""
    return [SubscriptionItem(**s) for s in get_subscriptions_for_ui()]


@router.get("/stats", response_model=SubscriptionStats)
def subscription_stats() -> SubscriptionStats:
    """订阅统计：总数 / 启用数 / 命中总数。"""
    return SubscriptionStats(**get_subscription_stats_ui())


@router.get("/{subscription_id}/notices", response_model=NoticePage)
def subscription_notices(
    subscription_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> NoticePage:
    """某订阅命中的通知列表（分页，供订阅页内联展开验证）。

    订阅不存在返回 404；每条通知带 keywords=[订阅词] 便于列表徽标展示。
    """
    data = get_matched_notices_for_subscription(subscription_id, page, page_size)
    if data is None:
        raise HTTPException(status_code=404, detail=f"订阅 {subscription_id} 不存在")
    sub = get_subscription_record(subscription_id)
    keyword = sub.get("keyword") if sub else None
    items = [
        NoticeSummary(
            **{
                k: (r.get(k) or []) if k == "keywords" else r.get(k)
                for k in NoticeSummary.model_fields
            }
        )
        for r in data["items"]
    ]
    for it in items:
        if keyword:
            it.keywords = [keyword]
    return NoticePage(
        items=items,
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
    )


@router.post("/preview", response_model=SubscriptionPreview)
def preview_subscription(body: SubscriptionPreviewRequest) -> SubscriptionPreview:
    """两步式第一步：只读预览按当前规则该订阅会命中多少条通知（不写库）。"""
    result = preview_subscription_matches(
        keyword=body.keyword,
        notice_type=body.notice_type,
        enabled=body.enabled,
        sample_limit=body.sample_limit,
    )
    return SubscriptionPreview(**result)


@router.post("", status_code=202, response_model=TaskCreateResult)
def create_subscription(
    request: Request, body: SubscriptionCreateRequest
) -> TaskCreateResult:
    """两步式第二步：确认后新增订阅并全库回填（异步任务，202 返回 task_id 供轮询）。

    路由同步校验（400 立即返回，不进入任务队列），通过后提交 subscription_add。
    """
    err = validate_subscription_input(body.keyword, body.notice_type)
    if err:
        raise HTTPException(status_code=400, detail=err)
    task_id = get_task_manager(request).submit(
        "subscription_add",
        {
            "keyword": body.keyword,
            "notice_type": body.notice_type,
            "enabled": body.enabled,
        },
    )
    return TaskCreateResult(task_id=task_id, type="subscription_add", status="queued")


@router.put("/{subscription_id}", status_code=202, response_model=TaskCreateResult)
def update_subscription(
    request: Request, subscription_id: int, body: SubscriptionUpdateRequest
) -> TaskCreateResult:
    """更新订阅并重算命中关系（异步任务，202 返回 task_id 供轮询）。

    _UNSET 语义：请求体缺失字段 = 不修改；notice_type 显式传 null = 清空类型过滤。
    路由同步校验 404/400（订阅不存在、输入非法立即返回），通过后提交 subscription_update。
    """
    if get_subscription_record(subscription_id) is None:
        raise HTTPException(status_code=404, detail=f"订阅 {subscription_id} 不存在")
    params: dict = {"subscription_id": subscription_id}
    if "keyword" in body.model_fields_set:
        params["keyword"] = body.keyword
    if "notice_type" in body.model_fields_set:
        params["notice_type"] = body.notice_type
    if "enabled" in body.model_fields_set:
        params["enabled"] = body.enabled
    if "keyword" in params or "notice_type" in params:
        err = validate_subscription_input(params.get("keyword"), params.get("notice_type"))
        if err:
            raise HTTPException(status_code=400, detail=err)
    task_id = get_task_manager(request).submit("subscription_update", params)
    return TaskCreateResult(task_id=task_id, type="subscription_update", status="queued")


@router.post("/{subscription_id}/toggle", status_code=202, response_model=TaskCreateResult)
def toggle_subscription_endpoint(
    request: Request, subscription_id: int, body: SubscriptionToggleRequest
) -> TaskCreateResult:
    """启用/停用订阅（异步任务，202 返回 task_id 供轮询）。

    停用清理旧命中，启用后全库回填——两者都可能长耗时，统一迁入任务模型。
    路由同步校验 404（订阅不存在立即返回）。
    """
    if get_subscription_record(subscription_id) is None:
        raise HTTPException(status_code=404, detail=f"订阅 {subscription_id} 不存在")
    task_id = get_task_manager(request).submit(
        "subscription_update",
        {"subscription_id": subscription_id, "enabled": body.enabled},
    )
    return TaskCreateResult(task_id=task_id, type="subscription_update", status="queued")


@router.delete("/{subscription_id}", response_model=SubscriptionMutationResult)
def delete_subscription_endpoint(subscription_id: int) -> SubscriptionMutationResult:
    """删除订阅及其命中关系。"""
    result = delete_subscription_record(subscription_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=f"订阅 {subscription_id} 不存在")
    return SubscriptionMutationResult(**result)


@router.post("/match-all", status_code=202, response_model=TaskCreateResult)
def match_all(request: Request) -> TaskCreateResult:
    """全库重匹配（异步任务，202 返回 task_id 供轮询）。"""
    task_id = get_task_manager(request).submit("match_all", {})
    return TaskCreateResult(task_id=task_id, type="match_all", status="queued")


# ---------- /notices 下的只读查询（浏览页） ----------


@notice_router.get("/count", response_model=int)
def notices_count() -> int:
    """通知总数（重匹配影响面预览用）。"""
    return count_all_notices()


@notice_router.get("/matched-ids", response_model=list[int])
def matched_notice_ids() -> list[int]:
    """全部有命中关系的通知 ID（浏览页命中筛选开关）。"""
    return sorted(get_matched_notice_ids_set())


@notice_router.post("/match-map", response_model=MatchMapResult)
def notice_match_map(body: MatchMapRequest) -> MatchMapResult:
    """批量查询通知命中的订阅词，返回 {notice_id: [keyword, ...]}。"""
    return MatchMapResult(root=get_match_map(body.notice_ids))
