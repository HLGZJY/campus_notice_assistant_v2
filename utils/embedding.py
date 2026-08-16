"""Embedding 客户端（M4 + M6 改造）。

M6 改造点：
  - 从 ConfigStore 读取 embedding 模型配置（provider + model）
  - 增加 version 感知：配置/模型变更后自动重建 embedding 实例
  - provider 不局限于 opencode-go，任何 OpenAI-compatible 端点均可配置
  - 未配置 base_url 或 provider 名为 local 时，fallback 到本地 sentence-transformers

复用 RAG 项目的 fallback 逻辑：
  1. 先尝试 OpenAI-compatible embedding API
  2. 不可用则自动切换到本地 sentence-transformers 的 all-MiniLM-L6-v2

为避免首次下载模型时访问 HuggingFace 被墙，默认设置 HF_ENDPOINT=https://hf-mirror.com。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from config.store import ConfigStore
from utils.llm import record_llm_usage

# 国内镜像，避免首次下载模型时连接失败
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# 单次 HTTP 请求的文本批大小，避免长文本超出接口 token 上限
_EMBED_BATCH_SIZE = 16

# 模块级缓存状态（被 ConfigStore.version 驱动失效）
_EMBEDDING_CACHE: Optional[object] = None
_CONFIG_VERSION_AT_LOAD: int = -1
_LAST_EMBEDDING_MODEL: Optional[str] = None
_LAST_EMBEDDING_PROVIDER: Optional[str] = None


def _probe_embedding_endpoint(base_url: str, api_key: str, model: str) -> bool:
    """直接探测 OpenAI-compatible embedding 接口是否可用，避免 OpenAIEmbeddings 打印 404 日志。"""
    import requests

    url = f"{base_url}/embeddings"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(
            url,
            headers=headers,
            json={"input": "test", "model": model},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _log_embedding_usage(
    model: str,
    input_tokens: int = 0,
    success: bool = True,
    error: Optional[str] = None,
) -> None:
    """把一次 embedding 调用写入 token 计量表（task=embedding）。"""
    record_llm_usage(
        task="embedding",
        model=model,
        input_tokens=input_tokens,
        output_tokens=0,
        success=success,
        error=error,
    )


class _MeteredOpenAIEmbeddings:
    """OpenAI-compatible embedding + token 计量。

    直接请求 {base_url}/embeddings 并从响应 usage 读取 prompt_tokens 记账；
    与 langchain 的 OpenAIEmbeddings 接口兼容（embed_documents / embed_query），
    storage/vectorstore.py 无需改动。
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url
        self.model = model
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import requests

        results: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[i : i + _EMBED_BATCH_SIZE]
            try:
                resp = requests.post(
                    f"{self.base_url}/embeddings",
                    headers=self._headers,
                    json={"model": self.model, "input": batch},
                    timeout=60,
                )
                data = resp.json()
                if resp.status_code != 200 or "data" not in data:
                    raise RuntimeError(f"embedding 接口返回异常: HTTP {resp.status_code} {data}")
            except Exception as e:  # noqa: BLE001 —— 失败也要记一次计量
                _log_embedding_usage(self.model, success=False, error=f"{type(e).__name__}: {e}")
                raise
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
            _log_embedding_usage(self.model, input_tokens=input_tokens)
            # 按 index 排序返回，保证与输入顺序一致
            batch_embeddings = [
                d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])
            ]
            results.extend(batch_embeddings)
        return results


class _CountingEmbeddings:
    """本地 embedding 包装：不产生 API 成本，仅记录调用次数（tokens=0）。"""

    def __init__(self, inner, model: str):
        self._inner = inner
        self.model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            vectors = self._inner.embed_documents(texts)
        except Exception as e:  # noqa: BLE001
            _log_embedding_usage(self.model, success=False, error=f"{type(e).__name__}: {e}")
            raise
        _log_embedding_usage(self.model)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        try:
            vector = self._inner.embed_query(text)
        except Exception as e:  # noqa: BLE001
            _log_embedding_usage(self.model, success=False, error=f"{type(e).__name__}: {e}")
            raise
        _log_embedding_usage(self.model)
        return vector


