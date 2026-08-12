"""问答模块（阶段 5）：SSE 流式端点 + 索引状态。

- `GET /qa/ask/stream?question=`：StreamingResponse（text/event-stream），
  LLM token 级流式输出（QAResult 序列化是盘点 §5.7 唯一例外，此处做 as_source 转换）。
- `GET /qa/index-stats`：向量索引统计（问答页角标，§5.6 映射）。

SSE 事件负载（data-only + type 判别，兼容 EventSource）：
    data: {"type": "delta", "content": "<token 增量>"}
    data: {"type": "done", "answer": "...", "sources": [...], "retrieved_chunks": N}
    data: {"type": "error", "message": "..."}
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from api.deps import require_auth
from api.schemas import IndexStatsView
from services import qa_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/qa",
    tags=["qa"],
    dependencies=[Depends(require_auth)],
)

def _sse(obj: dict) -> str:
    """把事件对象序列化为 SSE 的 data 行。"""
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _as_source(src) -> dict:
    """QAResult.sources 的 as_source 转换（盘点 §5.7 唯一例外，路由层接管）。"""
    return {
        "notice_id": src.notice_id,
        "title": src.title,
        "url": src.url,
        "notice_type": src.notice_type,
        "deadline": src.deadline,
    }


def _serialize_result(result) -> dict:
    """QAResult → SSE done 事件负载（来源逐条 as_source 转换）。"""
    return {
        "answer": result.answer,
        "sources": [_as_source(s) for s in result.sources],
        "retrieved_chunks": result.retrieved_chunks,
    }


@router.get("/ask/stream", response_class=StreamingResponse)
async def ask_stream(
    request: Request,
    question: str = Query(min_length=1, description="要回答的问题"),
) -> StreamingResponse:
    """SSE 流式问答：逐 token 产出 delta，末尾产出 done（含 as_source 来源）。"""

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event_type, payload in qa_service.ask_stream(question):
                if await request.is_disconnected():
                    logger.info("问答流式客户端断开，提前结束")
                    return
                if event_type == "delta":
                    yield _sse({"type": "delta", "content": payload})
                elif event_type == "done":
                    yield _sse({"type": "done", **_serialize_result(payload)})
                    return
        except Exception as e:  # noqa: BLE001 —— 流式异常转 SSE error 事件
            logger.exception("问答流式失败: question=%r", question)
            yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/index-stats", response_model=IndexStatsView)
def index_stats() -> IndexStatsView:
    """向量索引统计（chunks / persist_dir；加载失败返回 error 信息）。"""
    return IndexStatsView(**qa_service.get_index_stats())
