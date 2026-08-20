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
    extract_skipped_reason: Optional[str] = None  # 提取预筛跳过原因（阶段 7）


class KeyDateItem(BaseModel):
    """一条关键日期（对应 core.models.KeyDate，经 /openapi.json 下发强类型）。"""

    label: str = ""
    date_raw: str = ""
    datetime: Optional[str] = None


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
    key_dates: list[KeyDateItem] = []
    summary: Optional[str] = None
    extracted_at: Optional[str] = None
    keywords: list[str] = []
    extract_skipped_reason: Optional[str] = None  # 提取预筛跳过原因（阶段 7）


# ---------- 通知列表分页 / 元信息 / 管理（新增数据管理能力） ----------


class NoticePage(BaseModel):
    """通知列表分页信封（items + 总数，供分页条）。"""

    items: list[NoticeSummary]
    total: int = 0
    page: int = 1
    page_size: int = 20


class NoticeMetaItem(BaseModel):
    """元信息条目：存储值 + 中文标签。"""

    value: str
    label: str


class NoticeMeta(BaseModel):
    """通知元信息（状态/类型中文标签，翻译单一事实源在 core/models.py）。"""

    statuses: list[NoticeMetaItem] = Field(default_factory=list)
    notice_types: list[NoticeMetaItem] = Field(default_factory=list)
    action_notice_types: list[str] = Field(default_factory=list)


class NoticeBatchFilter(BaseModel):
    """通知筛选条件（列表时间筛选 / 批量操作共用）。"""

    status: Optional[str] = None
    source: Optional[str] = None
    notice_type: Optional[str] = None
    published_from: Optional[str] = None
    published_to: Optional[str] = None
    published_before: Optional[str] = None
    crawled_from: Optional[str] = None
    crawled_to: Optional[str] = None


class NoticeBatchRequest(NoticeBatchFilter):
    """批量重置请求体：筛选条件 + 重置目标状态。"""

    target_status: str = "raw"


class NoticeBatchResult(BaseModel):
    """批量操作结果（删除/重置共用，按操作填充对应字段）。"""

    model_config = ConfigDict(extra="allow")

    ok: bool
    error: Optional[str] = None
    deleted_notices: int = 0
    deleted_ids: list[int] = Field(default_factory=list)
    reset_notices: int = 0
    reset_ids: list[int] = Field(default_factory=list)
    chunk_warnings: Optional[list] = None


class ExtractPreviewItem(BaseModel):
    """提取前预览明细项（passed 无 reason；skipped 带跳过原因）。"""

    id: int
    title: str = ""
    url: str = ""
    source: str = ""
    published_at: Optional[str] = None
    status: str = ""
    reason: Optional[str] = None


class ExtractPreviewResponse(BaseModel):
    """提取前预览结果：将提取 / 跳过明细。"""

    passed: list[ExtractPreviewItem] = Field(default_factory=list)
    skipped: list[ExtractPreviewItem] = Field(default_factory=list)


class NoticeResetRequest(BaseModel):
    """单条重置状态请求体。"""

    status: str = "raw"


class NoticeMutationResult(BaseModel):
    """单条管理操作结果（删除/重置共用）。"""

    ok: bool
    error: Optional[str] = None
    id: Optional[int] = None
    deleted_notices: int = 0


# ---------- 待办（阶段 2，盘点 §5.6 待办映射表） ----------


class TodoItem(BaseModel):
    """待办列表项（含关联通知标题与原文链接）。"""

    id: int
    notice_id: int
    notice_title: Optional[str] = None
    notice_url: Optional[str] = None
    action: str
    due_at: Optional[str] = None
    priority: str = "normal"
    status: str = "pending"
    notes: Optional[str] = None
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


class TodoUpdateRequest(BaseModel):
    """待办更新请求体（PATCH）：缺失字段 = 不修改；显式 null = 清空。"""

    action: Optional[str] = None
    due_at: Optional[str] = None
    notes: Optional[str] = None


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
    total_notices: int = 0


