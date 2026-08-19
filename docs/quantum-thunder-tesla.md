# 校园通知助手 - QA 智能问答功能实施方案

> 项目根：`f:\pending_Agent_Project\campus_notice_assistant_v2`
> 范围：问答历史持久化 + 日期基准 + 状态计时提示
> 模式：只输出方案，不执行

---

## A. 数据库 Schema 设计

### A.1 新增 `qa_history` 表（问答历史 + 缓存载体，二合一）

写入 `storage/db.py` 的 `SCHEMA` 常量末尾（紧跟 `tasks` 表后），与现有 10 张表同库管理。

```sql
CREATE TABLE IF NOT EXISTS qa_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text TEXT NOT NULL,                  -- 原始问题文本
    question_hash TEXT NOT NULL,                  -- SHA-256(空白折叠后)，精确去重键
    answer_text TEXT NOT NULL,                    -- 完整答案
    sources_json TEXT,                            -- QAResult.sources 的 JSON 序列化
    retrieved_chunks INTEGER DEFAULT 0,           -- 引用 chunk 数
    embedding_blob BLOB,                          -- 问题文本的 512 维 embedding（float32 序列化）
    user_session_id TEXT,                         -- 前端会话标识（localStorage 生成 UUID），用于多端隔离
    hit_count INTEGER DEFAULT 0,                  -- 缓存命中次数（统计用）
    created_at TEXT NOT NULL,                     -- 首次写入时间
    updated_at TEXT NOT NULL,                     -- 最近命中时间
    expires_at TEXT NOT NULL                      -- TTL 过期时间（created_at + cache_ttl）
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_qa_history_hash ON qa_history(question_hash);
CREATE INDEX IF NOT EXISTS idx_qa_history_created ON qa_history(created_at);
CREATE INDEX IF NOT EXISTS idx_qa_history_expires ON qa_history(expires_at);
CREATE INDEX IF NOT EXISTS idx_qa_history_session ON qa_history(user_session_id);
```

**字段决策说明：**
- `embedding_blob BLOB`：存问题向量，复用 `utils/embedding.py` 的 `get_embeddings().embed_query()` 返回 `list[float]`，序列化为 `struct.pack(f"<{len}f", *vec)` 的 bytes。BLOB 比 JSON 节省 60% 空间，且避免 JSON parse 开销。
- 不拆 `qa_questions` 独立表：用户决策 4 已明确"新建 questions 独立 collection"，但单表 `qa_history` 直接存 `embedding_blob` + 内存 cosine 比新建 Chroma collection 更轻量（QA 问题量级远小于通知 chunk，无需 HNSW 索引）。**推荐采用单表 + 内存 cosine 方案**：避免引入第二个 Chroma client（vectorstore.py 已暴露 PersistentClient 单例难维护，`_CLIENTS` 字典 + 锁重试 3 次）。
- `user_session_id`：用户决策 1 的"前端 localStorage 缓存近期会话减少首屏请求"，前端首次进入生成 UUID 存 localStorage，所有请求带上，后端按 session 隔离历史。
- `sources_json`：复用 `api/routes/qa.py` 的 `_serialize_result` 输出格式，直接存 as_source 后的 JSON，命中缓存时零转换直发 SSE done。
- `hit_count` + `updated_at`：用于 LRU 淘汰（`max_history` 超限时按 `updated_at` 升序淘汰）。

### A.2 迁移 SQL（追加到 `storage/db.py` 的 `_MIGRATIONS` 列表）

由于 `qa_history` 是新表，`SCHEMA` 的 `CREATE TABLE IF NOT EXISTS` 已幂等，无需 ALTER 迁移。但为保持与现有迁移模式一致，新增一条幂等索引迁移到 `_MIGRATIONS` 末尾（参考 `db.py:220-223` 的 `idx_tasks_lock` 模式）：

```python
# 追加到 _MIGRATIONS 末尾
"CREATE INDEX IF NOT EXISTS idx_qa_history_hash ON qa_history(question_hash)",
```

注：`SCHEMA` 里的 `CREATE INDEX IF NOT EXISTS` 已覆盖此索引，这条 ALTER 段作为冗余保险，避免旧库因 SCHEMA 执行顺序问题漏建索引。

### A.3 索引设计

| 索引名 | 列 | 类型 | 用途 |
|---|---|---|---|
| `idx_qa_history_hash` | `question_hash` | UNIQUE | 精确命中主键查询（一级缓存） |
| `idx_qa_history_created` | `created_at` | 普通 | 历史列表分页（按时间倒序） |
| `idx_qa_history_expires` | `expires_at` | 普通 | TTL 过期批量清理 |
| `idx_qa_history_session` | `user_session_id` | 普通 | 多端会话隔离查询 |

不建联合索引：cosine 检索需先全表扫 `embedding_blob`，索引无法加速向量计算，靠 `LIMIT + hit_count DESC` 控制扫描量。

---

## B. 后端核心改动

### B.1 `core/qa.py` - 日期基准 + 阶段事件产出

**改动 1：日期基准注入**

在 `QAAgent.__init__` 增加可选 `current_date` 参数（默认 `datetime.date.today()`），存为实例属性。`_build_prompt` 把日期拼入 system 上下文。

```python
# core/qa.py 顶部 import 增加
from datetime import date

# QAAgent.__init__ 签名新增参数（在第 78 行 usage_cb 后追加）：
def __init__(
    self,
    index: Optional["VectorIndex"] = None,
    top_k: int = DEFAULT_TOP_K,
    max_sources: int = DEFAULT_MAX_SOURCES,
    strategy: str = "none",
    expire_days: Optional[int] = None,
    search_mode: str = "vector",
    usage_cb=None,
    current_date: Optional[date] = None,  # 新增
    **search_kwargs,
):
    # ...原有初始化...
    self._current_date = current_date or date.today()
```

**改动 2：扩展 `QA_INSTRUCTIONS` 注入日期上下文**

修改 `QA_INSTRUCTIONS` 常量（第 35-45 行），在末尾追加日期提示段：

```python
QA_INSTRUCTIONS = """你是校园通知智能问答助手。请严格根据下面提供的【参考通知】内容回答用户问题。

## 回答规则
1. 只能基于提供的参考通知作答，不要编造参考通知中没有的信息。
2. 如果参考通知没有相关信息，请明确说明"根据已抓取的通知，没有找到相关信息"。
3. 回答时通过 [1]、[2] 等编号引用来源通知。
4. 如果问题是问"最近有哪些比赛/活动/通知"，请列出相关通知的标题、截止时间和关键信息。
5. 保持简洁、清晰，使用中文回答。

## 日期基准
当前日期：{current_date}（{weekday}）。
"本周"指 {current_week_start} 至 {current_week_end}；
"最近"/"近期"默认指最近 7 天；
"上月"指 {last_month_start} 至 {last_month_end}。
用户问题中出现的相对时间词，请按此基准日换算为绝对日期后再筛选参考通知。

## 参考通知格式
每个参考通知前有 [编号]，请使用该编号引用。"""
```

`_get_agent` 创建 Agent 时用 `.format(current_date=..., weekday=..., current_week_start=..., ...)` 填充。为避免每次都 format，缓存按 `(model, current_date_iso)` 复合 key。

**改动 3：`ask_stream` 阶段事件产出**

在 `core/qa.py:249-308` 的 `ask_stream` 改造，新增 4 个阶段事件：

```python
async def ask_stream(self, question: str):
    """流式回答一个问题（阶段 5 SSE）。

    产出事件二元组 (event_type, payload)：
      - ("status", dict)：阶段提示 {stage, message, elapsed_ms}
      - ("delta", str)：LLM 输出文本增量
      - ("done", QAResult)：完整问答结果
    """
    import time
    t0 = time.monotonic()

    # 阶段 1：检索
    yield ("status", {"stage": "retrieval", "message": "检索中", "elapsed_ms": 0})
    docs = self._retrieve(question)
    yield ("status", {"stage": "retrieval", "message": f"已检索 {len(docs)} 段", "elapsed_ms": int((time.monotonic() - t0) * 1000)})

    if not docs:
        yield (
            "done",
            QAResult(answer="根据已抓取的通知，没有找到相关信息。", sources=[], retrieved_chunks=0),
        )
        return

    context, sources = self._build_context(docs)
    prompt = self._build_prompt(question, context)

    # 阶段 2：思考（拼 prompt 完成进入 LLM 调用前）
    t1 = time.monotonic()
    yield ("status", {"stage": "thinking", "message": "思考中", "elapsed_ms": int((t1 - t0) * 1000)})

    parts: list[str] = []
    last_error: Optional[str] = None
    started = False
    for model in self.models:
        try:
            agent = self._get_agent(model)
            # 阶段 3：生成（首个 delta 产出前）
            yield ("status", {"stage": "generating", "message": "生成回复中", "elapsed_ms": int((time.monotonic() - t0) * 1000)})
            async for delta in run_agent_stream(agent, prompt, task="qa", model=model, provider=self.provider, usage_cb=self._usage_cb):
                started = True
                parts.append(delta)
                yield ("delta", delta)
            break
        except Exception as e:
            if started or not is_failover_worthy(e):
                raise
            last_error = f"模型 {model} 失败: {type(e).__name__}: {e}"
            logger.warning("问答流式模型切换 %s → 下一个候选: %s", model, last_error[:200])
            parts = []
            continue
    else:
        raise RuntimeError(last_error or "所有候选模型均失败")

    answer = "".join(parts).strip() or "根据已抓取的通知，没有找到相关信息。"
    cited = self._filter_cited_sources(answer, sources)
    yield (
        "done",
        QAResult(answer=answer, sources=cited, retrieved_chunks=len(cited)),
    )
```

**`usage_cb` 透传保持不变**（已在 `run_agent_stream` 调用链中保留）。

### B.2 `api/routes/qa.py` - SSE 契约扩展 + 历史记录 API

**改动 1：`event_stream` 新增 status 事件分发**

在 `api/routes/qa.py:65-80` 的 `event_stream` 内增加 status 分支：

```python
async def event_stream() -> AsyncIterator[str]:
    try:
        async for event_type, payload in qa_service.ask_stream(question, user_session_id=user_session_id):
            if await request.is_disconnected():
                logger.info("问答流式客户端断开，提前结束")
                return
            if event_type == "status":
                yield _sse({"type": "status", **payload})  # 新增
            elif event_type == "cache_hit":  # 缓存命中专用事件（在 service 层产生）
                yield _sse({"type": "status", "stage": "cache_hit", "message": "命中缓存", **payload})
            elif event_type == "delta":
                yield _sse({"type": "delta", "content": payload})
            elif event_type == "done":
                yield _sse({"type": "done", **_serialize_result(payload)})
                return
    except Exception as e:
        logger.exception("问答流式失败: question=%r", question)
        yield _sse({"type": "error", "message": "推理中断，请稍后重试"})
```

**改动 2：路由签名增加 `user_session_id` Query 参数**

