"""问答相关服务：封装 M4 问答、索引、缓存与历史。"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
from datetime import datetime, timedelta
from typing import Optional

from core.qa import QAResult, ask_question
from storage.db import (
    compute_content_hash,
    get_connection,
    get_notice_by_id,
)

logger = logging.getLogger(__name__)


def _get_vector_index():
    """延迟导入 VectorIndex，避免在不需要向量功能时触发重依赖。"""
    from storage.vectorstore import get_vector_index

    return get_vector_index()


def _get_qa_config():
    """读取 QAConfig（enable_cache/cache_ttl_hours/similarity_threshold/max_history/semantic_scan_limit）。"""
    from config.store import ConfigStore

    return ConfigStore.get_instance().get_qa()


def _serialize_embedding(vec: list[float]) -> bytes:
    """list[float] → bytes（float32 little-endian）。"""
    return struct.pack(f"<{len(vec)}f", *vec)


def _deserialize_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """纯 Python cosine（QA 问题量级 < 1k，无需 numpy）。"""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _check_cache(question_hash: str, user_session_id: Optional[str], cfg) -> Optional[dict]:
    """一级缓存：精确 hash 命中 + TTL 检查。返回 dict 或 None。

    注：question_hash 全局唯一，缓存不按 session 隔离（session 仅用于历史列表）。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT id, answer_text, sources_json, retrieved_chunks, hit_count, expires_at
               FROM qa_history WHERE question_hash = ?""",
            (question_hash,),
        ).fetchone()
        if row is None:
            return None
        # TTL 检查
        try:
            exp = datetime.fromisoformat(row["expires_at"])
            if datetime.now() > exp:
                return None  # 过期，交给后续流程覆盖
        except (ValueError, TypeError):
            return None
        # 命中：hit_count 自增
        conn.execute(
            "UPDATE qa_history SET hit_count = hit_count + 1, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), row["id"]),
        )
        conn.commit()
        return {
            "id": row["id"],
            "answer": row["answer_text"],
            "sources": json.loads(row["sources_json"] or "[]"),
            "retrieved_chunks": row["retrieved_chunks"],
        }
    finally:
        conn.close()


def _check_semantic(question_emb: list[float], cfg) -> Optional[dict]:
    """二级缓存：cosine 相似度检索（阈值 similarity_threshold）。

    只扫最近 semantic_scan_limit 条未过期记录，控制线性扫描开销。
    """
    threshold = cfg.similarity_threshold
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, answer_text, sources_json, retrieved_chunks, embedding_blob, expires_at
               FROM qa_history
               WHERE embedding_blob IS NOT NULL
               ORDER BY updated_at DESC
               LIMIT ?""",
            (cfg.semantic_scan_limit,),
        ).fetchall()
        best_score, best_row = 0.0, None
        for r in rows:
            try:
                exp = datetime.fromisoformat(r["expires_at"])
                if datetime.now() > exp:
                    continue
                cand_emb = _deserialize_embedding(r["embedding_blob"])
                score = _cosine_similarity(question_emb, cand_emb)
                if score > best_score:
                    best_score, best_row = score, r
            except Exception:  # noqa: BLE001 —— 单条解析失败跳过，不影响整体
                continue
        if best_row is not None and best_score >= threshold:
            conn.execute(
                "UPDATE qa_history SET hit_count = hit_count + 1, updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), best_row["id"]),
            )
            conn.commit()
            return {
                "id": best_row["id"],
                "answer": best_row["answer_text"],
                "sources": json.loads(best_row["sources_json"] or "[]"),
                "retrieved_chunks": best_row["retrieved_chunks"],
                "similarity": best_score,
            }
    finally:
        conn.close()
    return None


