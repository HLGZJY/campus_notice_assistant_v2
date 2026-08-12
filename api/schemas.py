"""API 响应模型：services 返回 dict，此处声明契约（盘点 §5.7 已核实无 sqlite3.Row 泄漏）。

原则：服务层返回的 dict 直接 `model_validate`，不引入转换器；
`core/qa.py` 的 `QAResult` 是唯一例外，序列化在路由层完成。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, RootModel


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    version: str = "1.0"
    notices: int = 0
    db: str = "ok"


class StatusCounts(BaseModel):
    """通知状态统计（raw/extracted/partial/failed…）。"""

    model_config = ConfigDict(extra="allow")

    raw: int = 0
    extracted: int = 0
    partial: int = 0
    failed: int = 0


class NoticeSummary(BaseModel):
    """通知列表项（浏览页卡片所需字段，避免整条 raw_content 传输）。"""

    id: int
    url: str
    source: str
    title: str
    published_at: Optional[str] = None
    crawled_at: str
    status: str
    notice_type: Optional[str] = None
    deadline: Optional[str] = None
    summary: Optional[str] = None
    keywords: list[str] = []  # 订阅命中词（浏览页徽标用，无则空）


class NoticeDetail(BaseModel):
    """通知详情（含正文与关键日期）。"""

    id: int
    url: str
    source: str
    title: str
    raw_content: Optional[str] = None
    published_at: Optional[str] = None
    crawled_at: str
    status: str
    notice_type: Optional[str] = None
    target_audience: Optional[str] = None
    signup_method: Optional[str] = None
    signup_url: Optional[str] = None
    location: Optional[str] = None
    location_type: Optional[str] = None
    deadline: Optional[str] = None
    deadline_raw: Optional[str] = None
    key_dates: list[dict] = []
    summary: Optional[str] = None
    extracted_at: Optional[str] = None
    keywords: list[str] = []


# ---------- 待办（阶段 2，盘点 §5.6 待办映射表） ----------


class TodoItem(BaseModel):
    """待办列表项（含关联通知标题）。"""

    id: int
    notice_id: int
    notice_title: Optional[str] = None
    action: str
    due_at: Optional[str] = None
    priority: str = "normal"
    status: str = "pending"
    created_at: str
    completed_at: Optional[str] = None


class TodoStats(BaseModel):
    """待办状态统计。"""

    pending: int = 0
    done: int = 0
    skipped: int = 0
    total: int = 0


class TodoStatusUpdate(BaseModel):
    """待办状态变更请求体（pending / done / skipped）。"""

    status: str


class TodoGenerateResult(BaseModel):
    """待办生成结果（生成即落库，items 带主键）。"""

    success: bool
    status: str
    items: list[TodoItem] = Field(default_factory=list)
    error: Optional[str] = None


# ---------- 提醒（阶段 2，盘点 §5.6 提醒映射表） ----------


class ReminderItem(BaseModel):
    """提醒列表项（含 tier_label / is_today 增强字段）。"""

    id: int
    notice_id: int
    todo_id: Optional[int] = None
    due_at: str
    tier: str
    remind_on: str
    status: str = "pending"
    created_at: str
    read_at: Optional[str] = None
    notice_title: Optional[str] = None
    notice_source: Optional[str] = None
    todo_action: Optional[str] = None
    tier_label: str = ""
    is_today: bool = False


class ReminderStats(BaseModel):
    """提醒状态统计。"""

    pending: int = 0
    read: int = 0
    ignored: int = 0
    total: int = 0


class ReminderStatusUpdate(BaseModel):
    """提醒状态变更请求体（pending / read / ignored）。"""

    status: str


# ---------- 订阅（阶段 2，盘点 §5.6 订阅映射表） ----------


class SubscriptionItem(BaseModel):
    """订阅列表项（含 match_count / type_label）。"""

    id: int
    keyword: str
    notice_type: Optional[str] = None
    enabled: int = 1
    created_at: str
    match_count: int = 0
    type_label: str = ""


class SubscriptionStats(BaseModel):
    """订阅统计。"""

    total: int = 0
    enabled: int = 0
    matches: int = 0


class SubscriptionPreview(BaseModel):
    """两步式第一步：订阅命中影响面（只读预览）。"""

    matched: int = 0
    total: int = 0
    samples: list[str] = Field(default_factory=list)


class SubscriptionPreviewRequest(BaseModel):
    """preview 请求体。"""

    keyword: str
    notice_type: Optional[str] = None
    enabled: bool = True
    sample_limit: int = Field(default=5, ge=0, le=50)


class SubscriptionCreateRequest(BaseModel):
    """新增订阅请求体（两步式第二步确认后调用）。"""

    keyword: str
    notice_type: Optional[str] = None
    enabled: bool = True


class SubscriptionUpdateRequest(BaseModel):
    """更新订阅请求体：缺失字段 = 不修改；notice_type 显式 null = 清空类型。"""

    keyword: Optional[str] = None
    notice_type: Optional[str] = None
    enabled: Optional[bool] = None


class SubscriptionToggleRequest(BaseModel):
    """启用/停用订阅请求体。"""

    enabled: bool


class MatchMapRequest(BaseModel):
    """批量查询命中订阅词请求体。"""

    notice_ids: list[int]


class BackfillResult(BaseModel):
    """全库回填 / 重匹配结果。"""

    ok: bool
    notices: int = 0
    matched_notices: int = 0
    total_matches: int = 0


class SubscriptionMutationResult(BaseModel):
    """订阅写操作结果（新增/更新/启停/删除共用）。"""

    ok: bool
    error: Optional[str] = None
    id: Optional[int] = None
    keyword: Optional[str] = None
    notice_type: Optional[str] = None
    enabled: Optional[bool] = None
    backfill: Optional[dict] = None
    deleted: Optional[int] = None


class MatchMapResult(RootModel):
    """通知 ID → 命中订阅词列表（JSON 对象键自动字符串化）。"""

    root: dict[int, list[str]]