class SubscriptionPreview(BaseModel):
    """两步式第一步：订阅命中影响面（只读预览）。"""

    matched: int = 0
    total: int = 0
    samples: list[str] = Field(default_factory=list)
    sample_ids: list[int] = Field(default_factory=list)


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


# ---------- 配置（阶段 3，盘点 §5.6 配置映射表） ----------
#
# 约定：
#   - GET 返回 service 的 dict，此处声明响应契约（extra="allow" 防字段漂移）；
#   - PUT 请求体直接复用 config/schema.py 的 ModelsConfig / ProviderConfig / SourceConfig，
#     不在此重复定义，避免契约漂移（stage 7 的 openapi-typescript 基于 /openapi.json 生成类型）；
#   - PUT 失败语义保持 config_service 统一结构：HTTP 200 + {"ok": false, "error": ...}
#     （schema 级错误由 FastAPI 自动 422）。


class ProviderView(BaseModel):
    """供应商视图（api_key 以状态标记代替，不泄露）。"""

    model_config = ConfigDict(extra="allow")

    name: str
    display_name: str = ""
    base_url: str = ""
    api_key_env: str = ""
    api_key_status: bool = False
    models: list[str] = []  # 可选模型名列表（纯手动维护）
    type: str = ""  # 提供商类型徽章（local/bailian/opencode-zen/custom）


class ModelProfileView(BaseModel):
    """任务-模型映射视图。"""

    model_config = ConfigDict(extra="allow")

    provider: str
    models: list[str] = []  # 有序候选列表，先尝试在前


class ModelsView(BaseModel):
    """各任务模型配置视图。"""

    model_config = ConfigDict(extra="allow")

    extraction: ModelProfileView
    qa: ModelProfileView
    todo: ModelProfileView
    embedding: ModelProfileView


class ConfigView(BaseModel):
    """完整配置视图（GET /config）。"""

    model_config = ConfigDict(extra="allow")

    active_school: str
    models: ModelsView
    providers: dict[str, ProviderView]
    crawl: dict
    extract: dict = Field(default_factory=dict)


class ConfigMutationResult(BaseModel):
    """配置写操作结果（models/providers/sources PUT 共用）。"""

    model_config = ConfigDict(extra="allow")

    ok: bool
    error: Optional[str] = None
    changed: Optional[bool] = None
    version: Optional[int] = None
    message: Optional[str] = None
    path: Optional[str] = None


class ReloadResult(BaseModel):
    """强制重载结果。"""

    model_config = ConfigDict(extra="allow")

    ok: bool
    version: Optional[int] = None
    error: Optional[str] = None


class DiskInfo(BaseModel):
    """配置文件磁盘信息。"""

    model_config = ConfigDict(extra="allow")

    path: str
    exists: bool
    last_modified: Optional[str] = None


class TestSourceRequest(BaseModel):
    """测试数据源 URL 请求体。"""

    url: str
    timeout: int = Field(default=15, ge=1, le=120)


class TestSourceResult(BaseModel):
    """数据源 URL 测试结果。"""

    model_config = ConfigDict(extra="allow")

    ok: bool
    status_code: Optional[int] = None
    latency_ms: int = 0
    link_count: int = 0
    error: Optional[str] = None
    # 阶段 7：自动发现的链接模式建议（前端一键填入 url_pattern）
    suggested_pattern: Optional[str] = None
    sample_links: list[str] = Field(default_factory=list)


class TestModelRequest(BaseModel):
    """测试模型连接请求体。"""

    provider: str
    model: str
    timeout: int = Field(default=30, ge=1, le=300)


class TestModelResult(BaseModel):
    """模型连接测试结果。"""

    model_config = ConfigDict(extra="allow")

    ok: bool
    latency_ms: int = 0
    completion: Optional[str] = None
    error: Optional[str] = None


class ApiKeyRequest(BaseModel):
    """供应商 API Key 写入请求体（后端 upsert 到 .env，不入库不落 YAML）。"""

    api_key: str


