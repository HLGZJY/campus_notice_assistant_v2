"""批次 G.1 验收：QA 三层缓存 + 历史 + 日期基准 + 阶段 SSE 事件（离线，临时库隔离）。

覆盖（方案 G.1 的 8 个测试用例）：
  1. 精确命中：同问题二次请求 → status(cache_hit) → done，且不再调 LLM
  2. 语义命中：相似问题（非完全相同）→ cosine ≥ 阈值 → cache_hit（附 hit_count 自增、远义不命中）
  3. TTL 过期：expires_at 改为过去时间 → 不命中，走完整 QA
  4. 通知入库失效钩子：invalidate_cache_for_notice(1) 删除引用该通知的缓存
  5. 日期基准注入：QAAgent._get_agent 的 instructions 含「当前日期 / 本周 / 上月」
  6. 阶段事件产出：ask_stream 事件序列 status(retrieval/thinking/generating) → delta* → done
  7. 兜底答案不缓存：空检索 done 后 qa_history 无记录
  8. LRU 淘汰：max_history=2 写 3 条 → 最早一条被删

测试约定：
  - 临时库用 tempfile.mkdtemp() 专属目录，不放 data/
  - cleanup() 用 try/except OSError 包住
  - 参考 test_qa_sse_error.py 的隔离模式（DB_PATH 覆盖 + ConfigStore.reset_instance）

用法：C:\\Users\\Administrator\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\python.exe test_qa_cache.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

os.environ["APP_ENV"] = "test"

logging.basicConfig(level=logging.CRITICAL, format="%(levelname)s %(message)s")

failures: list[str] = []

FALLBACK_ANSWER = "根据已抓取的通知，没有找到相关信息。"


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def _cleanup(tmpdir: str) -> None:
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except OSError:
        pass


def _seed_config(config_dir: Path) -> None:
    schools = config_dir / "schools"
    schools.mkdir(parents=True, exist_ok=True)
    (config_dir / "app.yaml").write_text(
        """active_school: scuec
models:
  extraction:
    provider: opencode-zen
    model: model-a
  qa:
    provider: opencode-zen
    model: model-a
  todo:
    provider: opencode-zen
    model: model-a
  embedding:
    provider: local
    model: bge
providers:
  opencode-zen:
    name: opencode-zen
    base_url: http://localhost:1
    api_key_env: OPENCODE_API_KEY
  local:
    name: local
    base_url: ""
    api_key_env: ""
qa:
  enable_cache: true
  cache_ttl_hours: 24
  similarity_threshold: 0.85
  max_history: 2
  semantic_scan_limit: 200
""",
        encoding="utf-8",
    )
    (schools / "scuec.yaml").write_text(
        """name: 测试大学
code: scuec
sources:
- name: 教务处
  type: web
  list_url: http://example.com/tzgg.htm
  max_pages: 3
