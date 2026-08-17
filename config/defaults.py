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
    display_name="OpenCode Zen",
    base_url="https://opencode.ai/zen/go/v1",
    api_key_env="OPENCODE_API_KEY",
    models=["kimi-k2.7-code", "deepseek-v4-pro", "kimi-k2.5-turbo"],
    type="opencode-zen",
)

DEFAULT_PROVIDER_BAILIAN = ProviderConfig(
    name="bailian",
    display_name="阿里云百炼",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key_env="DASHSCOPE_API_KEY",
    models=["qwen3.7-flash", "qwen3.7-turbo", "qwen3.7-max"],
    type="bailian",
)

DEFAULT_PROVIDER_LOCAL = ProviderConfig(
    name="local",
    display_name="本地模型",
    base_url="",
    api_key_env="",
    models=["models/bge-small-zh-v1.5"],
    type="local",
)

DEFAULT_MODELS = ModelsConfig(
    extraction=ModelProfile(
        provider="opencode-zen",
        models=["kimi-k2.7-code", "deepseek-v4-pro"],
    ),
    qa=ModelProfile(
        provider="opencode-zen",
        models=["deepseek-v4-pro", "kimi-k2.7-code"],
    ),
    todo=ModelProfile(
        provider="opencode-zen",
        models=["kimi-k2.7-code", "deepseek-v4-pro"],
    ),
    embedding=ModelProfile(
        provider="local",
        models=["sentence-transformers/all-MiniLM-L6-v2"],
    ),
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
