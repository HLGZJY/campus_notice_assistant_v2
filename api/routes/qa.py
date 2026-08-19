"""问答模块（阶段 5）：SSE 流式端点 + 索引状态 + 历史记录。

- `GET /qa/ask/stream?question=&user_session_id=`：StreamingResponse（text/event-stream），
  LLM token 级流式输出（QAResult 序列化是盘点 §5.7 唯一例外，此处做 as_source 转换）。
  流内事件：status（阶段提示/缓存命中）→ delta* → done / error。
- `GET /qa/index-stats`：向量索引统计（问答页角标，§5.6 映射）。
- `GET /qa/history`：分页查询问答历史（按 created_at 倒序，session 隔离）。
- `DELETE /qa/history/{id}`：删除单条历史。
- `DELETE /qa/history`：清空历史（传 session_id 只清该会话；不传清空全部，慎用）。

SSE 事件负载（data-only + type 判别，兼容 EventSource）：
    data: {"type": "status", "stage": "retrieval|thinking|generating|cache_hit",
            "message": "...", "elapsed_ms": N, "similarity"?: N}
    data: {"type": "delta", "content": "<token 增量>"}
    data: {"type": "done", "answer": "...", "sources": [...], "retrieved_chunks": N}
    data: {"type": "error", "message": "..."}
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from api.deps import require_auth
from api.schemas import IndexStatsView, QaHistoryPage
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
    user_session_id: Optional[str] = Query(None, description="前端会话 ID（用于历史隔离）"),
) -> StreamingResponse:
    """SSE 流式问答：逐 token 产出 delta，末尾产出 done（含 as_source 来源）。

    - status 事件：各阶段提示（retrieval/thinking/generating）与缓存命中（cache_hit）。
    - 历史按 user_session_id 隔离；不传则作为匿名请求（缓存仍全局生效）。
    """

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event_type, payload in qa_service.ask_stream(question, user_session_id=user_session_id):
                if await request.is_disconnected():
                    logger.info("问答流式客户端断开，提前结束")
                    return
                if event_type == "status":
                    # 阶段提示：{stage, message, elapsed_ms} 直接并入 status 事件
                    yield _sse({"type": "status", **payload})
                elif event_type == "cache_hit":
                    # 缓存命中（service 层产生）：归一化为 status.cache_hit，附 history_id/similarity
                    yield _sse({"type": "status", "stage": "cache_hit", "message": "命中缓存", **payload})
                elif event_type == "delta":
                    yield _sse({"type": "delta", "content": payload})
                elif event_type == "done":
                    yield _sse({"type": "done", **_serialize_result(payload)})
                    return
        except Exception as e:  # noqa: BLE001 —— 流式异常转 SSE error 事件
            # 批次 D3：started 之后的中途断流不抛 500，向客户端发友好错误事件后正常关流；
            # 原始异常只进日志（避免泄露内部细节给前端）。
            logger.exception("问答流式失败: question=%r", question)
            try:
                qa_service.record_error_message(question, user_session_id)
            except Exception:  # noqa: BLE001 —— 历史写失败不阻断错误事件
                logger.warning("问答错误历史写入失败 question=%r", question)
            yield _sse({"type": "error", "message": "推理中断，请稍后重试"})

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


@router.get("/history", response_model=QaHistoryPage)
def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_session_id: Optional[str] = Query(None, description="前端会话 ID（只查该会话历史）"),
) -> QaHistoryPage:
    """分页查询问答历史（按 created_at 倒序；传 user_session_id 只返回该会话）。"""
    return QaHistoryPage(**qa_service.list_history(page, page_size, user_session_id))


@router.delete("/history/{history_id}")
def delete_history(history_id: int) -> dict:
    """删除单条问答历史。"""
    return qa_service.delete_history(history_id)


@router.delete("/history")
def clear_history(user_session_id: Optional[str] = Query(None, description="前端会话 ID")) -> dict:
    """清空当前会话的所有历史（不传 session_id 清空全部，慎用）。"""
    return qa_service.clear_history(user_session_id)
