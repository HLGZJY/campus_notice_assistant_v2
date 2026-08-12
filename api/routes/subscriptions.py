"""订阅模块：CRUD + 两步式 preview/confirm 语义（盘点 §5.6 订阅映射表）。

两步式平移（原 ui/two_step.py 的 request_action / render_confirmation）：
  - 第一步 `POST /subscriptions/preview`：只读预览影响面（会命中 N 条通知 + 样例标题），不写库；
  - 第二步 `POST /subscriptions` / `PUT /subscriptions/{id}` / …：前端确认后才写库。
  - 前端以确认弹窗 + 路由状态替代 Streamlit 的 session_state。

注意：`POST /notices/match-map`、`GET /notices/matched-ids`、`GET /notices/count`
须先于 notices 路由的 `GET /notices/{notice_id}` 注册（否则会被通配段捕获），
见 api/main.py 的 include 顺序注释。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import require_auth
from api.schemas import (
    BackfillResult,
    MatchMapRequest,
    MatchMapResult,
    SubscriptionCreateRequest,
    SubscriptionItem,
    SubscriptionMutationResult,
    SubscriptionPreview,
    SubscriptionPreviewRequest,
    SubscriptionStats,
    SubscriptionToggleRequest,
    SubscriptionUpdateRequest,
)
from services.subscription_service import (
    add_subscription,
    count_all_notices,
    delete_subscription_record,
    get_matched_notice_ids_set,
    get_match_map,
    get_subscription_stats_ui,
    get_subscriptions_for_ui,
    match_all_notices,
    preview_subscription_matches,
    toggle_subscription,
    update_subscription_record,
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


@router.post("", response_model=SubscriptionMutationResult)
def create_subscription(
    body: SubscriptionCreateRequest,
) -> SubscriptionMutationResult:
    """两步式第二步：确认后新增订阅并全库回填（同步执行，阶段 4 迁入任务模型）。"""
    result = add_subscription(
        keyword=body.keyword,
        notice_type=body.notice_type,
        enabled=body.enabled,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "新增订阅失败"))
    return SubscriptionMutationResult(**result)


@router.put("/{subscription_id}", response_model=SubscriptionMutationResult)
def update_subscription(
    subscription_id: int, body: SubscriptionUpdateRequest
) -> SubscriptionMutationResult:
    """更新订阅并重算命中关系。

    _UNSET 语义：请求体缺失字段 = 不修改；notice_type 显式传 null = 清空类型过滤。
    """
    kwargs: dict = {}
    if "keyword" in body.model_fields_set:
        kwargs["keyword"] = body.keyword
    if "notice_type" in body.model_fields_set:
        kwargs["notice_type"] = body.notice_type
    if "enabled" in body.model_fields_set:
        kwargs["enabled"] = body.enabled
    result = update_subscription_record(subscription_id, **kwargs)
    if not result.get("ok"):
        if result.get("error") == "订阅不存在":
            raise HTTPException(status_code=404, detail=f"订阅 {subscription_id} 不存在")
        raise HTTPException(status_code=400, detail=result.get("error", "更新订阅失败"))
    return SubscriptionMutationResult(**result)


@router.post("/{subscription_id}/toggle", response_model=SubscriptionMutationResult)
def toggle_subscription_endpoint(
    subscription_id: int, body: SubscriptionToggleRequest
) -> SubscriptionMutationResult:
    """启用/停用订阅：停用清理旧命中，启用后全库回填。"""
    result = toggle_subscription(subscription_id, body.enabled)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=f"订阅 {subscription_id} 不存在")
    return SubscriptionMutationResult(**result)


@router.delete("/{subscription_id}", response_model=SubscriptionMutationResult)
def delete_subscription_endpoint(subscription_id: int) -> SubscriptionMutationResult:
    """删除订阅及其命中关系。"""
    result = delete_subscription_record(subscription_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=f"订阅 {subscription_id} 不存在")
    return SubscriptionMutationResult(**result)


@router.post("/match-all", response_model=BackfillResult)
def match_all() -> BackfillResult:
    """全库重匹配（长耗时，同步；阶段 4 迁入任务模型）。"""
    return BackfillResult(**match_all_notices())


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