""",
        encoding="utf-8",
    )


def _fake_index(docs):
    """替身索引：search 直接返回预置 docs，绕过真实 Chroma。"""

    class _Fake:
        def __init__(self, docs):
            self.docs = docs

        def search(self, query, k=6, **kwargs):
            return self.docs

        def stats(self):
            return {}

    return _Fake(docs)


def _make_docs() -> list:
    from langchain_core.documents import Document

    return [
        Document(
            page_content="通知1的内容",
            metadata={
                "notice_id": 1,
                "title": "通知标题1",
                "notice_type": "竞赛",
                "source": "scuec",
                "url": "http://example.com/1",
                "deadline": "2026-12-01",
            },
        )
    ]


def main() -> None:
    from fastapi.testclient import TestClient

    from api.main import create_app
    from config.store import ConfigStore
    from core.qa import QAAgent, QAResult, SourceRef
    from services import qa_service
    from storage import db as db_mod
    from storage.db import compute_content_hash

    tmpdir = tempfile.mkdtemp(prefix="test_qa_cache_")
    db_path = Path(tmpdir) / "test_qa_cache.db"

    db_mod.DB_PATH = db_path
    db_mod.get_connection().close()

    config_dir = Path(tmpdir) / "config"
    _seed_config(config_dir)
    ConfigStore.reset_instance()
    ConfigStore.get_instance(config_dir)

    cfg = ConfigStore.get_instance().get_qa()

    async def _collect(agen):
        return [item async for item in agen]

    def _clear_history() -> None:
        conn = db_mod.get_connection()
        try:
            conn.execute("DELETE FROM qa_history")
            conn.commit()
        finally:
            conn.close()

    def _count_history() -> int:
        conn = db_mod.get_connection()
        try:
            return conn.execute("SELECT COUNT(*) AS c FROM qa_history").fetchone()["c"]
        finally:
            conn.close()

    try:
        # ---- 1. 精确命中：同问题二次请求 → status(cache_hit) → done，不再调 LLM ----
        print("== 1. 精确命中：同问题二次请求 → status(cache_hit) → done，不再调 LLM ==")
        call_count = [0]

        async def _fake_stream(question):
            call_count[0] += 1
            yield ("status", {"stage": "retrieval", "message": "检索中", "elapsed_ms": 0})
            yield ("delta", "答案 A")
            yield ("done", QAResult(answer="答案 A", sources=[], retrieved_chunks=1))

        with patch("core.qa.QAAgent") as FakeAgent:
            FakeAgent.return_value.ask_stream = _fake_stream
            with TestClient(create_app()) as client:

                def _stream_events(question: str) -> list:
                    with client.stream(
                        "GET",
                        "/api/v1/qa/ask/stream",
                        params={"question": question, "user_session_id": "sess-1"},
                    ) as r:
                        assert r.status_code == 200, f"status={r.status_code}"
                        return [
                            json.loads(line[6:])
                            for line in r.iter_lines()
                            if line.startswith("data: ")
                        ]

                ev1 = _stream_events("test q")
                check(
                    "首次请求无 cache_hit 事件",
                    not any(e.get("stage") == "cache_hit" for e in ev1),
                    f"{[e.get('type') for e in ev1]}",
                )
                check("首次请求调用 LLM（call_count=1）", call_count[0] == 1, f"count={call_count[0]}")

                ev2 = _stream_events("test q")
                types2 = [e.get("type") for e in ev2]
                check("二次请求事件序列 [status, done]", types2 == ["status", "done"], f"{types2}")
                check(
                    "二次请求含 status.cache_hit",
                    any(e.get("type") == "status" and e.get("stage") == "cache_hit" for e in ev2),
                    f"{ev2}",
                )
                check("二次请求不再调 LLM（call_count=1）", call_count[0] == 1, f"count={call_count[0]}")
                check("二次请求 done 携带缓存答案", ev2[-1].get("answer") == "答案 A", f"{ev2[-1]}")

        # ---- 2. 语义命中：相似问题（非完全相同）→ cosine ≥ 阈值 → cache_hit ----
        print("\n== 2. 语义命中：相似问题 → cosine ≥ 阈值 → cache_hit（含 hit_count / 远义不命中） ==")
        _clear_history()

        emb_a = [1.0, 0.0, 0.0, 0.0]
        qa_service._write_cache(
            "今天有什么比赛？",
            compute_content_hash("今天有什么比赛？"),
            emb_a,
            QAResult(answer="比赛答案 A", sources=[], retrieved_chunks=1),
            None,
            cfg,
        )

        similar_q = "今天有什么比赛可以报名？"
        emb_b = [1.0, 0.001, 0.0, 0.0]  # 近义：cosine ≈ 0.9999995 ≥ 0.85

        with patch("services.qa_service._embed_question", new=AsyncMock(return_value=emb_b)):
            events = asyncio.run(_collect(qa_service.ask_stream(similar_q)))
        check(
            "语义命中首事件为 cache_hit",
            events and events[0][0] == "cache_hit",
            f"{[t for t, _ in events]}",
        )
        check(
            "语义命中携带 similarity ≥ 0.85",
            events and events[0][1].get("similarity", 0) >= 0.85,
            f"{events[0][1] if events else None}",
        )
        check(
            "语义命中 done 携带缓存答案",
            events[-1][1].answer == "比赛答案 A",
            f"{events[-1][1] if events else None}",
        )

        conn = db_mod.get_connection()
        try:
            hit = conn.execute(
                "SELECT hit_count FROM qa_history WHERE question_hash = ?",
                (compute_content_hash("今天有什么比赛？"),),
            ).fetchone()
        finally:
            conn.close()
        check("语义命中后 hit_count 自增为 1", hit is not None and hit["hit_count"] == 1, f"{hit['hit_count'] if hit else None}")

        # 远义 embedding 不命中 → 走完整 QA
        async def _far_stream(question):
            yield ("delta", "远义答案")
            yield ("done", QAResult(answer="远义答案", sources=[], retrieved_chunks=0))

        with patch("core.qa.QAAgent") as FakeAgent, patch(
            "services.qa_service._embed_question", new=AsyncMock(return_value=[0.0, 1.0, 0.0, 0.0])
        ):
            FakeAgent.return_value.ask_stream = _far_stream
            events_far = asyncio.run(_collect(qa_service.ask_stream("完全无关的问题")))
        check(
            "远义不命中：无 cache_hit 且走完整 QA",
            not any(t == "cache_hit" for t, _ in events_far)
            and any(t == "delta" for t, _ in events_far),
            f"{[t for t, _ in events_far]}",
        )

        # ---- 3. TTL 过期：expires_at 改过去时间 → 不命中，走完整 QA ----
        print("\n== 3. TTL 过期：expires_at 改为过去 → 不命中，走完整 QA ==")
        _clear_history()

        ttl_q = "过期问题"
        ttl_hash = compute_content_hash(ttl_q)
        qa_service._write_cache(
            ttl_q,
            ttl_hash,
            None,
            QAResult(answer="过期缓存", sources=[], retrieved_chunks=0),
            None,
            cfg,
        )
        conn = db_mod.get_connection()
        try:
            conn.execute(
                "UPDATE qa_history SET expires_at = ? WHERE question_hash = ?",
                ((datetime.now() - timedelta(hours=1)).isoformat(), ttl_hash),
            )
            conn.commit()
        finally:
            conn.close()

        call_count[0] = 0

        async def _fresh_stream(question):
            call_count[0] += 1
            yield ("delta", "新鲜答案")
            yield ("done", QAResult(answer="新鲜答案", sources=[], retrieved_chunks=0))

        with patch("core.qa.QAAgent") as FakeAgent:
            FakeAgent.return_value.ask_stream = _fresh_stream
            events = asyncio.run(_collect(qa_service.ask_stream(ttl_q)))
        check(
            "过期不命中：无 cache_hit",
            not any(t == "cache_hit" for t, _ in events),
            f"{[t for t, _ in events]}",
        )
        check("过期走完整 QA（call_count=1）", call_count[0] == 1, f"count={call_count[0]}")
        check(
            "过期回答为新鲜答案",
            events[-1][1].answer == "新鲜答案",
            f"{events[-1][1] if events else None}",
        )

        # ---- 4. 通知入库失效钩子：invalidate_cache_for_notice(1) 删除引用该通知的缓存 ----
        print("\n== 4. 通知入库失效钩子：invalidate_cache_for_notice(1) ==")
        _clear_history()

        qa_service._write_cache(
            "q1",
            compute_content_hash("q1"),
            None,
            QAResult(answer="A", sources=[SourceRef(notice_id=1, title="t1")], retrieved_chunks=1),
            None,
            cfg,
        )
        qa_service._write_cache(
            "q2",
            compute_content_hash("q2"),
            None,
            QAResult(answer="B", sources=[SourceRef(notice_id=2, title="t2")], retrieved_chunks=1),
            None,
            cfg,
        )

        deleted = qa_service.invalidate_cache_for_notice(1)
        check("删除引用 notice_id=1 的缓存（返回 1）", deleted == 1, f"deleted={deleted}")

        conn = db_mod.get_connection()
        try:
            rows = conn.execute(
                "SELECT question_text FROM qa_history ORDER BY question_text"
            ).fetchall()
        finally:
            conn.close()
        remaining = [r["question_text"] for r in rows]
        check(
            "引用 notice_id=1 的缓存被删、引用 notice_id=2 的保留",
            remaining == ["q2"],
            f"{remaining}",
        )

        # ---- 5. 日期基准注入：QAAgent._get_agent 的 instructions 含日期基准 ----
        print("\n== 5. 日期基准注入：instructions 含当前日期 / 本周 / 上月 ==")
        agent5 = QAAgent(index=_fake_index(_make_docs()), current_date=date(2026, 8, 19), search_mode="vector")
        # 离线无凭据：只 patch AsyncOpenAI 客户端构造，_get_agent 其余路径（QA_INSTRUCTIONS.format）原样走
        with patch("core.qa.AsyncOpenAI"):
            inst = agent5._get_agent(agent5.models[0]).instructions
        check("instructions 含当前日期 2026-08-19", "当前日期：2026-08-19" in inst, "")
        check(
            "instructions 含本周区间 08-17~08-23",
            "本周" in inst and "2026-08-17" in inst and "2026-08-23" in inst,
            "",
        )
        check(
            "instructions 含上月区间 07-01~07-31",
            "上月" in inst and "2026-07-01" in inst and "2026-07-31" in inst,
            "",
        )
        check("instructions 无未填充占位符", "{current_date}" not in inst and "{weekday}" not in inst, "")

        # ---- 6. 阶段事件产出：status(retrieval/thinking/generating) → delta* → done ----
        print("\n== 6. 阶段事件产出：status(retrieval/thinking/generating) → delta* → done ==")
        agent6 = QAAgent(index=_fake_index(_make_docs()), current_date=date(2026, 8, 19), search_mode="vector")

        async def _fake_stream_llm(agent, prompt, **kwargs):
            yield "你好"
            yield "，答案"

        with patch("core.qa.run_agent_stream", side_effect=_fake_stream_llm), patch.object(
            agent6, "_get_agent", return_value=object()
        ):
            events6 = asyncio.run(_collect(agent6.ask_stream("最近有什么比赛？")))
        types6 = [t for t, _ in events6]
        check(
            "事件序列 status×4 → delta×2 → done",
            types6 == ["status", "status", "status", "status", "delta", "delta", "done"],
            f"{types6}",
        )
        stages6 = [p.get("stage") for t, p in events6 if t == "status"]
        check(
            "阶段序列 retrieval,retrieval,thinking,generating",
            stages6 == ["retrieval", "retrieval", "thinking", "generating"],
            f"{stages6}",
        )
        check(
            "status 事件均携带 message 与 elapsed_ms",
            all("message" in p and "elapsed_ms" in p for t, p in events6 if t == "status"),
            "",
        )
        done6 = [p for t, p in events6 if t == "done"][0]
        check("done 携带拼接答案", done6.answer == "你好，答案", f"{done6.answer!r}")

        # ---- 7. 兜底答案不缓存：空检索 done 后 qa_history 无记录 ----
        print("\n== 7. 兜底答案不缓存：空检索 done 后 qa_history 无记录 ==")
        _clear_history()

        async def _fallback_stream(question):
            yield ("done", QAResult(answer=FALLBACK_ANSWER, sources=[], retrieved_chunks=0))

        with patch("core.qa.QAAgent") as FakeAgent:
            FakeAgent.return_value.ask_stream = _fallback_stream
            events7 = asyncio.run(_collect(qa_service.ask_stream("没有通知的问题")))
        check("兜底流仅产出 done", [t for t, _ in events7] == ["done"], f"{[t for t, _ in events7]}")
        check("兜底回答文案正确", events7[0][1].answer == FALLBACK_ANSWER, "")
        check("qa_history 无记录（兜底答案未缓存）", _count_history() == 0, f"rows={_count_history()}")

        # ---- 8. LRU 淘汰：max_history=2 写 3 条 → 最早一条被删 ----
        print("\n== 8. LRU 淘汰：max_history=2 写 3 条 → 最早一条被删 ==")
        _clear_history()

        for i in range(3):
            qa_service._write_cache(
                f"问题 {i}",
                compute_content_hash(f"问题 {i}"),
                None,
                QAResult(answer=f"A{i}", sources=[], retrieved_chunks=0),
                None,
                cfg,
            )
            time.sleep(0.01)  # 保证 updated_at 严格递增，淘汰顺序确定

        conn = db_mod.get_connection()
        try:
            rows8 = conn.execute(
                "SELECT question_text FROM qa_history ORDER BY updated_at ASC"
            ).fetchall()
        finally:
            conn.close()
        check("写入 3 条后仅保留 2 条", len(rows8) == 2, f"rows={len(rows8)}")
        check(
            "最早写入的 '问题 0' 被淘汰",
            all(r["question_text"] != "问题 0" for r in rows8),
            f"{[r['question_text'] for r in rows8]}",
        )

        # ---- 9. 历史日志回填真正幂等：清空 qa_messages 后不从 qa_history 复活 ----
        print("\n== 9. 历史日志回填幂等：清空 qa_messages 后不从缓存复活 ==")
        # 前序用例已在 qa_history 写入若干缓存行；若回填仅按「qa_messages 为空」判断，
        # 清空后会再次从 qa_history 复活，导致「清空全部历史」失效。此处用持久标记
        # (qa_backfill_log) 保证只回填一次。
        qa_service.clear_history()  # 清空全部历史日志（不传 session）
        conn9 = db_mod.get_connection()
        try:
            after_clear = conn9.execute("SELECT COUNT(*) AS c FROM qa_messages").fetchone()["c"]
        finally:
            conn9.close()
        check("清空全部历史后 qa_messages=0", after_clear == 0, f"c={after_clear}")

        # 再触发一次 get_connection（模拟后续任意请求）→ 不应从 qa_history 复活
        conn9b = db_mod.get_connection()
        try:
            after_reconnect = conn9b.execute("SELECT COUNT(*) AS c FROM qa_messages").fetchone()["c"]
        finally:
            conn9b.close()
        check("再次连接后 qa_messages 仍为 0（未从缓存复活）", after_reconnect == 0, f"c={after_reconnect}")

        # 正向校验：全新库（仅 qa_history 有旧数据、qa_messages 为空）首次连接应按标记回填一次
        tmpdir2 = tempfile.mkdtemp(prefix="test_qa_backfill_")
        try:
            db_path2 = Path(tmpdir2) / "backfill.db"
            db_mod.DB_PATH = db_path2
            db_mod.get_connection().close()
            config_dir2 = Path(tmpdir2) / "config"
            _seed_config(config_dir2)
            ConfigStore.reset_instance()
            ConfigStore.get_instance(config_dir2)
            # 直接写一条旧缓存（qa_messages 此时为空、qa_backfill_log 尚未置位）
            conn_seed = db_mod.get_connection()
            try:
                conn_seed.execute(
                    """INSERT INTO qa_history
                       (question_text, question_hash, answer_text, sources_json,
                        retrieved_chunks, user_session_id, hit_count, created_at, updated_at, expires_at)
                       VALUES (?, ?, ?, ?, 0, ?, 0, ?, ?, ?)""",
                    ("旧问题", compute_content_hash("旧问题"), "旧答案", "[]", "sess-old",
                     datetime.now().isoformat(), datetime.now().isoformat(),
                     (datetime.now() + timedelta(hours=1)).isoformat()),
                )
                conn_seed.commit()
                # 重置回填标记，模拟「升级场景下 qa_messages 此时才出现、qa_history 已存在」的时序
                conn_seed.execute("DELETE FROM qa_backfill_log")
                conn_seed.commit()
            finally:
                conn_seed.close()
            # 首次连接触发回填
            hist_bf = qa_service.list_history(1, 50)
            check("全新库首次连接回填既有缓存历史", hist_bf["total"] == 1, f"total={hist_bf['total']}")
            check("回填状态为 answer", hist_bf["items"][0]["status"] == "answer",
                  str(hist_bf["items"][0]["status"]))
            # 清空后再连接不应复活
            qa_service.clear_history()
            hist_bf2 = qa_service.list_history(1, 50)
            check("回填库清空后不从缓存复活", hist_bf2["total"] == 0, f"total={hist_bf2['total']}")
        finally:
            db_mod.DB_PATH = db_path  # 还原，避免影响后续
            _cleanup(tmpdir2)
    finally:
        _cleanup(tmpdir)

    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    main()