def _write_cache(
    question_text: str,
    question_hash: str,
    question_emb: Optional[list[float]],
    result: QAResult,
    user_session_id: Optional[str],
    cfg,
) -> None:
    """写入缓存（覆盖同 question_hash 旧记录）。

    question_emb 为 None（embedding 计算失败/跳过）时 embedding_blob 写 NULL，
    精确 hash 缓存仍可用，仅语义缓存降级。
    """
    now = datetime.now()
    expires_at = now + timedelta(hours=cfg.cache_ttl_hours)
    emb_blob = _serialize_embedding(question_emb) if question_emb is not None else None
    sources_json = json.dumps(
        [
            {
                "notice_id": s.notice_id,
                "title": s.title,
                "url": s.url,
                "notice_type": s.notice_type,
                "deadline": s.deadline,
            }
            for s in result.sources
        ],
        ensure_ascii=False,
    )
    conn = get_connection()
    try:
        # UPSERT：同 hash 直接覆盖（更新答案/embedding/expires_at，hit_count 不重置）
        conn.execute(
            """INSERT INTO qa_history
               (question_text, question_hash, answer_text, sources_json, retrieved_chunks,
                embedding_blob, user_session_id, hit_count, created_at, updated_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
               ON CONFLICT(question_hash) DO UPDATE SET
                 answer_text = excluded.answer_text,
                 sources_json = excluded.sources_json,
                 retrieved_chunks = excluded.retrieved_chunks,
                 embedding_blob = excluded.embedding_blob,
                 updated_at = excluded.updated_at,
                 expires_at = excluded.expires_at""",
            (question_text, question_hash, result.answer, sources_json, result.retrieved_chunks,
             emb_blob, user_session_id, now.isoformat(), now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()
        # LRU 淘汰：超过 max_history 时按 updated_at 升序删最旧的
        total = conn.execute("SELECT COUNT(*) AS c FROM qa_history").fetchone()["c"]
        if total > cfg.max_history:
            excess = total - cfg.max_history
            conn.execute(
                """DELETE FROM qa_history WHERE id IN (
                     SELECT id FROM qa_history ORDER BY updated_at ASC LIMIT ?
                   )""",
                (excess,),
            )
            conn.commit()
    finally:
        conn.close()


def _has_semantic_candidates() -> bool:
    """是否存在未过期缓存条目（二级语义检索的前提，避免空表时无谓计算 embedding）。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM qa_history WHERE expires_at > ? LIMIT 1",
            (datetime.now().isoformat(),),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _record_message(
    question_text: str,
    answer_text: str,
    sources: list,
    retrieved_chunks: int,
    status: str,
    user_session_id: Optional[str],
) -> None:
    """写问答历史日志（append-only）。

    每次提问结束无论状态（answer / cache_hit / fallback / error）都记录一条，
    与缓存表 qa_history 解耦（缓存表按 question_hash 唯一，历史日志按次追加）。
    """
    sources_json = json.dumps(
        [
            {
                "notice_id": s.notice_id,
                "title": s.title,
                "url": s.url,
                "notice_type": s.notice_type,
                "deadline": s.deadline,
            }
            for s in sources
        ],
        ensure_ascii=False,
    )
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO qa_messages
               (user_session_id, question_text, answer_text, sources_json, retrieved_chunks, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_session_id, question_text, answer_text, sources_json, retrieved_chunks,
             status, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


async def _embed_question(question: str) -> Optional[list[float]]:
    """计算问题 embedding（best-effort）。

    失败返回 None：语义缓存降级为不可用，不阻塞主问答流程（离线测试同理，
    未配置/不可用的 embedding 模型不会把问答链路打挂）。
    """
    try:
        from utils.embedding import get_embeddings

        return await asyncio.to_thread(get_embeddings().embed_query, question)
    except Exception as e:  # noqa: BLE001 —— 缓存是附加能力，绝不上抛影响问答
        logger.warning("问题 embedding 计算失败（语义缓存降级）: %s", e)
        return None


def record_error_message(question: str, user_session_id: Optional[str], message: str = "推理中断，请稍后重试") -> None:
    """记录一次失败的提问（error 状态入历史日志）。"""
    _record_message(question, message, [], 0, "error", user_session_id)


async def ask_stream(question: str, user_session_id: Optional[str] = None):
    """流式问答：缓存检查 → 命中即一次性返回；未命中走完整 QA 并回写缓存。

    产出事件二元组 (event_type, payload)：
      - ("cache_hit", dict)：缓存命中 {history_id, similarity?, elapsed_ms}
      - ("status", dict) / ("delta", str) / ("done", QAResult)：透传 QAAgent.ask_stream
    """
    cfg = _get_qa_config()
    question_hash = compute_content_hash(question)
    question_emb: Optional[list[float]] = None

    # 一级 + 二级缓存（仅在启用缓存时）
    if cfg.enable_cache:
        cached = _check_cache(question_hash, user_session_id, cfg)
        if cached is not None:
            _record_message(question, cached["answer"],
                            [_dict_to_source_ref(s) for s in cached["sources"]],
                            cached["retrieved_chunks"], "cache_hit", user_session_id)
            yield ("cache_hit", {"history_id": cached["id"], "elapsed_ms": 0})
            yield ("done", QAResult(
                answer=cached["answer"],
                sources=[_dict_to_source_ref(s) for s in cached["sources"]],
                retrieved_chunks=cached["retrieved_chunks"],
            ))
            return

        # 二级：语义命中（先确认有可扫描条目，避免无谓计算 embedding）
        if _has_semantic_candidates():
            question_emb = await _embed_question(question)
            if question_emb is not None:
                sem_cached = _check_semantic(question_emb, cfg)
                if sem_cached is not None:
                    _record_message(question, sem_cached["answer"],
                                    [_dict_to_source_ref(s) for s in sem_cached["sources"]],
                                    sem_cached["retrieved_chunks"], "cache_hit", user_session_id)
                    yield ("cache_hit", {
                        "history_id": sem_cached["id"],
                        "similarity": sem_cached.get("similarity", 0),
                        "elapsed_ms": 0,
                    })
                    yield ("done", QAResult(
                        answer=sem_cached["answer"],
                        sources=[_dict_to_source_ref(s) for s in sem_cached["sources"]],
                        retrieved_chunks=sem_cached["retrieved_chunks"],
                    ))
                    return

    # 三级：完整 QA（注入日期基准，懒导入使冒烟测试可 patch core.qa.QAAgent 全链路生效）
    from core.qa import QAAgent

    agent = QAAgent(current_date=datetime.now().date())
    result = None
    async for item in agent.ask_stream(question):
        # 透传 status/delta 事件，done 先拦截存下、收尾时统一产出
        if item[0] == "done":
            result = item[1]
        else:
            yield item

    # done 必须回写缓存后再产出：路由层收到 done 即 return，generator 会被关闭，
    # 之后的代码不会再执行，因此缓存落库必须放在 yield 之前。
    if result is not None:
        # 兜底答案不缓存，但无论兜底与否都写历史日志
        is_fallback = result.answer.startswith("根据已抓取的通知，没有找到相关信息")
        _record_message(question, result.answer, list(result.sources), result.retrieved_chunks,
                        "fallback" if is_fallback else "answer", user_session_id)
        if cfg.enable_cache and result.answer and not is_fallback:
            if question_emb is None:
                question_emb = await _embed_question(question)
            try:
                await asyncio.to_thread(
                    _write_cache, question, question_hash, question_emb, result, user_session_id, cfg
                )
            except Exception as e:  # noqa: BLE001 —— 缓存写失败不阻断已完成的回答
                logger.warning("缓存写入失败 question=%r: %s", question, e)
        yield ("done", result)


def _dict_to_source_ref(d: dict):
    """dict → SourceRef。"""
    from core.qa import SourceRef

    return SourceRef(
        notice_id=d["notice_id"],
        title=d.get("title", ""),
        url=d.get("url", ""),
        notice_type=d.get("notice_type", ""),
        deadline=d.get("deadline"),
    )


# ---------- 历史 CRUD ----------


def list_history(page: int = 1, page_size: int = 20, user_session_id: Optional[str] = None) -> dict:
    """分页查询问答历史日志（按 created_at 倒序，append-only qa_messages）。"""
    conn = get_connection()
    try:
        offset = (page - 1) * page_size
        if user_session_id:
            rows = conn.execute(
                """SELECT id, question_text, answer_text, sources_json, retrieved_chunks,
                          status, created_at
                   FROM qa_messages WHERE user_session_id = ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (user_session_id, page_size, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM qa_messages WHERE user_session_id = ?",
                (user_session_id,),
            ).fetchone()["c"]
        else:
            rows = conn.execute(
                """SELECT id, question_text, answer_text, sources_json, retrieved_chunks,
                          status, created_at
                   FROM qa_messages ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (page_size, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS c FROM qa_messages").fetchone()["c"]
        return {
            "items": [
                {
                    "id": r["id"],
                    "question_text": r["question_text"],
                    "answer_text": r["answer_text"],
                    "sources": json.loads(r["sources_json"] or "[]"),
                    "retrieved_chunks": r["retrieved_chunks"],
                    "created_at": r["created_at"],
                    "status": r["status"],
                    "hit_count": 1 if r["status"] == "cache_hit" else 0,
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    finally:
        conn.close()


def delete_history(history_id: int) -> dict:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM qa_messages WHERE id = ?", (history_id,))
        conn.commit()
        return {"ok": cur.rowcount > 0, "id": history_id}
    finally:
        conn.close()


def clear_history(user_session_id: Optional[str] = None) -> dict:
    """清空问答历史日志（传 session_id 只清该会话；不传清空全部，慎用）。"""
    conn = get_connection()
    try:
        if user_session_id:
            cur = conn.execute("DELETE FROM qa_messages WHERE user_session_id = ?", (user_session_id,))
        else:
            cur = conn.execute("DELETE FROM qa_messages")
        conn.commit()
        return {"ok": True, "deleted": cur.rowcount}
    finally:
        conn.close()


def invalidate_cache_for_notice(notice_id: int) -> int:
    """通知入库/变更钩子：清除可能引用了该通知的缓存条目。

    保守策略：扫描 sources_json 是否含 notice_id，命中则删。返回删除条数。
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, sources_json FROM qa_history WHERE sources_json IS NOT NULL"
        ).fetchall()
        to_delete = []
        for r in rows:
            try:
                sources = json.loads(r["sources_json"] or "[]")
                if any(s.get("notice_id") == notice_id for s in sources):
                    to_delete.append(r["id"])
            except Exception:  # noqa: BLE001 —— 单条解析失败跳过
                continue
        if to_delete:
            placeholders = ",".join("?" * len(to_delete))
            conn.execute(f"DELETE FROM qa_history WHERE id IN ({placeholders})", to_delete)
            conn.commit()
        return len(to_delete)
    finally:
        conn.close()


def invalidate_all_cache() -> int:
    """全量清除（管理员/调试用）。"""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM qa_history")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------- 保留原有接口 ----------


def ask(question: str) -> QAResult:
    """基于已索引通知回答用户提问。"""
    return ask_question(question)


def get_index_stats() -> dict:
    """返回向量索引统计信息。导入失败时返回错误信息，避免 UI 崩溃。"""
    try:
        index = _get_vector_index()
        return {
            "chunks": index.count(),
            "persist_dir": index.stats().get("persist_dir", ""),
        }
    except Exception as e:
        return {
            "chunks": 0,
            "persist_dir": "",
            "error": f"{type(e).__name__}: {e}",
        }


def index_notice(notice_id: int) -> dict:
    """将单条通知增量加入向量索引。"""
    conn = get_connection()
    try:
        notice = get_notice_by_id(conn, notice_id)
        if not notice:
            return {"success": False, "error": "通知不存在"}
        notice = dict(notice)
    finally:
        conn.close()

    if not notice.get("raw_content"):
        return {"success": False, "error": "通知无正文内容"}

    try:
        index = _get_vector_index()
        result = index.add_notice(notice)
        return {"success": True, "notice_id": notice_id, "chunks": result["chunks"]}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def rebuild_index(statuses: Optional[list[str]] = None, dry_run: bool = False) -> dict:
    """全量重建向量索引。

    Args:
        statuses: 只索引指定状态的通知，默认 extracted 和 partial
        dry_run: 只统计不写入
    """
    statuses = statuses or ["extracted", "partial"]
    conn = get_connection()
    try:
        placeholders = ", ".join("?" * len(statuses))
        rows = conn.execute(
            f"SELECT * FROM notices WHERE status IN ({placeholders}) AND raw_content IS NOT NULL",
            statuses,
        ).fetchall()
        notices = [dict(r) for r in rows]
    finally:
        conn.close()

    index = _get_vector_index()
    result = index.rebuild(notices, dry_run=dry_run)
    return {
        "notices": result["notices"],
        "chunks": result["chunks"],
        "dry_run": dry_run,
    }


def remove_notice(notice_id: int) -> dict:
    """从向量索引中删除某通知。"""
    index = _get_vector_index()
    removed = index.remove_notice(notice_id)
    return {"success": True, "notice_id": notice_id, "removed": removed}