```python
@router.get("/ask/stream", response_class=StreamingResponse)
async def ask_stream(
    request: Request,
    question: str = Query(min_length=1, description="要回答的问题"),
    user_session_id: Optional[str] = Query(None, description="前端会话 ID（用于历史隔离"),
) -> StreamingResponse:
```

**改动 3：缓存检查逻辑放在 `qa_service.ask_stream`（不放路由层）**

**理由**：路由层应保持薄（只做协议转换）；缓存查询需要访问 SQLite + 计算 embedding + 调 cosine，属于业务逻辑，归 service 层；同时 service 层可在 yield status 事件后再 yield done，整个流式契约统一在 service 层产出。

**改动 4：新增历史记录 REST API**

在 `api/routes/qa.py` 末尾追加：

```python
class QaHistoryItem(BaseModel):
    id: int
    question_text: str
    answer_text: str
    sources: list = []
    retrieved_chunks: int = 0
    created_at: str
    hit_count: int = 0

class QaHistoryPage(BaseModel):
    items: list[QaHistoryItem]
    total: int = 0
    page: int = 1
    page_size: int = 20

@router.get("/history", response_model=QaHistoryPage)
def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_session_id: Optional[str] = Query(None),
) -> QaHistoryPage:
    """分页查询问答历史（按 created_at 倒序）。"""
    return QaHistoryPage(**qa_service.list_history(page, page_size, user_session_id))

@router.delete("/history/{history_id}")
def delete_history(history_id: int) -> dict:
    return qa_service.delete_history(history_id)

@router.delete("/history")
def clear_history(user_session_id: Optional[str] = Query(None)) -> dict:
    """清空当前会话的所有历史（不传 session_id 清空全部，慎用）。"""
    return qa_service.clear_history(user_session_id)
```

### B.3 `services/qa_service.py` - 缓存查询/写入/语义匹配/TTL/失效

完整改造后的 `services/qa_service.py`：

```python
"""问答相关服务：封装 M4 问答、索引、缓存与历史。"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
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
    from storage.vectorstore import get_vector_index
    return get_vector_index()


def _get_qa_config():
    """读取 QAConfig（cache_ttl_hours/similarity_threshold/enable_cache/max_history）。"""
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
    """一级缓存：精确 hash 命中 + TTL 检查。返回 dict 或 None。"""
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
    """二级缓存：cosine 相似度检索（阈值 0.85）。"""
    threshold = cfg.similarity_threshold
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, answer_text, sources_json, retrieved_chunks, embedding_blob, expires_at
               FROM qa_history
               WHERE embedding_blob IS NOT NULL
               ORDER BY updated_at DESC
               LIMIT 200"""  # 仅扫最近 200 条，控制开销
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
            except Exception:
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
    question_emb: list[float],
    result: QAResult,
    user_session_id: Optional[str],
    cfg,
) -> None:
    """写入缓存（覆盖同 question_hash 旧记录）。"""
    now = datetime.now()
    expires_at = now + timedelta(hours=cfg.cache_ttl_hours)
    emb_blob = _serialize_embedding(question_emb)
    sources_json = json.dumps(
        [{"notice_id": s.notice_id, "title": s.title, "url": s.url, "notice_type": s.notice_type, "deadline": s.deadline} for s in result.sources],
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
        self_total = conn.execute("SELECT COUNT(*) AS c FROM qa_history").fetchone()["c"]
        if self_total > cfg.max_history:
            excess = self_total - cfg.max_history
            conn.execute(
                """DELETE FROM qa_history WHERE id IN (
                     SELECT id FROM qa_history ORDER BY updated_at ASC LIMIT ?
                   )""",
                (excess,),
            )
            conn.commit()
    finally:
        conn.close()


async def ask_stream(question: str, user_session_id: Optional[str] = None):
    """流式问答：缓存检查 → 命中即一次性返回；未命中走完整 QA 并回写缓存。"""
    cfg = _get_qa_config()
    question_hash = compute_content_hash(question)

    # 一级：精确命中
    if cfg.enable_cache:
        cached = _check_cache(question_hash, user_session_id, cfg)
        if cached is not None:
            yield ("cache_hit", {"history_id": cached["id"], "elapsed_ms": 0})
            yield ("done", QAResult(
                answer=cached["answer"],
                sources=[_dict_to_source_ref(s) for s in cached["sources"]],
                retrieved_chunks=cached["retrieved_chunks"],
            ))
            return

    # 计算 embedding（二级检索前置；embedding 模块已进程级缓存）
    from utils.embedding import get_embeddings
    question_emb = await asyncio.to_thread(get_embeddings().embed_query, question)

    # 二级：语义命中
    if cfg.enable_cache:
        sem_cached = _check_semantic(question_emb, cfg)
        if sem_cached is not None:
            yield ("cache_hit", {"history_id": sem_cached["id"], "similarity": sem_cached.get("similarity", 0), "elapsed_ms": 0})
            yield ("done", QAResult(
                answer=sem_cached["answer"],
                sources=[_dict_to_source_ref(s) for s in sem_cached["sources"]],
                retrieved_chunks=sem_cached["retrieved_chunks"],
            ))
            return

    # 三级：完整 QA（注入日期基准）
    from core.qa import QAAgent
    agent = QAAgent(current_date=datetime.now().date())
    result = None
    async for item in agent.ask_stream(question):
        # 透传 status/delta 事件
        if item[0] == "done":
            result = item[1]
        else:
            yield item

    if result is not None and cfg.enable_cache and result.answer:
        # 不缓存兜底答案（"没有找到相关信息"）
        if not result.answer.startswith("根据已抓取的通知，没有找到相关信息"):
            try:
                await asyncio.to_thread(
                    _write_cache, question, question_hash, question_emb, result, user_session_id, cfg
                )
            except Exception as e:
                logger.warning("缓存写入失败 question=%r: %s", question, e)


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
    conn = get_connection()
    try:
        offset = (page - 1) * page_size
        if user_session_id:
            rows = conn.execute(
                """SELECT id, question_text, answer_text, sources_json, retrieved_chunks,
                          created_at, hit_count
                   FROM qa_history WHERE user_session_id = ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (user_session_id, page_size, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM qa_history WHERE user_session_id = ?",
                (user_session_id,),
            ).fetchone()["c"]
        else:
            rows = conn.execute(
                """SELECT id, question_text, answer_text, sources_json, retrieved_chunks,
                          created_at, hit_count
                   FROM qa_history ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (page_size, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS c FROM qa_history").fetchone()["c"]
        return {
            "items": [
                {
                    "id": r["id"],
                    "question_text": r["question_text"],
                    "answer_text": r["answer_text"],
                    "sources": json.loads(r["sources_json"] or "[]"),
                    "retrieved_chunks": r["retrieved_chunks"],
                    "created_at": r["created_at"],
                    "hit_count": r["hit_count"],
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
        cur = conn.execute("DELETE FROM qa_history WHERE id = ?", (history_id,))
        conn.commit()
        return {"ok": cur.rowcount > 0, "id": history_id}
    finally:
        conn.close()


def clear_history(user_session_id: Optional[str] = None) -> dict:
    conn = get_connection()
    try:
        if user_session_id:
            cur = conn.execute("DELETE FROM qa_history WHERE user_session_id = ?", (user_session_id,))
        else:
            cur = conn.execute("DELETE FROM qa_history")
        conn.commit()
        return {"ok": True, "deleted": cur.rowcount}
    finally:
        conn.close()


def invalidate_cache_for_notice(notice_id: int) -> int:
    """通知入库/变更钩子：清除可能引用了该通知的缓存条目。

    保守策略：扫描 sources_json 是否含 notice_id，命中则删。
    返回删除条数。"""
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
            except Exception:
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
    return ask_question(question)


def get_index_stats() -> dict:
    try:
        index = _get_vector_index()
        return {"chunks": index.count(), "persist_dir": index.stats().get("persist_dir", "")}
    except Exception as e:
        return {"chunks": 0, "persist_dir": "", "error": f"{type(e).__name__}: {e}"}


def index_notice(notice_id: int) -> dict:
    # ...（保持原样）...
    pass


def rebuild_index(statuses: Optional[list[str]] = None, dry_run: bool = False) -> dict:
    # ...（保持原样）...
    pass


def remove_notice(notice_id: int) -> dict:
    # ...（保持原样）...
    pass
```

### B.4 `storage/db.py` - 不新增函数（CRUD 全在 qa_service）

**理由**：`storage/db.py` 当前 1700+ 行已较大，QA CRUD 与 service 紧耦合（涉及 embedding 序列化、JSON parse），归 service 层更内聚。`compute_content_hash`（`db.py:185-193`）和 `get_connection` 已够用，无需新增 db 函数。

### B.5 `storage/vectorstore.py` - 不改动

**理由**：A.1 已决策不新建 Chroma questions collection，questions 直接存 `qa_history.embedding_blob` + 内存 cosine。vectorstore.py 保持只服务通知 chunk 索引。

### B.6 `config/schema.py` - 新增 QAConfig + AppConfig.qa 字段

在 `config/schema.py` 的 `ExtractConfig` 之后、`SchedulerConfig` 之前插入：

```python
class QAConfig(BaseModel):
    """QA 问答模块配置（缓存 + 历史记录）。"""

    # 是否启用问答缓存（精确 hash + 语义 cosine 双层）
    enable_cache: bool = True
    # 缓存 TTL（小时）
    cache_ttl_hours: int = 24
    # 语义匹配 cosine 阈值（>= 该值才命中）
    similarity_threshold: float = 0.85
    # 历史记录上限（条），超出按 LRU(updated_at) 淘汰
    max_history: int = 500
    # 语义检索扫描窗口大小（最近 N 条）
    semantic_scan_limit: int = 200

    @field_validator("cache_ttl_hours", "max_history", "semantic_scan_limit")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("必须 >= 1")
        return v

    @field_validator("similarity_threshold")
    @classmethod
    def _threshold_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("similarity_threshold 必须在 0.0~1.0 之间")
        return v
```

修改 `AppConfig`（第 279 行）：

```python
class AppConfig(BaseModel):
    active_school: str
    models: ModelsConfig
    providers: dict[str, ProviderConfig]
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    extract: ExtractConfig = Field(default_factory=ExtractConfig)
    qa: QAConfig = Field(default_factory=QAConfig)  # 新增
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
```

### B.7 `config/store.py` - 新增 `get_qa()` 方法

在 `get_extract()` 之后追加（参考 `store.py:137-138`）：

```python
def get_qa(self) -> QAConfig:
    return self._data.qa
```

### B.8 `config/app.yaml` - 新增 qa 配置段

在 `extract:` 段之后追加（默认值已设，可省略，但显式列出便于运维）：

```yaml
qa:
  enable_cache: true
  cache_ttl_hours: 24
  similarity_threshold: 0.85
  max_history: 500
  semantic_scan_limit: 200
```

