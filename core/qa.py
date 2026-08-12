"""RAG 问答 Agent（M4）。

基于 Chroma 向量检索 + OpenAI Agents SDK 生成回答。
来源通知从检索结果的 metadata 确定性导出，不依赖 LLM 自报引用。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from storage.vectorstore import VectorIndex

from agents import (
    Agent,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from utils.llm import get_model_for_task, run_agent

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 6
DEFAULT_MAX_SOURCES = 5

QA_INSTRUCTIONS = """你是校园通知智能问答助手。请严格根据下面提供的【参考通知】内容回答用户问题。

## 回答规则
1. 只能基于提供的参考通知作答，不要编造参考通知中没有的信息。
2. 如果参考通知没有相关信息，请明确说明"根据已抓取的通知，没有找到相关信息"。
3. 回答时通过 [1]、[2] 等编号引用来源通知。
4. 如果问题是问"最近有哪些比赛/活动/通知"，请列出相关通知的标题、截止时间和关键信息。
5. 保持简洁、清晰，使用中文回答。

## 参考通知格式
每个参考通知前有 [编号]，请使用该编号引用。"""


class SourceRef(BaseModel):
    """回答引用的来源通知。"""

    notice_id: int
    title: str = ""
    url: str = ""
    notice_type: str = ""
    deadline: Optional[str] = None


class QAResult(BaseModel):
    """问答结果。"""

    answer: str = ""
    sources: list[SourceRef] = Field(default_factory=list)
    retrieved_chunks: int = 0


class QAAgent:
    """基于 OpenAI Agents SDK 的问答 Agent。"""

    def __init__(
        self,
        index: Optional["VectorIndex"] = None,
        top_k: int = DEFAULT_TOP_K,
        max_sources: int = DEFAULT_MAX_SOURCES,
        strategy: str = "none",
        expire_days: Optional[int] = None,
        search_mode: str = "vector",
        **search_kwargs,
    ):
        if index is None:
            from storage.vectorstore import VectorIndex

            index = VectorIndex()
        self.index = index
        self.api_key, self.base_url, self.model = get_model_for_task("qa")
        self.top_k = top_k
        self.max_sources = max_sources
        # 模块 2.3 过期策略（none/decay/filter）；默认 none=不过滤，实验结论落地后再切换
        self.strategy = strategy
        self.expire_days = expire_days
        # 模块 2.4 混合检索（vector/hybrid）；默认 vector，评测结论上/不上后再切换
        self.search_mode = search_mode
        self.search_kwargs = search_kwargs
        self._agent: Optional[Agent] = None

    def _get_agent(self) -> Agent:
        if self._agent is None:
            set_tracing_disabled(True)
            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
            set_default_openai_client(client, use_for_tracing=False)
            set_default_openai_api("chat_completions")
            self._agent = Agent(
                name="通知问答助手",
                instructions=QA_INSTRUCTIONS,
                model=self.model,
            )
        return self._agent

    def _retrieve(self, question: str) -> list:
        """检索 Top-K chunks（可按模块 2.3 策略过滤/降权，或模块 2.4 混合检索）。"""
        if self.search_mode == "hybrid":
            from storage.hybrid import HybridIndex

            hybrid = self.index if isinstance(self.index, HybridIndex) else HybridIndex(self.index)
            return hybrid.search(
                question,
                k=self.top_k,
                strategy=self.strategy,
                expire_days=self.expire_days,
                **self.search_kwargs,
            )
        return self.index.search(
            question,
            k=self.top_k,
            strategy=self.strategy,
            expire_days=self.expire_days,
            **self.search_kwargs,
        )

    def _build_context(self, docs: list) -> tuple[str, list[SourceRef]]:
        """把检索结果去重、编号，拼成 Prompt 中的 Context。"""
        # 按 notice_id 去重，同一个通知的多个 chunk 合并
        grouped: dict[int, dict] = {}
        for doc in docs:
            meta = doc.metadata
            nid = meta.get("notice_id")
            if nid is None:
                continue
            if nid not in grouped:
                grouped[nid] = {
                    "meta": meta,
                    "chunks": [doc.page_content],
                }
            else:
                grouped[nid]["chunks"].append(doc.page_content)

        sources: list[SourceRef] = []
        context_parts: list[str] = []
        for idx, (nid, item) in enumerate(grouped.items(), start=1):
            if idx > self.max_sources:
                break
            meta = item["meta"]
            # 合并同一通知的多个 chunk
            content = "\n---\n".join(item["chunks"])
            title = meta.get("title") or "未知标题"
            context_parts.append(
                f"[{idx}] 标题：{title}\n"
                f"类型：{meta.get('notice_type') or '-'}\n"
                f"截止时间：{meta.get('deadline') or '-'}\n"
                f"来源：{meta.get('source') or '-'}\n"
                f"内容摘要：{content}\n"
            )
            sources.append(
                SourceRef(
                    notice_id=nid,
                    title=title,
                    url=meta.get("url") or "",
                    notice_type=meta.get("notice_type") or "",
                    deadline=meta.get("deadline") or None,
                )
            )

        return "\n\n".join(context_parts), sources

    async def ask(self, question: str) -> QAResult:
        """回答一个问题。"""
        docs = self._retrieve(question)
        if not docs:
            return QAResult(
                answer="根据已抓取的通知，没有找到相关信息。",
                sources=[],
                retrieved_chunks=0,
            )

        context, sources = self._build_context(docs)
        prompt = (
            f"问题：{question}\n\n"
            f"参考通知：\n\n{context}\n\n"
            f"请根据参考通知回答问题，并用 [编号] 引用来源。"
        )

        agent = self._get_agent()
        result = await run_agent(agent, prompt, task="qa", model=self.model)
        answer = str(result.final_output or "").strip()
        if not answer:
            answer = "根据已抓取的通知，没有找到相关信息。"

        return QAResult(
            answer=answer,
            sources=sources,
            retrieved_chunks=len(docs),
        )


def ask_question(question: str) -> QAResult:
    """同步便捷函数。"""
    agent = QAAgent()
    return asyncio.run(agent.ask(question))