class ApiKeyResult(BaseModel):
    """API Key 写入结果。"""

    model_config = ConfigDict(extra="allow")

    ok: bool
    env_var: Optional[str] = None
    env_path: Optional[str] = None
    error: Optional[str] = None


# ---------- 异步任务（阶段 4） ----------


class TaskCreateRequest(BaseModel):
    """任务提交请求体。type 见 api/tasks/workers.py 的 WORKERS 注册表。"""

    type: str
    params: dict = Field(default_factory=dict)


class TaskCreateResult(BaseModel):
    """任务提交结果（202）：前端据此轮询 GET /tasks/{id}。"""

    task_id: int
    type: str
    status: str = "queued"


class TaskTokenUsage(BaseModel):
    """任务级 token 反查聚合（由 result.notice_ids + 时间窗反查 token_usage 表）。"""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    providers: list[str] = []


class TaskView(BaseModel):
    """任务查询视图（轮询点）。"""

    id: int
    type: str
    params: Optional[dict] = None
    status: str  # queued / running / success / failed
    progress: float = 0
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str
    lock_key: Optional[str] = None
    token_usage: Optional[TaskTokenUsage] = None


# ---------- 问答（阶段 5，盘点 §5.7 唯一例外） ----------
#
# 约定：SSE 事件负载由 api/routes/qa.py 手动序列化（QAResult 是 services 返回
# dict 约定的唯一例外，路由层做 as_source 转换）。以下模型仅用于 /openapi.json
# 契约文档与 index-stats 响应，不参与流式事件输出。


class QaSourceRef(BaseModel):
    """回答引用的来源通知（as_source 转换后的契约形态）。"""

    notice_id: int
    title: str = ""
    url: str = ""
    notice_type: str = ""
    deadline: Optional[str] = None


class QaResultView(BaseModel):
    """问答完整结果（SSE done 事件负载的文档形态）。"""

    answer: str
    sources: list[QaSourceRef] = Field(default_factory=list)
    retrieved_chunks: int = 0


class IndexStatsView(BaseModel):
    """向量索引统计（问答页索引状态角标）。"""

    model_config = ConfigDict(extra="allow")

    chunks: int = 0
    persist_dir: str = ""
    error: Optional[str] = None


class QaHistoryItem(BaseModel):
    """问答历史条目（GET /qa/history 返回；sources 为 as_source 契约形态）。

    status 取值：answer（正常回答）/ cache_hit（缓存命中）/ fallback（兜底）/ error（失败）。
    """

    id: int
    question_text: str
    answer_text: str
    sources: list[QaSourceRef] = Field(default_factory=list)
    retrieved_chunks: int = 0
    created_at: str
    hit_count: int = 0
    status: str = "answer"