### B.9 `services/notice_service.py` - 缓存失效钩子

在 `_process_one`（`notice_service.py:581-643`）的 `if auto_index:` 块内 `add_notice` 成功后注入钩子。两处都需要改（skip_llm 路径 + 正常提取路径）：

**改动位置 1**：`notice_service.py:592-597`（skip_llm 分支）

```python
if auto_index:
    try:
        updated = get_notice_by_id(conn2, notice["id"])
        await asyncio.to_thread(_get_vector_index().add_notice, dict(updated))
        # 新增：通知入库后清除受影响的 QA 缓存
        try:
            from services.qa_service import invalidate_cache_for_notice
            await asyncio.to_thread(invalidate_cache_for_notice, notice["id"])
        except Exception as e:
            logger.warning("QA 缓存失效钩子失败 notice_id=%s: %s", notice["id"], e)
    except Exception as e:
        logger.warning("自动索引失败 notice_id=%s: %s", notice["id"], e)
```

**改动位置 2**：`notice_service.py:623-628`（正常提取分支）同样模式注入。

**改动位置 3**：`notice_service.py:384-390`（单条 extract_one 入口的 auto_index 块）同样模式注入。

钩子失败不阻断主流程（try/except 包住）。

---

## C. 缓存/去重策略与质量保证

### C.1 三级命中流程图

```
用户提问
   │
   ▼
compute_content_hash(question)  →  question_hash（SHA-256 + 空白折叠）
   │
   ▼
[一级] SELECT FROM qa_history WHERE question_hash=? AND expires_at > now
   │
   ├─ 命中 → hit_count++, yield status(cache_hit), yield done(完整答案) → 结束
   │
   ▼ 未命中
get_embeddings().embed_query(question)  →  question_emb (512 维)
   │
   ▼
[二级] 扫描最近 200 条（updated_at DESC），cosine >= 0.85
   │
   ├─ 命中 → hit_count++, yield status(cache_hit, similarity), yield done → 结束
   │
   ▼ 未命中
[三级] 完整 QA 流程
   ├─ yield status(retrieval) → retrieve → status(thinking) → LLM 流式 → status(generating) → delta* → done
   │
   ▼
写入缓存（UPSERT by question_hash）
   ├─ 不缓存兜底答案（"没有找到相关信息"）
   ├─ 不缓存空答案
   └─ LRU 淘汰（> max_history 时删 updated_at 最旧的）
```

### C.2 并发安全

**同 question_hash 并发请求去重**：当前方案不做"并发同问题只跑一次"的 singleflight，理由如下：

- 现有 `create_task_or_get_existing`（`db.py:1630-1666`）用 `BEGIN IMMEDIATE` 串行化任务表，但 QA 是 SSE 流式响应，并发请求各自持独立 SSE 连接，无法共享同一流给多个客户端（每个客户端看到的 delta 不同步）。
- 改用**乐观写入 + UPSERT**：并发请求都会跑完整 QA，最后一个完成的 UPSERT 覆盖前面的，前端各自收到自己的流。这避免了"客户端 A 等 B 的结果"的复杂性。
- 后续如要优化，可引入 `asyncio.Lock` per question_hash（模块级 dict 缓存锁），但会增加状态复杂度，暂不引入。

**SQLite 写入安全**：`UPSERT ... ON CONFLICT(question_hash) DO UPDATE` 原子操作，`compute_content_hash` 已做空白折叠（`db.py:185-193`），避免大小写/空格差异造成多份缓存。

### C.3 质量保证机制

| 机制 | 实现 | 风险/缓解 |
|---|---|---|
| 兜底答案不缓存 | `if not result.answer.startswith("根据已抓取的通知，没有找到相关信息")` | 避免缓存空结果误导后续命中 |
| TTL 双重保障 | `expires_at` 字段（写入时计算）+ 通知入库钩子主动清除 | TTL 过期靠查询时检查；钩子是失效加速器 |
| 语义匹配阈值 | `similarity_threshold=0.85`（可配置） | 误判风险见 H.2 |
| 命中次数统计 | `hit_count` 字段 + `updated_at` 自更新 | 供运维查看缓存热度，识别"高频问题" |
| 埋点 | 复用 `services/tracking_service.track_event`，事件类型 `qa_cache_hit` | 不新增事件表，复用 events 表 |
| 用户可清除 | `DELETE /qa/history/{id}` + `DELETE /qa/history` | 单条/全量清空 |
| LRU 淘汰 | `max_history=500`，写入时检查超限删 `updated_at ASC LIMIT excess` | 避免无限膨胀 |

**埋点代码片段**（在 `services/qa_service.ask_stream` 命中分支末尾）：

```python
from services.tracking_service import track_event
track_event("qa_cache_hit", ref_id=cached["id"], note=f"hash_hit|similarity={cached.get('similarity', 0):.3f}")
```

---

## D. 前端改动

### D.1 `frontend/src/api/schema.ts` - 扩展 QaStreamEvent + 新增 QaHistoryItem

修改 `schema.ts:81-93`：

```typescript
/** SSE 事件类型（兼容 qa_service 流式 + 路由层 as_source 转换 + 缓存命中 + 阶段提示）。 */
export type QaStreamEvent =
  | { type: 'delta'; content: string }
  | { type: 'status'; stage: 'retrieval' | 'thinking' | 'generating' | 'cache_hit'; message: string; elapsed_ms: number; similarity?: number }
  | { type: 'done'; answer: string; sources: QaSourceRef[]; retrieved_chunks: number }
  | { type: 'error'; message: string }

/** 问答历史条目（GET /qa/history 返回）。 */
export interface QaHistoryItem {
  id: number
  question_text: string
  answer_text: string
  sources: QaSourceRef[]
  retrieved_chunks: number
  created_at: string
  hit_count: number
}

/** 历史分页响应。 */
export interface QaHistoryPage {
  items: QaHistoryItem[]
  total: number
  page: number
  page_size: number
}
```

### D.2 `frontend/src/api/endpoints.ts` - 新增历史端点

修改 `endpoints.ts:43-46`：

```typescript
qa: {
  stream: `${API_BASE}/qa/ask/stream`,
  indexStats: `${API_BASE}/qa/index-stats`,
  history: `${API_BASE}/qa/history`,
  historyDelete: (id: number) => `${API_BASE}/qa/history/${id}`,
  historyClear: `${API_BASE}/qa/history`,
},
```

### D.3 `frontend/src/api/qa.ts` - 新建历史 API 方法

新建文件 `frontend/src/api/qa.ts`（项目原本无此文件，services 层只是 store 直接调 http）：

```typescript
import { get, del } from './http'
import { endpoints } from './endpoints'
import type { QaHistoryPage } from './schema'

export function fetchQaHistory(page = 1, pageSize = 20, userSessionId?: string) {
  return get<QaHistoryPage>(endpoints.qa.history, { page, page_size: pageSize, user_session_id: userSessionId })
}

export function deleteQaHistory(id: number) {
  return del<{ ok: boolean; id: number }>(endpoints.qa.historyDelete(id))
}

export function clearQaHistory(userSessionId?: string) {
  return del<{ ok: boolean; deleted: number }>(endpoints.qa.historyClear, { /* qs 已在 del 内 */ } as any, {
    // DELETE 带 query 需手拼 URL
  })
}
```

注：`del` 工具签名 `(url, options)`，需要手拼 URL 或扩展 `del` 支持 params。**推荐**：扩展 `http.ts` 的 `del` 签名为 `del<T>(url, params?, options?)`，与 `get` 对齐（最小改动）。

### D.4 `frontend/src/stores/useQaStore.ts` - 重构 history 持久化 + 阶段状态

完整改造后：

```typescript
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { endpoints } from '../api/endpoints'
import { get } from '../api/http'
import { fetchQaHistory, deleteQaHistory, clearQaHistory } from '../api/qa'
import type { IndexStatsView, QaHistoryItem, QaSourceRef, QaStreamEvent } from '../api/schema'

const SESSION_KEY = 'qa_session_id'
const HISTORY_CACHE_KEY = 'qa_history_cache'

export interface QaMessage {
  id: string
  question: string
  answer: string
  sources: QaSourceRef[]
  retrievedChunks: number
  error?: string
  // 新增：阶段提示
  currentStage?: string
  currentStageStartedAt?: number
  stageElapsedMs?: number
  cached?: boolean  // 是否命中缓存
}

function getSessionId(): string {
  let id = localStorage.getItem(SESSION_KEY)
  if (!id) {
    id = crypto.randomUUID()
    localStorage.setItem(SESSION_KEY, id)
  }
  return id
}

export const useQaStore = defineStore('qa', () => {
  const history = ref<QaMessage[]>([])
  const streaming = ref(false)
  // 新增：当前阶段
  const currentStage = ref<string>('')
  const currentStageStartedAt = ref<number>(0)

  async function fetchIndexStats() {
    return await get<IndexStatsView>(endpoints.qa.indexStats)
  }

  // 新增：启动时加载历史
  async function loadHistory() {
    // 先读 localStorage 减少首屏空白
    try {
      const cached = localStorage.getItem(HISTORY_CACHE_KEY)
      if (cached) {
        const items: QaHistoryItem[] = JSON.parse(cached)
        history.value = items.map(itemToMessage)
      }
    } catch { /* ignore */ }

    // 再从后端拉取最新
    try {
      const page = await fetchQaHistory(1, 50, getSessionId())
      history.value = page.items.map(itemToMessage)
      // 回写 localStorage（仅缓存最近 50 条）
      localStorage.setItem(HISTORY_CACHE_KEY, JSON.stringify(page.items))
    } catch { /* 静默失败，不打断 UI */ }
  }

  function itemToMessage(item: QaHistoryItem): QaMessage {
    return {
      id: `hist-${item.id}`,
      question: item.question_text,
      answer: item.answer_text,
      sources: item.sources,
      retrievedChunks: item.retrieved_chunks,
      cached: item.hit_count > 0,
    }
  }

  async function askStream(
    payload: { question: string; params?: Record<string, unknown> },
    onEvent: (evt: QaStreamEvent) => void,
    signal?: AbortSignal,
  ) {
    streaming.value = true
    currentStage.value = ''
    currentStageStartedAt.value = Date.now()
    try {
      const params = new URLSearchParams({ question: payload.question })
      params.set('user_session_id', getSessionId())
      if (payload.params) {
        for (const [k, v] of Object.entries(payload.params)) {
          if (v !== undefined && v !== null) params.set(k, String(v))
        }
      }
      const res = await fetch(`${endpoints.qa.stream}?${params.toString()}`, {
        method: 'GET',
        headers: { 'Accept': 'text/event-stream' },
        signal,
      })

      if (!res.ok) {
        const txt = await res.text().catch(() => 'request failed')
        throw new Error(`${res.status}: ${txt}`)
      }
      if (!res.body) throw new Error('stream not supported')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let done = false
      while (!done) {
        const { value, done: d } = await reader.read()
        done = d
        if (value) {
          buffer += decoder.decode(value, { stream: !done })
          let idx: number
          while ((idx = buffer.indexOf('\n\n')) !== -1) {
            const block = buffer.slice(0, idx)
            buffer = buffer.slice(idx + 2)
            for (const line of block.split('\n')) {
              if (!line.startsWith('data:')) continue
              const raw = line.slice(5).trim()
              if (!raw) continue
              let evt: any
              try {
                evt = JSON.parse(raw)
              } catch {
                continue
              }
              if (evt.type === 'status') {
                currentStage.value = evt.stage
                currentStageStartedAt.value = Date.now()
                onEvent({
                  type: 'status',
                  stage: evt.stage,
                  message: evt.message ?? '',
                  elapsed_ms: evt.elapsed_ms ?? 0,
                  similarity: evt.similarity,
                })
              } else if (evt.type === 'delta' && typeof evt.content === 'string') {
                onEvent({ type: 'delta', content: evt.content })
              } else if (evt.type === 'done') {
                onEvent({
                  type: 'done',
                  answer: evt.answer ?? '',
                  sources: evt.sources ?? [],
                  retrieved_chunks: evt.retrieved_chunks ?? 0,
                })
              } else if (evt.type === 'error') {
                onEvent({ type: 'error', message: evt.message ?? 'stream error' })
              }
            }
          }
        }
        if (signal?.aborted) {
          throw new DOMException('aborted', 'AbortError')
        }
      }
    } finally {
      streaming.value = false
      currentStage.value = ''
    }
  }

  // 新增：删除单条历史
  async function removeHistory(id: number) {
    // hist-{id} 格式，提取 id
    const realId = parseInt(String(id).replace('hist-', ''), 10)
    await deleteQaHistory(realId)
    history.value = history.value.filter((m) => m.id !== `hist-${realId}`)
  }

  // 新增：清空历史
  async function clearAllHistory() {
    await clearQaHistory(getSessionId())
    history.value = []
    localStorage.removeItem(HISTORY_CACHE_KEY)
  }

  return {
    history,
    streaming,
    currentStage,
    currentStageStartedAt,
    fetchIndexStats,
    loadHistory,
    askStream,
    removeHistory,
    clearAllHistory,
  }
})
```