def create_embeddings(provider_name: Optional[str] = None, model_name: Optional[str] = None):
    """创建 embedding 实例。

    Args:
        provider_name: 供应商名；None 则从 ConfigStore 读取
        model_name: 模型名；None 则从 ConfigStore 读取
    """
    from langchain_huggingface import HuggingFaceEmbeddings

    store = ConfigStore.get_instance()
    if provider_name is None or model_name is None:
        provider, model_name = store.get_model("embedding")
        provider_name = provider.name
    else:
        provider = store.get_provider(provider_name)

    api_key = store.get_api_key(provider_name)

    # 1. 未配置 base_url 或明确本地 provider → 直接用本地模型
    if not provider.base_url:
        local_model = model_name if model_name else DEFAULT_LOCAL_EMBEDDING_MODEL
        # 兼容简写：all-MiniLM-L6-v2 → sentence-transformers/all-MiniLM-L6-v2；
        # 本地目录路径（含 / 或 \）保持原样，直接按路径加载
        if local_model and "/" not in local_model and "\\" not in local_model:
            local_model = f"sentence-transformers/{local_model}"
        logger.info(f"使用本地 embedding 模型: {local_model}")
        # 相对路径（如 models/bge-small-zh-v1.5）按项目根目录解析为绝对路径，
        # 避免依赖进程 cwd（scheduler/api/CLI 各自启动目录不同）
        resolved_model = local_model
        if ("/" in local_model or "\\" in local_model) and not os.path.isabs(local_model):
            resolved_model = str(
                Path(__file__).resolve().parent.parent / local_model
            )
            logger.info(f"本地模型解析为绝对路径: {resolved_model}")
        return _CountingEmbeddings(
            HuggingFaceEmbeddings(
                model_name=resolved_model,
                model_kwargs={"local_files_only": True},
            ),
            local_model,
        )

    # 2. 尝试 OpenAI-compatible embedding API（按候选模型列表顺序探测）
    if api_key:
        if model_name:
            candidates = [model_name]
        else:
            candidates = store.get_model_candidates("embedding")[1]
        tried: list[str] = []
        for cand in candidates:
            tried.append(cand)
            if _probe_embedding_endpoint(provider.base_url, api_key, cand):
                try:
                    logger.info(f"使用 OpenAI-compatible embedding 模型: {cand} @ {provider.base_url}")
                    return _MeteredOpenAIEmbeddings(provider.base_url, api_key, cand)
                except Exception as e:
                    logger.warning(
                        f"embedding 探测成功但初始化失败 ({type(e).__name__}: {e})，尝试下一个候选。"
                    )
                    continue
        logger.warning(
            f"embedding 接口探测失败（候选: {tried}），自动 fallback 到本地模型 {DEFAULT_LOCAL_EMBEDDING_MODEL}。"
        )
    else:
        logger.warning(
            f"API key 未配置，自动 fallback 到本地模型 {DEFAULT_LOCAL_EMBEDDING_MODEL}。"
        )

    return _CountingEmbeddings(
        HuggingFaceEmbeddings(
            model_name=DEFAULT_LOCAL_EMBEDDING_MODEL,
            model_kwargs={"local_files_only": True},
        ),
        DEFAULT_LOCAL_EMBEDDING_MODEL,
    )


def get_embeddings():
    """获取（缓存的）embedding 实例。

    通过比较 ConfigStore.version 与上次加载时的 version，
    自动识别配置变更并重建 embedding。
    """
    global _EMBEDDING_CACHE, _CONFIG_VERSION_AT_LOAD, _LAST_EMBEDDING_MODEL, _LAST_EMBEDDING_PROVIDER

    store = ConfigStore.get_instance()
    provider, model_name = store.get_model("embedding")
    current_version = store.version

    if (
        _EMBEDDING_CACHE is not None
        and _CONFIG_VERSION_AT_LOAD == current_version
        and _LAST_EMBEDDING_MODEL == model_name
        and _LAST_EMBEDDING_PROVIDER == provider.name
    ):
        return _EMBEDDING_CACHE

    _EMBEDDING_CACHE = create_embeddings(provider.name, model_name)
    _CONFIG_VERSION_AT_LOAD = current_version
    _LAST_EMBEDDING_MODEL = model_name
    _LAST_EMBEDDING_PROVIDER = provider.name
    return _EMBEDDING_CACHE


def invalidate_embedding_cache() -> None:
    """手动清空 embedding 缓存，下次调用 get_embeddings() 会重新创建实例。"""
    global _EMBEDDING_CACHE, _CONFIG_VERSION_AT_LOAD, _LAST_EMBEDDING_MODEL, _LAST_EMBEDDING_PROVIDER
    _EMBEDDING_CACHE = None
    _CONFIG_VERSION_AT_LOAD = -1
    _LAST_EMBEDDING_MODEL = None
    _LAST_EMBEDDING_PROVIDER = None
    logger.info("embedding 缓存已清空")


def get_embedding_model_info() -> dict:
    """返回当前 embedding 模型信息，供 UI 检测是否需要重建索引。"""
    store = ConfigStore.get_instance()
    provider, model_name = store.get_model("embedding")
    return {
        "provider": provider.name,
        "model": model_name,
        "base_url": provider.base_url,
        "api_key_status": store.get_api_key_status(provider.name),
    }
