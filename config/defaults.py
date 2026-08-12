"""内置默认配置。

当 app.yaml 缺失、损坏或 schema 校验失败时，ConfigStore 会回退到这里的默认值，
保证应用至少能启动（虽然可能缺少 API key，但不会因配置错误直接崩溃）。
"""
from __future__ import annotations

from config.schema import (
    AppConfig,
    CrawlConfig,
    ModelProfile,
    ModelsConfig,
    ProviderConfig,
)

DEFAULT_PROVIDER_ZEN = ProviderConfig(
    name="opencode-zen",
    base_url="https://opencode.ai/zen/go/v1",
    api_key_env="OPENCODE_API_KEY",
)

DEFAULT_PROVIDER_BAILIAN = ProviderConfig(
    name="bailian",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key_env="DASHSCOPE_API_KEY",
)

DEFAULT_PROVIDER_LOCAL = ProviderConfig(
    name="local",
    base_url="",
    api_key_env="",
)

DEFAULT_MODELS = ModelsConfig(
    extraction=ModelProfile(provider="opencode-zen", model="kimi-k2.7-code"),
    qa=ModelProfile(provider="opencode-zen", model="deepseek-v4-pro"),
    todo=ModelProfile(provider="opencode-zen", model="kimi-k2.7-code"),
    embedding=ModelProfile(provider="local", model="sentence-transformers/all-MiniLM-L6-v2"),
)

DEFAULT_CONFIG = AppConfig(
    active_school="scuec",
    models=DEFAULT_MODELS,
    providers={
        "opencode-zen": DEFAULT_PROVIDER_ZEN,
        "bailian": DEFAULT_PROVIDER_BAILIAN,
        "local": DEFAULT_PROVIDER_LOCAL,
    },
    crawl=CrawlConfig(),
)