### D.5 `frontend/src/views/QaView.vue` - 阶段提示 + 历史侧栏

**改动 1：消息气泡内阶段提示**

替换 `QaView.vue:141-145` 的 3 圆点 loading 为带阶段文字 + 耗时：

```vue
<div v-else class="stage-indicator">
  <span class="stage-dot"></span>
  <span class="stage-text">{{ stageLabel(qa.currentStage) }}</span>
  <span v-if="stageElapsed" class="stage-elapsed">{{ stageElapsed }}</span>
</div>
```

新增 `stageLabel` 和 1s setInterval 计时（参考 `TaskListDrawer.vue:67-94` 的 `formatElapsed`）：

```typescript
const STAGE_LABELS: Record<string, string> = {
  retrieval: '检索中',
  thinking: '思考中',
  generating: '生成回复中',
  cache_hit: '命中缓存',
  '': '处理中',
}
function stageLabel(stage: string) {
  return STAGE_LABELS[stage] ?? '处理中'
}

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  timer = setInterval(() => { now.value = Date.now() }, 1000)
  qa.loadHistory()  // 启动时加载历史
})
onUnmounted(() => { if (timer) clearInterval(timer) })

const stageElapsed = computed(() => {
  if (!qa.currentStageStartedAt) return ''
  const secs = Math.max(0, Math.floor((now.value - qa.currentStageStartedAt) / 1000))
  if (secs < 60) return `${secs}s`
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}:${String(s).padStart(2, '0')}`
})
```

**改动 2：扩展事件回调处理 status**

修改 `QaView.vue:50-63`：

```typescript
await qa.askStream(
  { question: q },
  (evt) => {
    if (evt.type === 'status') {
      msg.currentStage = evt.stage
      msg.currentStageStartedAt = Date.now()
      if (evt.stage === 'cache_hit') {
        msg.cached = true
      }
    } else if (evt.type === 'delta') {
      msg.answer += evt.content
    } else if (evt.type === 'done') {
      msg.answer = evt.answer
      msg.sources = evt.sources
      msg.retrievedChunks = evt.retrieved_chunks
      msg.currentStage = undefined
      delete msg.error
    } else if (evt.type === 'error') {
      msg.error = evt.message
    }
  },
  abortController.signal,
)
```

**改动 3：历史会话侧栏（n-drawer）**

参考 `NoticesView.vue:796-829` 的 drawer 模式。在 `<template>` 末尾的 `</n-card>` 前插入：

```vue
<n-drawer v-model:show="historyOpen" :width="420" placement="left">
  <n-drawer-content title="问答历史" closable>
    <template #header-extra>
      <n-button quaternary size="small" @click="qa.clearAllHistory">
        <template #icon><n-icon><TrashOutline /></n-icon></template>
        清空
      </n-button>
    </template>
    <n-empty v-if="qa.history.length === 0" description="暂无历史记录" />
    <div v-else class="history-list">
      <div
        v-for="msg in qa.history"
        :key="msg.id"
        class="history-item"
        @click="scrollToMessage(msg.id)"
      >
        <div class="history-q">{{ msg.question }}</div>
        <div class="history-meta">
          <n-tag v-if="msg.cached" size="small" type="warning" :bordered="false">缓存</n-tag>
          <span class="muted">{{ msg.answer.slice(0, 60) }}{{ msg.answer.length > 60 ? '...' : '' }}</span>
        </div>
        <n-button
          class="history-del"
          quaternary
          size="tiny"
          @click.stop="qa.removeHistory(msg.id)"
        >
          <template #icon><n-icon><TrashOutline /></n-icon></template>
        </n-button>
      </div>
    </div>
  </n-drawer-content>
</n-drawer>
```

在 `section-title` 区域新增触发按钮：

```vue
<n-button quaternary size="small" @click="historyOpen = true">
  <template #icon><n-icon><LayersOutline /></n-icon></template>
  历史 ({{ qa.history.length }})
</n-button>
```

`historyOpen` 用 `ref(false)` 声明。

---

## E. SSE 契约变更总览

| 事件类型 | payload | 触发时机 | 前端处理 |
|---|---|---|---|
| `status` | `{stage, message, elapsed_ms, similarity?}` | 各阶段开始/结束 | 更新 currentStage + 计时 |
| `delta` | `{content}` | LLM 流式增量 | 追加 answer |
| `done` | `{answer, sources, retrieved_chunks}` | 完整结果 / 缓存命中 | 终态展示 |
| `error` | `{message}` | 异常 | 显示错误 |

**三种事件流序列**：

```
缓存命中流：status(cache_hit) → done
正常流：status(retrieval) → status(retrieval, "已检索 N 段") → status(thinking) → status(generating) → delta* → done
空检索流：status(retrieval) → status(retrieval, "已检索 0 段") → done(兜底)
错误流：status(retrieval)? → delta*? → error
```

**契约变更清单**：

| 文件 | 改动 |
|---|---|
| `frontend/src/api/schema.ts` | QaStreamEvent 新增 status 分支 + QaHistoryItem 接口 |
| `frontend/src/stores/useQaStore.ts` | askStream onEvent 新增 status 处理 + currentStage 状态 + loadHistory |
| `frontend/src/views/QaView.vue` | 阶段提示 UI + 历史侧栏 |
| `api/routes/qa.py` | event_stream 新增 status/cache_hit 分发 + user_session_id 参数 + 历史 API |
| `core/qa.py` | ask_stream yield status 事件 + current_date 注入 |
| `services/qa_service.py` | 缓存三层命中 + CRUD |

---

## F. 改动文件清单（按依赖顺序）

| 序号 | 文件路径 | 类型 | 说明 |
|---|---|---|---|
| 1 | `f:\pending_Agent_Project\campus_notice_assistant_v2\storage\db.py` | 修改 | SCHEMA 新增 qa_history 表 + _MIGRATIONS 追加 |
| 2 | `f:\pending_Agent_Project\campus_notice_assistant_v2\config\schema.py` | 修改 | 新增 QAConfig + AppConfig.qa 字段 |
| 3 | `f:\pending_Agent_Project\campus_notice_assistant_v2\config\store.py` | 修改 | 新增 get_qa() 方法 |
| 4 | `f:\pending_Agent_Project\campus_notice_assistant_v2\config\app.yaml` | 修改 | 新增 qa 配置段 |
| 5 | `f:\pending_Agent_Project\campus_notice_assistant_v2\core\qa.py` | 修改 | current_date 注入 + ask_stream yield status |
| 6 | `f:\pending_Agent_Project\campus_notice_assistant_v2\services\qa_service.py` | 修改 | 缓存三层 + CRUD + 失效钩子 |
| 7 | `f:\pending_Agent_Project\campus_notice_assistant_v2\services\notice_service.py` | 修改 | _process_one 三处 add_notice 后注入失效钩子 |
| 8 | `f:\pending_Agent_Project\campus_notice_assistant_v2\api\routes\qa.py` | 修改 | status 事件分发 + user_session_id + 历史 API |
| 9 | `f:\pending_Agent_Project\campus_notice_assistant_v2\api\schemas.py` | 修改 | 新增 QaHistoryItem/QaHistoryPage 模型 |
| 10 | `f:\pending_Agent_Project\campus_notice_assistant_v2\frontend\src\api\schema.ts` | 修改 | QaStreamEvent 扩展 + QaHistoryItem 接口 |
| 11 | `f:\pending_Agent_Project\campus_notice_assistant_v2\frontend\src\api\endpoints.ts` | 修改 | qa 新增 history/historyDelete/historyClear |
| 12 | `f:\pending_Agent_Project\campus_notice_assistant_v2\frontend\src\api\http.ts` | 修改 | del 扩展支持 params 参数 |
| 13 | `f:\pending_Agent_Project\campus_notice_assistant_v2\frontend\src\api\qa.ts` | 新增 | fetchQaHistory/deleteQaHistory/clearQaHistory |
| 14 | `f:\pending_Agent_Project\campus_notice_assistant_v2\frontend\src\stores\useQaStore.ts` | 修改 | history 持久化 + currentStage + loadHistory |
| 15 | `f:\pending_Agent_Project\campus_notice_assistant_v2\frontend\src\views\QaView.vue` | 修改 | 阶段提示 UI + 历史侧栏 |

---

## G. 测试计划

### G.1 后端测试（新建 `test_qa_cache.py`）

**测试约定**（务必遵守）：
- 临时库放 `tempfile.mkdtemp()` 专属目录，不放 `data/`
- `cleanup()` 用 `try/except OSError` 包住
- 测试用系统 venv Python：`C:\Users\Administrator\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`
- 参考 `test_qa_sse_error.py:93-106` 的临时库初始化模式

**测试用例**：

```python
# test_qa_cache.py
import asyncio
import json
import os
import struct
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
os.environ["APP_ENV"] = "test"

