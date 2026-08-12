"""LLM 客户端配置：从 ConfigStore 读取任务级模型配置。

M6 改造：
  - 旧版：从 .env 直接读取 OPENCODE_API_KEY / OPENCODE_BASE_URL / LLM_MODEL
  - 新版：统一从 config/store.py 读取，每个任务（extraction/qa/todo）可独立配置模型
  - 保留 LLMConfig / get_llm_config() 作为薄兼容层，默认映射到 extraction 任务

W1 模块 1.4 改造：
  - 新增统一 LLM 调用点 run_agent()：提取/待办/问答三条链路共用，
    成功/失败都自动写入 token_usage 计量表，不用在每处重复埋点
  - record_llm_usage() 供 embedding 等非 Agents SDK 链路复用

使用方式：
    from utils.llm import get_model_for_task, run_agent
    api_key, base_url, model = get_model_for_task("extraction")
    result = await run_agent(agent, prompt, task="extraction", model=model)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from agents import Runner

from config.store import ConfigStore

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """兼容旧版的 LLM 配置。内部映射到 ConfigStore 的 extraction 任务。"""

    api_key: str
    base_url: str
    model: str

    def validate(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "未配置 API Key。请在项目根目录 .env 中设置对应供应商的环境变量，"
                "并在 config/app.yaml 中正确配置 providers.*.api_key_env。"
            )


def get_model_for_task(task: str) -> tuple[str, str, str]:
    """获取指定任务的 LLM 连接参数。

    Args:
        task: "extraction" | "qa" | "todo" | "embedding"

    Returns:
        (api_key, base_url, model_name)
    """
    store = ConfigStore.get_instance()
    provider, model_name = store.get_model(task)
    api_key = store.get_api_key(provider.name)
    return api_key, provider.base_url, model_name


def get_llm_config() -> LLMConfig:
    """兼容旧版：默认返回 extraction 任务的配置。"""
    api_key, base_url, model = get_model_for_task("extraction")
    return LLMConfig(api_key=api_key, base_url=base_url, model=model)


def get_api_key_for_env(env_var: str) -> Optional[str]:
    """读取指定环境变量名的 API key（兼容 .env 未加载时的兜底）。"""
    value = os.environ.get(env_var, "").strip()
    return value if value else None


# ---------- 统一调用点（W1 模块 1.4 token 计量） ----------


def record_llm_usage(
    task: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    success: bool = True,
    retry_count: int = 0,
    error: Optional[str] = None,
    notice_id: Optional[int] = None,
) -> None:
    """把一次 LLM 调用写入 token_usage 计量表（成功/失败都记账）。

    打开独立连接写入；计量失败只记 warning，绝不影响主流程。
    """
    try:
        from storage.db import get_connection, log_llm_usage

        conn = get_connection()
        try:
            log_llm_usage(
                conn,
                task=task,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                success=success,
                retry_count=retry_count,
                error=error,
                notice_id=notice_id,
            )
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 —— 计量失败不能影响主流程
        logger.warning("写入 token_usage 失败: %s", e)


def _extract_usage(result) -> tuple[int, int]:
    """从 RunResult.raw_responses 累加 input/output tokens。

    包含 SDK 内部重试（5xx/429 自动重试）产生的 token，与真实 API 计费一致。
    """
    input_tokens = 0
    output_tokens = 0
    for resp in (getattr(result, "raw_responses", None) or []) or []:
        usage = getattr(resp, "usage", None)
        if usage is not None:
            input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
    return input_tokens, output_tokens


async def run_agent(
    agent,
    prompt: str,
    *,
    task: str,
    model: str,
    attempt: int = 0,
    notice_id: Optional[int] = None,
):
    """统一 LLM 调用点：Runner.run + token 计量。

    提取/待办/问答三条链路共用此处，避免每处重复埋点。

    Args:
        agent: openai-agents 的 Agent 实例
        prompt: 发送给模型的完整 prompt
        task: extraction / qa / todo
        model: 实际使用模型名
        attempt: 本次是第几次尝试（0 = 首调），用于区分首调与重试
        notice_id: 关联通知 ID（提取/待办），问答为 None

    Returns:
        RunResult。调用失败时先写 success=0 的计量记录，再重新抛出异常，
        由各 Agent 原有的重试逻辑接管。
    """
    try:
        result = await Runner.run(agent, prompt)
    except Exception as e:  # noqa: BLE001 —— 失败也要记一次计量
        record_llm_usage(
            task=task,
            model=model,
            success=False,
            retry_count=attempt,
            error=f"{type(e).__name__}: {e}",
            notice_id=notice_id,
        )
        raise
    input_tokens, output_tokens = _extract_usage(result)
    record_llm_usage(
        task=task,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        success=True,
        retry_count=attempt,
        notice_id=notice_id,
    )
    return result