class QaHistoryPage(BaseModel):
    """问答历史分页信封（items + 总数，供分页条）。"""

    items: list[QaHistoryItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


# ---------- 调度器（阶段 6） ----------


class SchedulerJobView(BaseModel):
    """已注册的调度 job（只读状态用）。"""

    id: str
    name: Optional[str] = None
    next_run_time: Optional[str] = None


class SchedulerStatus(BaseModel):
    """调度器状态（GET /scheduler/status）。"""

    model_config = ConfigDict(extra="allow")

    enabled: bool
    running: bool = False
    interval_minutes: Optional[int] = None
    jobs: list[SchedulerJobView] = Field(default_factory=list)
    recent_runs: list[dict] = Field(default_factory=list)


# ---------- 用量（盘点 §5.6 用量映射表 + 阶段 7 遗留项） ----------
#
# 约定：GET /usage/tokens 返回 usage_service.get_token_usage_summary 的 dict，
# 分组为任务 × 供应商 × 模型；task_label 为中文标签单一事实源（services/usage_service.py）。


class TokenUsageRow(BaseModel):
    """token 计量分组行（按 task × provider × model）。"""

    task: str
    provider: str = ""
    model: str = ""
    calls: int = 0
    success: int = 0
    failed: int = 0
    retry_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    task_label: str = ""


class TokenUsageSummary(BaseModel):
    """token 用量汇总：近 N 天分组明细 + 总计。"""

    days: int = 7
    rows: list[TokenUsageRow] = Field(default_factory=list)
    total: dict = Field(default_factory=dict)


# ---------- 埋点（阶段 7，前端 fire-and-forget 上报） ----------
#
# 约定：写入逻辑归 tracking_service.track_event（独立短连接、整体 try/except，
# 绝不上抛、不阻塞主流程）；路由只做转发，返回 ok 布尔。


class EventCreateRequest(BaseModel):
    """埋点事件上报请求体。"""

    event_type: str
    ref_id: Optional[int] = None
    note: Optional[str] = None


class EventCreateResult(BaseModel):
    """埋点上报结果（写入失败仅 ok=false，不报错）。"""

    ok: bool


# ---------- 数据源中心（阶段 8：公共数据源目录） ----------
#
# 约定：目录数据静态存于 config/source_catalog.yaml；GET /source-center 返回
# 分类树 + 条目（含 adopted 状态，按 list_url 与个人数据源判重联动）；
# 选用/移除写入口与 config_service.update_sources 同一路径（个人数据源 YAML）。


class SourceCenterNode(BaseModel):
    """数据源中心左侧分类树节点（一级分组 → 二级组织，可折叠）。"""

    key: str
    label: str
    count: int = 0
    children: list["SourceCenterNode"] = Field(default_factory=list)


class SourceCenterItem(BaseModel):
    """数据源中心目录条目。"""

    id: str
    name: str
    org: str = ""
    org_group: str = ""
    list_url: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    updated_at: str = ""
    adopted: bool = False


class SourceCenterOverview(BaseModel):
    """数据源中心总览：学校信息 + 分类树 + 目录条目。"""

    school: str = ""
    school_code: str = ""
    tree: list[SourceCenterNode] = Field(default_factory=list)
    items: list[SourceCenterItem] = Field(default_factory=list)
    adopted_count: int = 0


class SourceCenterAdoptRequest(BaseModel):
    """选用数据源时可选的自定义抓取参数（缺省 = 使用默认参数）。

    与「系统配置-数据源」页的 SourceConfig 字段一致，选用即写入个人数据源并生效。
    """

    url_pattern: Optional[str] = None
    max_pages: Optional[int] = None
    max_age_days: Optional[int] = None
    enabled: Optional[bool] = None
    crawl_mode: Optional[str] = None
    fetch_detail: Optional[bool] = None
    deep_check: Optional[bool] = None


class SourceCenterAdoptResult(BaseModel):
    """选用/移除结果（already=目录条目已存在于个人数据源的幂等返回）。"""

    ok: bool
    source_id: str = ""
    adopted: bool = False
    already: bool = False
    error: Optional[str] = None


class SourceCenterPreviewItem(BaseModel):
    """预览样例条目（列表页解析出的标题/链接/日期）。"""

    title: str
    url: str
    date: Optional[str] = None


class SourceCenterPreview(BaseModel):
    """预览结果：抓取列表页返回样例数据（只读，不落库）。"""

    ok: bool
    source_id: str = ""
    list_url: str = ""
    items: list[SourceCenterPreviewItem] = Field(default_factory=list)
    error: Optional[str] = None


class SourceCenterPreviewByUrlRequest(BaseModel):
    """按 URL 预览请求（「我的数据源」卡片点击链接预览用，不要求 URL 在公共目录中）。"""

    url: str
    limit: int = Field(default=10, ge=1, le=30)


class SourceCenterPreviewByUrlResult(BaseModel):
    """按 URL 预览结果。"""

    ok: bool
    url: str = ""
    items: list[SourceCenterPreviewItem] = Field(default_factory=list)
    error: Optional[str] = None
    latency_ms: int = 0