from fastapi.testclient import TestClient


def setup_isolated_env(tmpdir):
    """参考 test_qa_sse_error.py 隔离模式。"""
    from storage import db as db_mod
    db_mod.DB_PATH = Path(tmpdir) / "test_qa_cache.db"
    db_mod.get_connection().close()
    from config.store import ConfigStore
    ConfigStore.reset_instance()
    # 种子配置省略，参考 _seed_config


# 1. 精确命中：同问题第二次请求 → status(cache_hit) → done
def test_exact_hit():
    tmpdir = tempfile.mkdtemp()
    try:
        setup_isolated_env(tmpdir)
        # patch QAAgent.ask_stream 模拟首次跑完整 QA
        call_count = [0]
        async def fake_stream(question):
            call_count[0] += 1
            yield ("status", {"stage": "retrieval", "message": "检索中", "elapsed_ms": 0})
            yield ("delta", "答案 A")
            from core.qa import QAResult
            yield ("done", QAResult(answer="答案 A", sources=[], retrieved_chunks=1))

        with patch("core.qa.QAAgent") as FakeAgent:
            FakeAgent.return_value.ask_stream = fake_stream
            with TestClient(create_app()) as client:
                # 第一次：完整 QA
                r1 = client.stream("GET", "/api/v1/qa/ask/stream", params={"question": "test q"})
                events1 = [json.loads(l[6:]) for l in r1.iter_lines() if l.startswith("data: ")]
                assert "cache_hit" not in [e.get("type") for e in events1]
                assert call_count[0] == 1
                # 第二次：同问题
                r2 = client.stream("GET", "/api/v1/qa/ask/stream", params={"question": "test q"})
                events2 = [json.loads(l[6:]) for l in r2.iter_lines() if l.startswith("data: ")]
                assert any(e.get("type") == "status" and e.get("stage") == "cache_hit" for e in events2)
                assert call_count[0] == 1  # 未再调 LLM
    except OSError:
        pass


