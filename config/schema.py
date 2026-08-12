"""配置数据模型（Pydantic）。

定义应用级配置（providers、models、active_school）和学校数据源配置
的数据结构，所有配置加载均先验证 schema。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ProviderConfig(BaseModel):
    """LLM / Embedding 供应商配置。

    不直接保存 api_key，而是通过 api_key_env 引用环境变量名，
    避免密钥写入版本控制的 YAML。
    """

    name: str
    base_url: str = ""
    api_key_env: str = ""  # 环境变量名，如 OPENCODE_API_KEY；本地模型可空

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("供应商名称不能为空")
        return v


class ModelProfile(BaseModel):
    """某个任务（extraction / qa / todo / embedding）使用的模型配置。"""

    provider: str
    model: str

    @field_validator("provider", "model")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("模型 provider / model 不能为空")
        return v


class ModelsConfig(BaseModel):
    """各任务对应的模型配置。"""

    extraction: ModelProfile
    qa: ModelProfile
    todo: ModelProfile
    embedding: ModelProfile


class SourceConfig(BaseModel):
    """单个数据源（列表页）配置。"""

    name: str
    type: str = "web"
    list_url: str
    url_pattern: Optional[str] = None
    max_pages: int = 5

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

    @field_validator("interval_minutes", "max_pages", "expire_days")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
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
