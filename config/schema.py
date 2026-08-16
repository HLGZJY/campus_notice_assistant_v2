"""配置数据模型（Pydantic）。

定义应用级配置（providers、models、active_school）和学校数据源配置
的数据结构，所有配置加载均先验证 schema。
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class ProviderConfig(BaseModel):
    """LLM / Embedding 供应商配置。

    不直接保存 api_key，而是通过 api_key_env 引用环境变量名，
    避免密钥写入版本控制的 YAML。
    models: 该供应商可选的模型名列表（纯手动维护，供前端下拉选择）。
    """

    name: str
    base_url: str = ""
    api_key_env: str = ""  # 环境变量名，如 OPENCODE_API_KEY；本地模型可空
    models: list[str] = []  # 可选模型名（纯手动维护；空 = 不提供下拉候选）

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("供应商名称不能为空")
        return v

    @field_validator("models")
    @classmethod
    def _models_strip(cls, v: list[str]) -> list[str]:
        return [m.strip() for m in v if m and m.strip()]


class ModelProfile(BaseModel):
    """某个任务（extraction / qa / todo / embedding）使用的模型配置。

    models: 有序模型候选列表，先尝试在前。首模型失败（配额/网络/5xx 等可恢复错误）
            时自动切换到下一个（同供应商内）。旧格式 model: "x" 自动迁移为 models: ["x"]。
    """

    provider: str
    models: list[str]

    @field_validator("provider")
    @classmethod
    def _provider_not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("模型 provider 不能为空")
        return v

    @model_validator(mode="before")
    @classmethod
    def _normalize_model(cls, data: Union[dict, "ModelProfile"]) -> Union[dict, "ModelProfile"]:
        """兼容旧格式：model: "x" → models: ["x"]。"""
        if isinstance(data, dict) and "model" in data and "models" not in data:
            m = data.pop("model")
            data["models"] = [m] if isinstance(m, str) and m.strip() else []
        return data

    @field_validator("models")
    @classmethod
    def _models_not_empty(cls, v: list[str]) -> list[str]:
        v = [m.strip() for m in v if m and m.strip()]
        if not v:
            raise ValueError("任务模型列表不能为空（至少一个模型名）")
        return v


class ModelsConfig(BaseModel):
    """各任务对应的模型配置。"""

    extraction: ModelProfile
    qa: ModelProfile
    todo: ModelProfile
    embedding: ModelProfile


class SourceConfig(BaseModel):
    """单个数据源（列表页）配置。

    抓取策略字段（阶段 7 抓取/提取优化）：
      - enabled: 停用来源无需删除配置
      - crawl_mode: incremental（增量早停，默认）/ full（全量翻页+变更检测）/ list_only（仅列表快照）
      - max_age_days: 只收录最近 N 天发布的（按列表页日期，无日期则忽略该过滤）
      - fetch_detail: 是否抓取详情页（false = 仅收录列表页标题/日期）
      - deep_check: 每轮重抓已入库详情页做内容指纹变更检测（incremental 下默认关闭）
    """

    name: str
    type: str = "web"
    list_url: str
    url_pattern: Optional[str] = None
    max_pages: int = 5
    enabled: bool = True
    crawl_mode: Literal["incremental", "full", "list_only"] = "incremental"
    max_age_days: Optional[int] = None
    fetch_detail: bool = True
    deep_check: bool = False

    @field_validator("name", "list_url")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("数据源 name / list_url 不能为空")
        return v

    @field_validator("max_pages")
    @classmethod
    def _max_pages_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_pages 至少为 1")
        return v

    @field_validator("max_age_days")
    @classmethod
    def _max_age_days_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("max_age_days 至少为 1")
        return v


class SchoolConfig(BaseModel):
    """单个学校的数据源配置。"""

    name: str
    code: str
    sources: list[SourceConfig]

    @field_validator("name", "code")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("学校 name / code 不能为空")
        return v

    @field_validator("sources")
    @classmethod
    def _sources_not_empty(cls, v: list[SourceConfig]) -> list[SourceConfig]:
        if not v:
            raise ValueError("至少配置一个数据源")
        return v


class CrawlConfig(BaseModel):
    """全局抓取参数。"""

    interval_minutes: int = 60
    user_agent: str = "CampusAssistant/1.0"
    max_pages: int = 5
    # 调度器每日清理（W1 模块 1.1）：无 deadline 通知的默认有效期（天）
    expire_days: int = 90
    # 调度器每日清理是否自动删除过期通知（默认 False=只报告不删除，见 issue #3）
    cleanup_enabled: bool = False
    # 阶段 7 抓取/提取优化：
    # 增量早停：整页通知均已入库时停止翻页（incremental 模式生效）
    stop_when_caught_up: bool = True
    # 详情页请求超时（秒）
    request_timeout: int = 15
    # 详情页失败重试次数
    retry_times: int = 2
    # 详情页抓取并发数（默认 1=礼貌抓取；>1 时每线程独立 SQLite 连接）
    concurrency: int = 1
    # 每 N 轮定时抓取自动做一轮全来源深度变更检测（0=关闭，只靠手动深度抓取）
    deep_check_interval_cycles: int = 24

    @field_validator("interval_minutes", "max_pages", "expire_days", "request_timeout", "retry_times", "concurrency", "deep_check_interval_cycles")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("必须 >= 1")
        return v

    @field_validator("concurrency")
    @classmethod
    def _concurrency_cap(cls, v: int) -> int:
        if v > 8:
            raise ValueError("并发数过大（上限 8），请控制对目标站点的请求频率")
        return v


class ExtractConfig(BaseModel):
    """提取前置过滤配置（阶段 7：零 LLM 成本的规则预检，全部可关=行为接近现状）。"""

    # 每轮最多提取条数（通过预筛后才调 LLM）
    batch_limit: int = 50
    # 只提取最近 N 天发布的通知（发布时间缺失时回退抓取时间；None=不限制）
    max_age_days: Optional[int] = None
    # 正文长度低于该值不提取（过滤无正文/纯标题快照）
    min_content_length: int = 100
    # 标题或正文包含任一关键词才提取（逗号分隔；None/空=不限制）
    keyword_filter: Optional[str] = None
    # 标题包含任一关键词则跳过（逗号分隔；None/空=不限制）
    skip_keywords: Optional[str] = None
    # 规则预检：标题/正文含时间线索（日期/截止/报名/时间等）才提取
    require_time_hint: bool = False
    # 只提取命中订阅的通知（最省成本模式）
    match_subscription_only: bool = False
    # failed 通知是否在下轮重试
    retry_failed: bool = True
    # 跳过 LLM 提取（仅入库 + 建向量索引，状态置 partial；省 token 模式）
    skip_llm: bool = False

    @field_validator("batch_limit", "min_content_length", "max_age_days")
    @classmethod
    def _positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("必须 >= 1")
        return v


class SchedulerConfig(BaseModel):
    """调度器配置（阶段 6：并入后端进程后，CLI --no-* 开关映射为配置项）。

    写入权语义（§5.8）：本段只被调度器读取，app.yaml 写入唯一归后端 API 进程。
    `enabled=false` 只约束 API lifespan 集成，不影响显式 CLI 启动。
    """

    enabled: bool = True  # API 进程是否随 lifespan 拉起调度器
    enable_daily: bool = True  # 每日过期清理 + 向量一致性检查（对应 --no-daily）
    enable_extract: bool = True  # 抓取后提取（对应 --no-extract）
    enable_reminder: bool = True  # 每日截止提醒扫描（对应 --no-reminder）
    enable_health: bool = True  # 每日体检，模块 4.2（对应 --no-health）
    log_file: str = "data/logs/scheduler.log"


class AppConfig(BaseModel):
    """应用主配置。"""

    active_school: str
    models: ModelsConfig
    providers: dict[str, ProviderConfig]
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    extract: ExtractConfig = Field(default_factory=ExtractConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)

    @field_validator("active_school")
    @classmethod
    def _active_school_not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("active_school 不能为空")
        return v

    @model_validator(mode="after")
    def _check_providers_and_models(self):
        """所有字段校验完成后执行：确保 providers 非空且每个任务引用的 provider 存在。"""
        if not self.providers:
            raise ValueError("至少配置一个 provider")
        for task in ("extraction", "qa", "todo", "embedding"):
            profile = getattr(self.models, task)
            if profile.provider not in self.providers:
                raise ValueError(f"任务 {task} 引用的 provider '{profile.provider}' 不存在")
        return self