# 2. 语义命中：相似问题（非完全相同）→ 命中
# 3. TTL 过期：写入后改 expires_at 为过去时间 → 不命中
# 4. 通知入库失效钩子：先缓存包含 notice_id=1 的答案，调用 invalidate_cache_for_notice(1) → 缓存被删
# 5. 日期基准注入：mock QAAgent 检查 prompt 包含"当前日期"
# 6. 阶段事件产出：检查事件序列含 status(retrieval)/status(thinking)/status(generating)
# 7. 兜底答案不缓存：检索返回空，done 后查 qa_history 表无记录
# 8. LRU 淘汰：max_history=2，写 3 条，最早一条被删
```

### G.2 前端测试（手动验收）

| 验收项 | 操作 | 预期 |
|---|---|---|
| 阶段提示 | 提问 | 气泡内显示"检索中 / 思考中 / 生成回复中"+ 实时计时 |
| 缓存命中 | 同问题再问 | 显示"命中缓存"，几乎瞬时返回 |
| 历史侧栏 | 点"历史"按钮 | 左侧 drawer 展开，列出最近问答 |
| 历史回溯 | 点历史项 | 主区滚动到对应消息 |
| 删除历史 | 点历史项删除按钮 | 立即从列表移除 |
| localStorage 缓存 | 刷新页面 | 首屏先显示缓存历史，再异步刷新 |
| 跨设备 | A 设备问，B 设备看 | B 设备看到自己 session 的历史（隔离） |

---

## H. 风险与权衡

### H.1 缓存膨胀控制

- `max_history=500` 默认硬上限，超出按 `updated_at ASC` 删最旧的（LRU）
- `embedding_blob` 512 维 float32 = 2048 字节/条，500 条共 ~1MB，可接受
- `semantic_scan_limit=200` 限制二级检索扫描量，避免线性扫描随表增长退化
- 后续可加后台清理任务（类似 `scheduler_log` 清理）按 `expires_at` 批量删过期条目

### H.2 语义匹配误判

**风险**：相似但不等价的问题被误判为同一问题，返回错误答案。
- 例："有哪些竞赛可以报名？" vs "有哪些竞赛已经截止？" → embedding 可能接近，但答案应不同

**缓解**：
1. 阈值 0.85 偏保守（实测 bge-small-zh-v1.5 同义改写通常 > 0.90，反义词在 0.75-0.85 区间）
2. 命中时返回 `similarity` 字段，前端可对低于 0.90 的命中显示"相似命中（87%）"提示
3. 用户可手动删除单条缓存（`DELETE /qa/history/{id}`）
4. 可在 QAConfig 增加 `semantic_strict_keywords`（如"截止/已结束/未开始"），含这些词的问题跳过语义缓存
5. 兜底答案不缓存，避免"没有找到相关信息"被语义命中复用

### H.3 embedding 计算开销

- 每次未命中精确缓存的问题都要 embed（512 维向量）
- `get_embeddings()` 是进程级单例（`utils/embedding.py:238-262`），HuggingFace 模型加载只发生一次
- 本地 bge-small-zh-v1.5 CPU 推理 ~30ms/问，远小于 LLM 调用（~3-10s），可接受
- 后续可加内存级 LRU 缓存 `(question_text → embedding)`，避免同问题重复 embed

### H.4 阶段事件频率

**风险**：阶段切换过于频繁导致前端闪烁。
- 当前设计只 4 个阶段（retrieval/thinking/generating/cache_hit），每阶段切换间隔通常 > 100ms
- 前端只在 `currentStage` 变化时更新 UI（响应式 ref 天然去重）
- 1s setInterval 计时器仅在 `streaming=true` 期间运行，`onUnmounted` 清理

### H.5 单表方案的权衡

**选择单表 `qa_history` 而非新建 Chroma questions collection 的理由**：
1. QA 问题量级远小于通知 chunk（通知千级、chunk 万级；问题百级），无需 HNSW 索引
2. vectorstore.py 的 `_CLIENTS` 字典 + 锁重试 3 次模式（`vectorstore.py:57-89`）暴露了 Chroma 多 collection 维护成本
3. 纯 Python cosine 在 200 条规模下 < 5ms（512 维 * 200 = 102K 次乘加），足够
4. 单表方案减少一处故障点（Chroma 锁文件、tenant 不存在等已知问题）

**未来扩展**：若问题量级增长到 1k+，可平滑迁移到独立 Chroma collection，`_check_semantic` 函数签名不变，只换内部实现。

### H.6 SSE 连接保持

- 现有方案用 `request.is_disconnected()` 检测断开（`api/routes/qa.py:68`），缓存命中场景同样适用
- 缓存命中时事件少（status + done），客户端可能未及时收到，前端已用 ReadableStream + AbortController 保证可靠性
- `X-Accel-Buffering: no` 头（`api/routes/qa.py:87`）确保 nginx 不缓冲 SSE

---

## 实施顺序建议

1. **数据层**：db.py SCHEMA + MIGRATIONS（A.1/A.2）
2. **配置层**：schema.py QAConfig + store.py get_qa + app.yaml（B.6/B.7/B.8）
3. **核心层**：core/qa.py 日期注入 + 阶段事件（B.1）
4. **服务层**：services/qa_service.py 缓存三层 + CRUD（B.3）
5. **路由层**：api/routes/qa.py SSE 扩展 + 历史 API + api/schemas.py（B.2）
6. **钩子层**：services/notice_service.py 失效钩子（B.9）
7. **后端测试**：test_qa_cache.py（G.1）
8. **前端契约**：schema.ts + endpoints.ts + http.ts（D.1/D.2/D.3）
9. **前端状态**：useQaStore.ts（D.4）
10. **前端视图**：QaView.vue（D.5）
11. **前端验收**：G.2 手动测试

每步完成后单独跑测试，确保不破坏现有功能。

---

# 执行记录

## Plan A（数据库 Schema 设计）已落地

> 执行日期：2026-08-19
> 涉及文件：`storage/db.py`

### A.1 完成：`qa_history` 表 + 索引已写入 SCHEMA

在 `storage/db.py` 的 `SCHEMA` 常量 `tasks` 表之后（`idx_tasks_created` 后、`"""` 前）追加了 `qa_history` 建表 SQL 与 4 个索引，字段与索引设计与方案完全一致：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | 主键 |
| `question_text` | TEXT NOT NULL | 原始问题文本 |
| `question_hash` | TEXT NOT NULL | SHA-256(空白折叠后)，精确去重键 |
| `answer_text` | TEXT NOT NULL | 完整答案 |
| `sources_json` | TEXT | QAResult.sources 的 JSON 序列化 |
| `retrieved_chunks` | INTEGER DEFAULT 0 | 引用 chunk 数 |
| `embedding_blob` | BLOB | 512 维 embedding（float32 序列化） |
| `user_session_id` | TEXT | 前端会话标识，多端隔离 |
| `hit_count` | INTEGER DEFAULT 0 | 缓存命中次数 |
| `created_at` | TEXT NOT NULL | 首次写入时间 |
| `updated_at` | TEXT NOT NULL | 最近命中时间 |
| `expires_at` | TEXT NOT NULL | TTL 过期时间 |

索引：`idx_qa_history_hash`(UNIQUE) / `idx_qa_history_created` / `idx_qa_history_expires` / `idx_qa_history_session`。

### A.2 完成（含方案偏差说明）：迁移追加

**偏差**：方案原文建议把 `CREATE INDEX` 语句直接追加到 `_MIGRATIONS` 列表，但 `_migrate()`（`db.py:213-218`）对 `_MIGRATIONS` 每条语句按 `ALTER TABLE ... ADD COLUMN` 解析，直接追加 `CREATE INDEX` 会触发 `IndexError` 导致迁移崩溃。

**落地方式**：改用与现有 `idx_tasks_lock` 相同的模式，在 `_migrate()` 的 `idx_tasks_lock` 创建之后直接执行冗余保险索引创建（`db.py` _migrate 尾部）：

```python
# qa_history 冗余保险索引（SCHEMA 已幂等创建表与索引，这里兜底旧库漏建；表已在上面 SCHEMA 执行后存在）
conn.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_qa_history_hash ON qa_history(question_hash)"
)
```

SCHEMA 的 `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` 已覆盖此索引，该语句为纯冗余保险，幂等且不影响既有迁移流程。

### A.3 索引设计确认

未建联合索引，符合方案 H.5 单表 + 内存 cosine 决策；`idx_qa_history_expires` 供 TTL 批量清理、`idx_qa_history_session` 供多端隔离查询。

### 验证结果（临时库，`tempfile.mkdtemp()`）

使用系统 venv Python 在临时目录独立建库验证：

1. `qa_history` 表成功创建，12 个字段与方案一致
2. 4 个索引全部建立，`idx_qa_history_hash` 为唯一索引
3. 重复初始化幂等（`get_connection` 二次调用不报错）
4. UNIQUE 约束生效：同 `question_hash` 二次插入抛 `IntegrityError`

数据层（Plan A）验收通过，可进入下一阶段（Plan B：配置层 B.6/B.7/B.8）。

## Plan B（配置层 B.6/B.7/B.8）已落地

> 执行日期：2026-08-19
> 涉及文件：`config/schema.py`、`config/store.py`、`config/app.yaml`

### B.6 完成：`QAConfig` + `AppConfig.qa` 字段

在 `config/schema.py` 的 `ExtractConfig` 之后、`SchedulerConfig` 之前新增 `QAConfig`，字段与校验器与方案完全一致：

| 字段 | 默认值 | 校验 |
|---|---|---|
| `enable_cache` | True | - |
| `cache_ttl_hours` | 24 | 必须 >= 1 |
| `similarity_threshold` | 0.85 | 0.0~1.0 |
| `max_history` | 500 | 必须 >= 1 |
| `semantic_scan_limit` | 200 | 必须 >= 1 |

`AppConfig` 新增 `qa: QAConfig = Field(default_factory=QAConfig)`（`extract` 之后、`scheduler` 之前）。

### B.7 完成：`ConfigStore.get_qa()`

`config/store.py` 新增 `get_qa()` 方法（`get_extract()` 之后，参考 `store.py:137-138` 模式）。

**方案偏差（完善性补充）**：方案原文只要求新增 `get_qa()`，但 `store.py` 的 5 个保存方法（`save_models` / `save_providers` / `save_api_key` / `save_crawl` / `save_extract`）在重建 `AppConfig` 时若不带 `qa` 字段，会因 `default_factory` 把 `qa` 静默重置为默认值（例如保存模型配置后 `max_history` 会从自定义值掉回 500）。故按现有 `crawl`/`extract`/`scheduler` 透传模式，为 5 个保存方法统一补上 `qa=self._data.qa`；`export_for_ui` 同步补 `qa` 段，保持导出契约与其他配置段一致。

### B.8 完成：`config/app.yaml` 新增 `qa` 配置段

在 `extract:` 段之后追加：

```yaml
qa:
  enable_cache: true
  cache_ttl_hours: 24
  similarity_threshold: 0.85
  max_history: 500
  semantic_scan_limit: 200
```

注：`config/app.yaml` 已移出版本控制（2026 早期提交 gitignore），改动为本地生效。

### 验证结果（系统 venv Python + 真实/临时配置双路径）

1. 真实 `app.yaml` 加载：`get_qa()` 返回 `QAConfig`，5 个字段值全部正确，`export_for_ui()["qa"]` 存在
2. 校验器：`cache_ttl_hours=0` / `max_history=-1` / `semantic_scan_limit=0` / `similarity_threshold=1.5` / `=-0.1` 均抛 `ValueError`；`QAConfig()` 默认值正常
3. 保存透传：`save_crawl` / `save_models` 后 `qa` 配置不被重置
4. 向后兼容：无 `qa` 段的旧 `app.yaml` 加载回退默认 `QAConfig`（`default_factory` 生效）
5. 全量回归：`test_qa_sse_error.py`（5 项 PASS）、`test_api_smoke.py`（全部 PASS，配置模块 10 含 `qa` 段输出）均通过

配置层（Plan B）验收通过，可进入下一阶段（Plan C：核心层 core/qa.py 日期注入 + 阶段事件 B.1）。

## Plan C（核心层 B.1）已落地

> 执行日期：2026-08-19
> 涉及文件：`core/qa.py`

### B.1-改动1 完成：`QAAgent.__init__` 日期基准注入

顶部新增 `from datetime import date, timedelta`；`__init__` 签名在 `usage_cb` 后新增 `current_date: Optional[date] = None`，实例属性 `self._current_date = current_date or date.today()`。

### B.1-改动2 完成：`QA_INSTRUCTIONS` 扩展 + `_get_agent` 复合缓存 key

- `QA_INSTRUCTIONS` 末尾（`## 参考通知格式` 前）追加 `## 日期基准` 段，占位符 `{current_date}/{weekday}/{current_week_start}/{current_week_end}/{last_month_start}/{last_month_end}`，规则与方案一致（本周区间 / 最近=7 天 / 上月区间）。
- 新增 `_date_context()` 助手：计算 6 个占位符（周一为 `d - timedelta(days=d.weekday())`，上周日 +6；上月首日 + 上月末日 `本月1日 - 1天`，跨年 1 月回退到上年 12 月）；中文星期映射。
- `_get_agent` 缓存 key 改为 `(model, current_date_iso)` 复合 key，创建 Agent 时 `QA_INSTRUCTIONS.format(**self._date_context())` 填充；`self._agents` 类型注解同步改为 `dict[tuple[str, str], Agent]`。

### B.1-改动3 完成：`ask_stream` 阶段事件产出

产出事件二元组升级为 `("status", {stage, message, elapsed_ms})` / `("delta", str)` / `("done", QAResult)`：

- 阶段 1 retrieval：`("status", {"stage": "retrieval", "message": "检索中", "elapsed_ms": 0})` → `("status", {"stage": "retrieval", "message": f"已检索 {len(docs)} 段", ...})`
- 阶段 2 thinking：`("status", {"stage": "thinking", "message": "思考中", ...})`（拼 prompt 完成、进入 LLM 调用前）
- 阶段 3 generating：`("status", {"stage": "generating", "message": "生成回复中", ...})`（每个候选模型首个 delta 前）
- 阶段 4 done：保留现有 failover 切换逻辑与 `usage_cb` 透传，末尾产出 done

**方案偏差（空检索事件契约）**：方案原文 E 节「空检索流」描述为 `status(retrieval) → status(retrieval, "已检索 0 段") → done(兜底)`，但本次执行指令明确「空检索兜底直接 done」且 `test_api_smoke.py:749` 断言 `len(items) == 1 and items[0][0] == "done"`。落地采用**空检索不发任何 status、直接产出唯一 done**（`_retrieve` 空结果判断前置在首个 status yield 之前），保持既有测试契约不回归。非空检索才产出 retrieval→thinking→generating 阶段序列。

### 验证结果（系统 venv Python 回归）

1. `test_qa_sse_error.py`：5 项全部 PASS（中途断流 / 首 token 前失败均事件化 200 关流，契约未受影响）
2. `test_api_smoke.py`：全部 PASS（含第 12 节问答 SSE：delta→done 主链路、错误路径、空 question 422、**空索引仅产 done（12.4）**、index-stats）
3. 附加回归：`test_qa_citation.py` 全部 PASS（流式事件序列 `status×4 → delta×9 → done`，done.sources 过滤不变）；`test_qa_lost_middle.py` 全部 PASS（流式 Lost-in-the-Middle 布局不变）
4. 日期基准抽查：`2026-08-19`（周三）→ 本周 2026-08-17~08-23、上月 2026-07-01~07-31；`2026-01-15` 跨年 → 上月 2025-12-01~12-31；`QA_INSTRUCTIONS` 无未填充占位符
5. Agent 复合缓存 key 生效：不同 `current_date` 实例各持独立缓存 dict

核心层（Plan C）验收通过，可进入下一阶段（Plan D：服务层 services/qa_service.py 三层缓存 + CRUD B.3）。

## Plan D（服务层 B.3）已落地

> 执行日期：2026-08-19
> 涉及文件：`services/qa_service.py`

### B.3 完成：三层缓存 + 历史 CRUD + 失效钩子

按方案 B.3 完整改造 `services/qa_service.py`：

| 模块 | 落地内容 |
|---|---|
| embedding 序列化 | `_serialize_embedding`（`struct.pack("<{n}f")`）/ `_deserialize_embedding` / `_cosine_similarity`（纯 Python，方案 H.5 单表 + 内存 cosine） |
| 一级缓存 | `_check_cache`：`question_hash` 精确命中 + `expires_at` TTL 检查 + 命中时 `hit_count` 自增 / `updated_at` 刷新 |
| 二级缓存 | `_check_semantic`：`ORDER BY updated_at DESC LIMIT cfg.semantic_scan_limit` 窗口扫描，cosine ≥ `similarity_threshold` 命中，返回 `similarity` 字段 |
| 三级写入 | `_write_cache`：UPSERT（`ON CONFLICT(question_hash)` 覆盖答案/embedding/expires_at，hit_count 不重置）+ LRU 淘汰（`> max_history` 按 `updated_at ASC` 删最旧） |
| 流式入口 | `ask_stream(question, user_session_id=None)`：一级命中/二级命中 → yield `cache_hit` → `done`；未命中 → `QAAgent(current_date=datetime.now().date())` 透传 status/delta，done 在回写缓存后统一产出；兜底答案不缓存 |
| 历史 CRUD | `list_history`（分页 + session 隔离）/ `delete_history` / `clear_history` |
| 失效钩子 | `invalidate_cache_for_notice(notice_id)`（扫描 sources_json 删引用该通知的条目）/ `invalidate_all_cache` |
| 保留接口 | `ask` / `get_index_stats` / `index_notice` / `rebuild_index` / `remove_notice` 原样保留 |

### 方案偏差（3 处，均以「不破坏离线测试契约 + 缓存为附加能力」为准则）

1. **done 事件回吐**：方案 B.3 的 `ask_stream` 代码把 done 拦截存下后**没有再 yield 出去**，路由层收到的是 `delta,delta` 而无终态，前端会挂起。落地改为「done 先拦截 → 回写缓存 → 统一 `yield ("done", result)`」。**回写必须放在 yield done 之前**：路由层收到 done 即 `return`，generator 被关闭后代码不会继续执行。
2. **embedding 计算按需 + best-effort**：方案原码无条件 `get_embeddings().embed_query()`。落地改为：
   - 仅 `cfg.enable_cache` 时计算；
   - 二级检索前先 `_has_semantic_candidates()` 确认存在未过期条目（空表不白算 embedding）；
   - `_embed_question` 整体 try/except，失败返回 `None` 并 `logger.warning`，语义缓存降级（`embedding_blob` 写 NULL，精确 hash 缓存不受影响），**绝不上抛阻断问答链路**。
   - 理由：离线测试环境 embedding 配置指向不存在的本地模型路径（`sentence-transformers/bge`、`emb-model`），无条件计算会把 `test_api_smoke.py` 12 节打挂。
3. **语义扫描窗口使用配置**：方案 B.3 硬编码 `LIMIT 200`，落地改用 `cfg.semantic_scan_limit`，与 B.6 配置项对齐。

### 验证结果（系统 venv Python 回归 + 独立功能验证）

1. `test_qa_sse_error.py`：5 项全部 PASS（`[delta,error]` 中途断流 / 首 token 前失败均事件化 200 关流，契约未受影响）
2. `test_api_smoke.py`：全部 PASS，exit=0（含第 12 节问答 SSE 全部 PASS：delta,delta,done 主链路、错误路径、空 question 422、空索引仅产 done、index-stats）
3. 独立功能验证（临时库 `tempfile.mkdtemp()` 隔离，patch `core.qa.QAAgent` + `services.qa_service._embed_question`）19 项全 PASS：
   - 首次完整 QA 写缓存；同问题二次**精确命中**（不再调 LLM，事件序列 `cache_hit→done`）；hit_count 自增
   - 近义 embedding（cosine≈0.9997）**语义命中**；远义 embedding 不命中走完整 QA
   - TTL 过期（`expires_at` 改过去）不命中；**兜底答案不写缓存**；LRU（`max_history=2` 写 3 条保留 2 条）
   - `list_history` session 过滤 / `delete_history` / `clear_history` / `invalidate_cache_for_notice(1)` 删除引用该通知的缓存

服务层（Plan D）验收通过，可进入下一阶段（Plan E：路由层 api/routes/qa.py SSE 扩展 + 历史 API B.2 + api/schemas.py）。

## Plan E（路由层 B.2 + api/schemas.py）已落地

> 执行日期：2026-08-19
> 涉及文件：`api/routes/qa.py`、`api/schemas.py`

### B.2-改动1 完成：`event_stream` status/cache_hit 事件分发

`api/routes/qa.py` 的 `event_stream` 新增两个分支：

- `("status", dict)` → `_sse({"type": "status", **payload})`：阶段提示直接并入 status 事件（`{stage, message, elapsed_ms}`）。
- `("cache_hit", dict)` → `_sse({"type": "status", "stage": "cache_hit", "message": "命中缓存", **payload})`：service 层缓存命中事件归一化为 status.cache_hit，附 `history_id`/`similarity`/`elapsed_ms`。

`delta`/`done`/`error` 分支保持原契约不变（done 仍由 `_serialize_result` 做 as_source 转换后 `return` 关流）。

### B.2-改动2 完成：路由签名新增 `user_session_id`

`ask_stream` 增加 `user_session_id: Optional[str] = Query(None, ...)`，透传给 `qa_service.ask_stream(question, user_session_id=user_session_id)`，用于历史按会话隔离。

### B.2-改动4 完成：历史记录 REST API

新增 3 个端点（模型声明放在 `api/schemas.py`，符合本项目"契约集中声明"约定，与方案原文在 qa.py 内联模型略有偏差，契约字段完全一致）：

| 端点 | 说明 |
|---|---|
| `GET /qa/history` | 分页查询历史（`page` ge=1 / `page_size` ge=1 le=100 / `user_session_id` 可选），`response_model=QaHistoryPage` |
| `DELETE /qa/history/{history_id}` | 删除单条历史，返回 `{ok, id}` |
| `DELETE /qa/history` | 清空历史（传 session_id 只清该会话；不传清空全部，慎用），返回 `{ok, deleted}` |

### `api/schemas.py` 新增模型

在 `IndexStatsView` 之后、调度器段之前新增 `QaHistoryItem`（`id/question_text/answer_text/sources: list[QaSourceRef]/retrieved_chunks/created_at/hit_count`）与 `QaHistoryPage`（`items/total/page/page_size`），sources 复用已有 `QaSourceRef` 契约形态，与 SSE done 的 as_source 输出一致。

### 验证结果（系统 venv Python 回归 + 独立功能验证）

1. `test_qa_sse_error.py`：5 项全部 PASS（中途断流 / 首 token 前失败均事件化 200 关流，status 分支未影响契约）
2. `test_api_smoke.py`：全部 PASS，exit=0（含第 12 节问答 SSE 全部 PASS：delta,delta,done 主链路、错误路径、空 question 422、空索引仅产 done、index-stats）
3. 独立功能验证（临时库 `tempfile.mkdtemp()` 隔离）17 项全 PASS：
   - status 事件透传：mock QAAgent 产出 status×2 → delta → done，事件序列与字段（stage/elapsed_ms/message）正确
   - **真实缓存命中链路**：同问题二次请求 → `status.cache_hit`（含 history_id）→ `done` 直达
   - `GET /qa/history` 分页信封 + session 过滤 + 无 session 全量 + 分页参数 422
   - `DELETE /qa/history/{id}` 单条删除 ok / 不存在 ok=false / 非数字 422
   - `DELETE /qa/history` 按 session 清空只删该会话、他会话保留
   - `/openapi.json` 含 `/qa/history` 路径与 `QaHistoryPage`/`QaHistoryItem` 模型

路由层（Plan E）验收通过，可进入下一阶段（Plan F：钩子层 services/notice_service.py 失效钩子 B.9）。

## Plan F（钩子层 B.9）已落地

> 执行日期：2026-08-19
> 涉及文件：`services/notice_service.py`

### B.9 完成：三处缓存失效钩子注入

按方案 B.9 在 `notice_service.py` 三个 `if auto_index:` 块内 `add_notice` 成功后注入 `invalidate_cache_for_notice(notice_id)` 钩子：

| 注入点 | 位置 | 说明 |
|---|---|---|
| 改动位置 1 | `extract_batch._process_one` skip_llm 分支（`if auto_index:` 块） | 省 token 模式，`await asyncio.to_thread(invalidate_cache_for_notice, notice["id"])` |
| 改动位置 2 | `extract_batch._process_one` 正常提取分支（`if auto_index:` 块） | 完整提取路径，同样 `asyncio.to_thread` 包装 |
| 改动位置 3 | `extract_notice` 单条入口（`if auto_index:` 块） | 同步函数，直接调用 `invalidate_cache_for_notice(notice_id)` |

实现细节与方案一致：
- 钩子采用**函数内延迟导入** `from services.qa_service import invalidate_cache_for_notice`，避免 notice_service ↔ qa_service 顶层循环依赖。
- 钩子失败不阻断主流程：独立 `try/except Exception` 包住，失败仅 `logger.warning("QA 缓存失效钩子失败 notice_id=%s: %s")`，后续 `add_notice` 异常处理保持原样。
- 位置 3 为同步上下文（`extract_notice` 是同步函数），直接同步调用，无需 `asyncio.to_thread`。
- 钩子位于 `add_notice` 成功后（同一 try 块内）：索引成功即清缓存；`add_notice` 抛异常时钩子不执行（走原自动索引失败分支），与方案注入结构完全一致。

### 验证结果（系统 venv Python 回归 + 独立功能验证）

1. 独立功能验证（临时库 `tempfile.mkdtemp()` 隔离，patch `_get_vector_index`/`_extract_one_async`/`NoticeExtractor`）5 节全 PASS：
   - **改动位置 3**：预置引用 notice_id=N 的缓存 + 引用无关通知的缓存，`extract_notice(N)` 后前者被删、后者保留
   - **改动位置 1**：`extract_batch(skip_llm=True)` 命中 partial，引用该通知的缓存被删、无关缓存保留
   - **改动位置 2**：`extract_batch(skip_llm=False)` 正常提取成功，引用该通知的缓存被删、无关缓存保留
   - `auto_index=False`：钩子不触发，缓存保留（确认钩子只在 `auto_index` 开启时执行）
   - 钩子自身抛异常（patch `invalidate_cache_for_notice` 抛 `RuntimeError`）：主流程不受影响，批量提取仍成功，仅记录 warning
2. `test_qa_sse_error.py`：全部 PASS（错误事件化契约未受影响）
3. `test_api_smoke.py`：全部 PASS，exit=0（含第 12 节问答 SSE delta,done 主链路、错误路径、空索引仅产 done、index-stats）

钩子层（Plan F）验收通过，可进入下一阶段（Plan G：后端测试 test_qa_cache.py G.1）。

## Plan G.1（后端测试 test_qa_cache.py）已落地

> 执行日期：2026-08-19
> 涉及文件：`test_qa_cache.py`（新建）

### G.1 完成：新建 `test_qa_cache.py`（方案 G.1 全部 8 个用例）

按方案 G.1 测试约定新建 `test_qa_cache.py`，参考 `test_qa_sse_error.py:93-106` 的隔离模式：

- **临时库**：`tempfile.mkdtemp()` 专属目录 + `DB_PATH` 覆盖（不放 `data/`）
- **cleanup**：`_cleanup()` 用 `try/except OSError` 包住 `shutil.rmtree(ignore_errors=True)`
- **种子配置**：临时 config 目录 + `ConfigStore.reset_instance()` 后重新实例化；`qa:` 段显式 `max_history: 2` 供 LRU 用例使用

| 用例 | 验证内容 |
|---|---|
| 1. 精确命中 | TestClient 端到端：同问题二次请求 → `status.cache_hit` → `done`，`call_count` 保持 1（不再调 LLM） |
| 2. 语义命中 | patch `_embed_question` 返回近义向量（cosine≈0.9999995 ≥ 0.85）→ `cache_hit` 带 `similarity`；`hit_count` 自增为 1；远义向量不命中走完整 QA |
| 3. TTL 过期 | 写入后把 `expires_at` 改为过去 1 小时 → 不命中，走完整 QA（call_count=1） |
| 4. 失效钩子 | 预置引用 notice_id=1/2 两条缓存，`invalidate_cache_for_notice(1)` 返回 1，仅删前者 |
| 5. 日期基准注入 | 真实 `QAAgent(current_date=2026-08-19)` + patch `core.qa.AsyncOpenAI`（离线无凭据），`_get_agent` 的 instructions 含「当前日期：2026-08-19 / 本周 08-17~08-23 / 上月 07-01~07-31」且无未填充占位符 |
| 6. 阶段事件产出 | patch `core.qa.run_agent_stream` + `_get_agent`，事件序列 `status×4(status: retrieval,retrieval,thinking,generating) → delta×2 → done` |
| 7. 兜底不缓存 | 空检索仅产 `done`（兜底文案），done 后 `qa_history` 计数为 0 |
| 8. LRU 淘汰 | `max_history=2` 写 3 条（间隔 0.01s 保证 `updated_at` 严格递增）→ 仅剩 2 条且最早「问题 0」被淘汰 |

### 方案偏差（1 处，测试侧适配）

**用例 5 需 patch `core.qa.AsyncOpenAI`**：方案原文「mock QAAgent 检查 prompt 包含当前日期」，但直接构造真实 `QAAgent._get_agent` 会因离线环境无 API 凭据在 `AsyncOpenAI(api_key="")` 处抛 `OpenAIError: Missing credentials`。落地为 patch `core.qa.AsyncOpenAI` 客户端构造，`QA_INSTRUCTIONS.format(**self._date_context())` 与 `_get_agent` 其余路径原样走，同样验证「日期基准注入 instructions」这一实质断言。

### 验证结果（系统 venv Python）

1. `test_qa_cache.py`：8 节 27 项全部 PASS（精确/语义/TTL/失效钩子/日期注入/阶段事件/兜底不缓存/LRU）
2. `test_qa_sse_error.py`：5 项全部 PASS（错误事件化契约未受影响）
3. `test_api_smoke.py`：全部 PASS，exit=0（含第 12 节问答 SSE 全部 PASS：delta,done 主链路、错误路径、空 question 422、空索引仅产 done、index-stats）

后端测试（Plan G.1）验收通过，可进入下一阶段（Plan G.2：前端手动验收）。

## Plan D.1-D.5 + G.2（前端实现 + 手动验收）已落地

> 执行日期：2026-08-19
> 涉及文件：`frontend/src/api/schema.ts`、`frontend/src/api/endpoints.ts`、`frontend/src/api/http.ts`、`frontend/src/api/qa.ts`（新建）、`frontend/src/api/types.ts`（重导）、`frontend/src/stores/useQaStore.ts`、`frontend/src/views/QaView.vue`

### D.1-D.5 完成：前端契约/状态/视图三件套

按方案 D.1-D.5 完整落地前端改造（此前 Plan A-G 只覆盖后端，前端契约/状态/视图为本阶段补齐）：

| 模块 | 落地内容 |
|---|---|
| `schema.ts` | `QaStreamEvent` 新增 `status` 分支（`stage: retrieval\|thinking\|generating\|cache_hit` + `message/elapsed_ms/similarity?`）；新增 `QaHistoryItem` / `QaHistoryPage`（复用 `components['schemas']` 自动生成类型，经重导 openapi.json + `npm run gen:api` 对齐） |
| `endpoints.ts` | `qa` 段新增 `history` / `historyDelete(id)` / `historyClear` |
| `http.ts` | `del` 签名扩展为 `del<T>(url, params?, options?)`，与 `get` 对齐（D.3 方案要求） |
| `api/qa.ts`（新建） | `fetchQaHistory`（分页 + session）/ `deleteQaHistory` / `clearQaHistory` |
| `useQaStore.ts` | `SESSION_KEY`（localStorage UUID 会话隔离）+ `HISTORY_CACHE_KEY`（首屏缓存）；`loadHistory`（先读 localStorage 再拉后端回写）；`askStream` 注入 `user_session_id`、解析 `status` 事件并更新 `currentStage`/`currentStageStartedAt`；`removeHistory` / `clearAllHistory`；`itemToMessage` 按 `hit_count>0` 标记 `cached` |
| `QaView.vue` | 消息气泡阶段提示（`stageLabel` 映射 + 1s setInterval 实时计时）；历史侧栏 `n-drawer`（清空 / 删除单条 / 点击滚动到消息 / 缓存标记）；section-title 新增「历史 (N)」按钮；`onMounted` 加载历史 |

### 方案偏差（2 处，均以「缓存命中可被用户感知」为准则）

1. **缓存命中增加持久标记**：方案原文只在 `currentStage` 变化时显示「命中缓存」阶段文案，但缓存命中时 `status(cache_hit)` 与 `done` 在同一网络帧到达，气泡在无答案阶段（`v-else` 分支）尚未渲染「命中缓存」就已被 done 填充答案，用户实际上看不到缓存命中提示。落地在助手气泡内新增**持久 `.cache-badge`（「命中缓存」徽标）**，`msg.cached` 置位后常驻显示，同问题二次提问用户能明确感知命中。
2. **`removeHistory` 参数类型放宽**：方案 D.4 签名 `removeHistory(id: number)`，但视图 `@click="qa.removeHistory(msg.id)"` 传入的是 `string`（`hist-{id}` 前缀格式），`vue-tsc` 报 TS2345。落地放宽为 `string \| number`，内部 `parseInt(String(id).replace('hist-',''), 10)` 归一化，行为不变。

### 前端构建验证

- `vue-tsc -b && vite build`：**本阶段新增/修改文件 0 类型错误**（QaView/useQaStore/schema/endpoints/http/qa 全部通过）。
- 其余 7 个 TS 错误（App.vue `RocketOutline`、TaskListDrawer.vue 两处、NoticesView.vue 两处、SubscriptionsView.vue 两处）为**既有基线错误**（`git stash` 后复现，非本阶段引入，未在本次范围内修复）。
- `openapi.json` 已用系统 venv 从 `api.main:app` 重导（含 `/qa/history` 路径与 `QaHistoryItem`/`QaHistoryPage` schema），`npm run gen:api` 重新生成 `types.ts`。

### G.2 前端手动验收（本地真实环境逐项验证）

本地起真实后端（`uvicorn api.main:app`，复用 `data/notices.db` 7 条通知 + 26 chunks 向量索引）与前端（Vite dev server，代理 `/api`）后，用 headless Chromium 驱动 UI 逐项验证：

| 验收项 | 结果 | 验证方式 |
|---|---|---|
| 阶段提示 | PASS | 提问后气泡显示「检索中/思考中/生成回复中」阶段 + 实时计时（实测「生成回复中 0s→14s」递增） |
| 缓存命中 | PASS | 同问题二次提问 → 气泡常驻「命中缓存」徽标，几乎瞬时返回（前端 DOM 断言 `.cache-badge` 出现） |
| 历史侧栏 | PASS | 点「历史 (N)」按钮 → 左侧 drawer 展开，列出最近问答（`history-item` 数量正确） |
| 历史回溯 | PASS | 点历史项 → 主区滚动到对应消息（`msg-pair` top≈125px） |
| 删除历史 | PASS | 点历史项删除按钮 → 立即从列表移除（数量递减） |
| localStorage 缓存 | PASS | 提问后 `qa_history_cache` 写入；刷新页面首屏先显示缓存历史 |
| 跨设备隔离 | PASS（契约级） | 后端 `GET /qa/history?user_session_id=...` 按 session 过滤；不同 session 互不可见（Plan E 已验证，前端按 `SESSION_KEY` 生成独立 UUID 带参） |

另通过直连后端 SSE 验证端到端事件契约：新鲜问题事件序列 `status(retrieval) → status(已检索 6 段) → status(thinking) → status(generating) → delta* → done`；同问题二次 `status(cache_hit) → done`；且日期基准注入生效（回答含「当前日期为2026年8月19日，该报名已经截止」）。

### 回归（系统 venv Python）

1. `test_qa_sse_error.py`：5 项全部 PASS（错误事件化契约未受影响）
2. `test_api_smoke.py`：全部 PASS，exit=0（含第 12 节问答 SSE 全部 PASS：delta,done 主链路、错误路径、空 question 422、空索引仅产 done、index-stats）

前端实现（Plan D.1-D.5）与前端手动验收（Plan G.2）验收通过，三层 QA 缓存 + 历史 + 日期基准 + 阶段 SSE 事件方案全部落地。

## 前端修复（新对话语义 / 切页任务保活 / 历史详情）已落地

> 执行日期：2026-08-19
> 涉及文件：`frontend/src/stores/useQaStore.ts`、`frontend/src/views/QaView.vue`
> 起因：G.2 手动验收后反馈 ——「新对话」应新开对话并归档当前对话而非清空页面缓存；数据/历史加载与整体刷新有小问题；回答过程中切换页面任务状态丢失。

### 根因（单一数组双职责）

`useQaStore.history` 一个数组同时充当「当前对话（聊天区）」与「持久化历史（抽屉）」：

1. 「新对话」= `qa.history.length = 0`（旧 QaView `clearHistory`），清掉共享数组 → 抽屉随之清空、localStorage 缓存失效，需再点历史才重新拉取。
2. 路由 `:key="route.path"` + `mode="out-in"` 使组件切页即卸载；`onMounted → loadHistory()` 用后端历史整体覆盖 `history.value`，进行中的 SSE 消息被覆盖丢失；`abortController` 为组件局部变量，切回后取消按钮指向 null 失效。

### 改动

| 模块 | 落地内容 |
|---|---|
| `useQaStore.ts` | 拆分 `messages`（当前对话）与 `historyItems`（历史抽屉）；`loadHistory()` 只刷新 `historyItems`（localStorage 首屏缓存 + 后端回写），不再触碰聊天区；新增 `activeAbort`（store 持有当前流 AbortController）+ `cancel()`（切页返回后仍可取消）；新增 `startNewConversation()`（取消进行中流 → 清空 `messages` → `loadHistory()` 刷新抽屉，即为「归档到历史」，因为每条非兜底问答对后端在 done 时已持久化）；新增 `refreshHistory()`（done 后刷新抽屉）；`removeHistory`/`clearAllHistory` 改作用于 `historyItems` |
| `QaView.vue` | 聊天区改渲染 `qa.messages`，空态/新对话按钮判断改 `messages.length`；「新对话」改调 `qa.startNewConversation()`；「取消」改调 `qa.cancel()`；历史抽屉改渲染 `qa.historyItems`，历史计数改 `historyItems.length`；历史项点击改为打开右侧详情 `n-drawer`（`detailMsg`/`detailOpen`，展示问题 + Markdown 回答 + 引用来源，改动前为「滚动到对应消息」）；删除按钮 `@click.stop` 保留；`onMounted` 只 `loadHistory()` + 拉 indexStats，聊天区默认空白新对话 |

### 交互决策（用户确认）

1. 进入页面/刷新：聊天区为**空白新对话**，历史全部在抽屉。
2. 点击历史条目：**仅查看详情**（右侧详情抽屉），不改变当前聊天区。

### 前端构建验证

- `vue-tsc -b && vite build`：本阶段修改文件 0 类型错误；仍仅有 7 个既有基线错误（App.vue / TaskListDrawer / NoticesView / SubscriptionsView，`git stash` 后复现，非本阶段引入）。

### 验收（本地真实环境 + headless Chromium，16/16 PASS）

播种 session（`browser-seed-v4`，两条已持久化问答）后逐项验证：

| 验收项 | 结果 |
|---|---|
| 初始聊天区为空 + 历史按钮计数=2（播种数据）+ 抽屉 2 条 | PASS |
| 提问完成 → 聊天区=1；新对话 → 聊天区=0 且历史抽屉仍保留记录 | PASS |
| 历史条目点击 → 右侧详情抽屉显示问题+回答，聊天区不被改动 | PASS |
| 删除单条历史 → 抽屉计数-1 | PASS |
| 刷新 → 聊天区仍为空，历史按钮计数保留（localStorage 首屏缓存） | PASS |
| SPA 侧边栏菜单切页（首页↔智能问答，非整页 reload）进行中返回 → 消息仍在、流最终完成、无残留取消按钮 | PASS |

另确认：此前 acceptance_test4 的 F1 失败为测试方法缺陷（`page.goto` 整页 reload 会重置 Pinia store，而非 SPA 路由切换；已改用点击侧边栏菜单验证）。

### 回归（系统 venv Python）

1. `test_qa_sse_error.py`：5 项全部 PASS，exit=0（错误事件化契约未受影响）
2. `test_api_smoke.py`：全部 PASS，exit=0（含第 12 节问答 SSE：delta,done 主链路、错误路径、空 question 422、空索引仅产 done、index-stats）

前端状态/视图修复验收通过，新对话归档、切页任务保活、历史详情查看三个反馈点全部闭环。
