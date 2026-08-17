"""配置相关服务：封装 ConfigStore，供 UI 调用。

职责：
  - 将 ConfigStore 的内部 Pydantic 模型转换为 Streamlit 友好的 dict
  - 提供配置测试（数据源 URL / 模型连接）
  - 所有 API 返回统一结构：{"ok": bool, "error": str|None, ...}
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from openai import AsyncOpenAI

from config.schema import (
    CrawlConfig,
    ExtractConfig,
    ModelProfile,
    ModelsConfig,
    ProviderConfig,
    SchoolConfig,
    SourceConfig,
)
from config.store import ConfigStore
from crawler.base import ListPageParser

logger = logging.getLogger(__name__)


# ---------- 读取：给 UI 展示 ----------

def get_config_for_ui() -> dict:
    """获取用于 UI 展示的配置（API key 不泄露）。"""
    return ConfigStore.get_instance().export_for_ui()


def get_providers_for_ui() -> dict:
    """获取供应商列表（含 API key 状态）。"""
    return get_config_for_ui()["providers"]


def get_models_for_ui() -> dict:
    """获取任务-模型映射。"""
    return get_config_for_ui()["models"]


def get_sources_for_ui() -> dict:
    """获取当前学校配置（含 name/code/sources）。"""
    store = ConfigStore.get_instance()
    school = store.get_school()
    return {
        "name": school.name,
        "code": school.code,
        "sources": [s.model_dump() for s in school.sources],
    }


def get_crawl_for_ui() -> dict:
    """获取全局抓取参数（阶段 7）。"""
    return ConfigStore.get_instance().get_crawl().model_dump()


def get_extract_for_ui() -> dict:
    """获取提取前置过滤配置（阶段 7）。"""
    return ConfigStore.get_instance().get_extract().model_dump()


def get_config_disk_info() -> dict:
    """获取配置文件磁盘信息。"""
    return ConfigStore.get_instance().get_disk_info()


# ---------- 写入：保存配置 ----------

def update_models(models_data: dict) -> dict:
    """保存模型配置。

    models_data 格式：
    {
        "extraction": {"provider": "opencode-zen", "model": "kimi-k2.7-code"},
        "qa": {...}, "todo": {...}, "embedding": {...}
    }
    """
    try:
        models = ModelsConfig(
            extraction=ModelProfile(**models_data["extraction"]),
            qa=ModelProfile(**models_data["qa"]),
            todo=ModelProfile(**models_data["todo"]),
            embedding=ModelProfile(**models_data["embedding"]),
        )
        result = ConfigStore.get_instance().save_models(models)
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("保存模型配置失败")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def update_providers(providers_data: dict[str, dict]) -> dict:
    """保存供应商配置。

    providers_data 格式：
    {"opencode-zen": {"name": "opencode-zen", "base_url": "...", "api_key_env": "OPENCODE_API_KEY", "models": [...]}}
    """
    try:
        providers = ConfigStore.build_providers_config(providers_data)
        result = ConfigStore.get_instance().save_providers(providers)
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("保存供应商配置失败")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def save_provider_api_key(provider_name: str, api_key: str) -> dict:
    """把供应商 API Key 写入 .env（gitignore，不落库），免重启生效。

    返回统一结构 {"ok": bool, "env_var": ..., "env_path": ..., "error": ...}。
    """
    try:
        return ConfigStore.get_instance().save_api_key(provider_name, api_key)
    except Exception as e:
        logger.exception("写入 API Key 失败")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def update_sources(sources_data: list[dict]) -> dict:
    """保存当前学校的数据源配置。"""
    try:
        store = ConfigStore.get_instance()
        school = store.get_school()
        sources = [SourceConfig(**s) for s in sources_data]
        school_config = SchoolConfig(name=school.name, code=school.code, sources=sources)
        result = store.save_sources(school.code, school_config)
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("保存数据源配置失败")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def update_crawl(crawl_data: dict) -> dict:
    """保存全局抓取参数（阶段 7：增量早停/超时/重试/并发/深检周期）。"""
    try:
        crawl = CrawlConfig(**crawl_data)
        result = ConfigStore.get_instance().save_crawl(crawl)
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("保存抓取参数失败")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def update_extract(extract_data: dict) -> dict:
    """保存提取前置过滤配置（阶段 7）。"""
    try:
        extract = ExtractConfig(**extract_data)
        result = ConfigStore.get_instance().save_extract(extract)
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("保存提取过滤配置失败")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def force_reload_config() -> dict:
    """强制从磁盘重新加载配置。"""
    try:
        return {"ok": True, **ConfigStore.get_instance().force_reload()}
    except Exception as e:
        logger.exception("重新加载配置失败")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------- 测试：连通性 ----------

def test_source_url(url: str, timeout: int = 15) -> dict:
    """测试数据源 URL 是否可达，并返回发现的链接数与建议的 url_pattern（阶段 7）。"""
    try:
        start = time.time()
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=timeout,
        )
        latency_ms = int((time.time() - start) * 1000)
        resp.encoding = resp.apparent_encoding

        if resp.status_code != 200:
            return {
                "ok": False,
                "status_code": resp.status_code,
                "latency_ms": latency_ms,
                "link_count": 0,
                "error": f"HTTP {resp.status_code}",
            }

        parser = ListPageParser(resp.text, url)
        links = parser.discover_notice_links()
        suggested_pattern, sample_links = parser.suggest_url_pattern()
        return {
            "ok": True,
            "status_code": resp.status_code,
            "latency_ms": latency_ms,
            "link_count": len(links),
            "suggested_pattern": suggested_pattern,
            "sample_links": sample_links,
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": 0,
            "link_count": 0,
            "error": f"{type(e).__name__}: {e}",
        }


def _record_connection_test(
    provider_name: str,
    model_name: str,
    response,
    error: Optional[str] = None,
) -> None:
    """把一次连通性测试写入 token 计量表（task=test）。

    成功读 response.usage 记账；失败记 success=0 + error。
    计量失败不影响主流程（record_llm_usage 内部已兜底）。
    """
    from utils.llm import record_llm_usage

    input_tokens = 0
    output_tokens = 0
    if error is None and response is not None:
        usage = getattr(response, "usage", None) or None
        if usage is not None:
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    record_llm_usage(
        task="test",
        provider=provider_name,
        model=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        success=error is None,
        error=error,
    )


def test_model_connection(provider_name: str, model_name: str, timeout: int = 30) -> dict:
    """测试模型连接是否可用。

    发送一个最小 chat completion 请求，验证 base_url / api_key / model 三者均可工作。
    """
    store = ConfigStore.get_instance()
    try:
        provider = store.get_provider(provider_name)
    except KeyError as e:
        return {"ok": False, "latency_ms": 0, "error": f"供应商不存在: {e}"}

    if not provider.base_url:
        return {"ok": False, "latency_ms": 0, "error": "该供应商未配置 base_url（本地模型无需测试）"}

    api_key = store.get_api_key(provider_name)
    if not api_key:
        return {"ok": False, "latency_ms": 0, "error": f"环境变量 {provider.api_key_env} 未设置"}

    try:
        import asyncio

        client = AsyncOpenAI(api_key=api_key, base_url=provider.base_url)
        start = time.time()
        response = asyncio.run(
            client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
                timeout=timeout,
            )
        )
        latency_ms = int((time.time() - start) * 1000)
        _record_connection_test(provider_name, model_name, response)
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "completion": response.choices[0].message.content or "",
            "error": None,
        }
    except Exception as e:
        _record_connection_test(provider_name, model_name, None, error=f"{type(e).__name__}: {e}")
        return {"ok": False, "latency_ms": 0, "error": f"{type(e).__name__}: {e}"}


# ---------- embedding 专用检测 ----------

def get_embedding_model_info() -> dict:
    """获取当前 embedding 模型信息。"""
    from utils.embedding import get_embedding_model_info as _get_info

    return _get_info()


def check_embedding_changed(previous: str) -> bool:
    """检测 embedding 模型是否相对于 previous 发生了变更。"""
    return ConfigStore.get_instance().check_embedding_model_changed(previous)


# ---------- 便捷：模型/供应商名列表 ----------

def get_provider_names() -> list[str]:
    return ConfigStore.get_instance().get_provider_names()


def get_model_names() -> dict[str, str]:
    return ConfigStore.get_instance().get_model_names()


def get_api_key_status(provider_name: str) -> bool:
    return ConfigStore.get_instance().get_api_key_status(provider_name)
