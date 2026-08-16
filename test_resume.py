"""模块 1.3 断点续跑（status 游标）的验收验证（离线、不依赖真实 LLM/网络）。

覆盖验收信号：
  1. 提取进行中 kill 进程 → 重启 → 只处理未完成的，已完成的不再调用 LLM；
  2. 两次运行后 token_usage 表中同一批通知只有一次提取计费记录（重启不重复计费）；
  3. 游标语义：raw → 提取；changed/raw → 索引（changed 通知被重置 raw 重新捞起，
     提取成功后退出 raw 游标、进入索引游标；索引 add_notice 幂等不产生重复 chunk）；
  4. --source 过滤发生在 LIMIT 之前（修复提取游标的截断缺口）。

用法：python test_resume.py
"""
import asyncio
import logging
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

import storage.db
from storage.db import (
    compute_content_hash,
    count_token_usage_by_task,
    get_connection,
    get_indexable_notice_ids,
    get_notices_by_status,
    log_llm_usage,
    update_extraction,
    update_notice_content,
)
from core.extractor import ExtractionOutcome
from core.models import NoticeExtraction
from extract import run_batch
from services.notice_service import extract_batch

TMP_DB = Path(__file__).parent / "data" / "test_resume.db"
TMP_CHROMA = Path(__file__).parent / "data" / "test_resume_chroma"

storage.db.DB_PATH = TMP_DB

URL = "https://resume.example/notice/1001.htm"


class SimulatedKill(BaseException):
    """模拟进程被杀：继承 BaseException，让 `except Exception` 无法吞掉（硬中断）。"""


class FakeExtractor:
    """假提取器：按通知计数调用、每次成功调用写一条计费记录，并可模拟中途被 kill。

    与真实 NoticeExtractor._call 的行为对齐：调用成功才写 token_usage 计费记录；
    被 kill 的那次调用既不写库也不计费（进程在 LLM 返回前就死了）。
    """

    def __init__(self, kill_after=None, fail_every=None):
        self.kill_after = kill_after  # 已完成多少次调用后，下一次调用模拟被杀
        self.fail_every = fail_every  # notice_id 每隔 N 返回 failed（锻炼 mark_failed 路径）
        self.completed = 0
        self._kill_fired = False
        self.calls_by_notice: dict[int, int] = defaultdict(int)

    async def extract_one(
        self,
        title: str,
        content: str,
        published_at=None,
        crawled_at=None,
        notice_id=None,
    ) -> ExtractionOutcome:
        nid = notice_id
        self.calls_by_notice[nid] += 1

        # 模拟崩溃：完成 kill_after 次后，下一次调用直接被"杀掉"（不计费、不写库）
        if (
            self.kill_after is not None
            and not self._kill_fired
            and self.completed >= self.kill_after
        ):
            self._kill_fired = True
            raise SimulatedKill(f"模拟进程在提取 notice_id={nid} 时被杀")

        self.completed += 1
        # 对齐真实 _call：只有调用成功才记账（首调 retry_count=0）
        self._bill(nid)

        if self.fail_every and nid % self.fail_every == 0:
            return ExtractionOutcome(status="failed", extraction=None, error="模拟失败")
        ext = NoticeExtraction(notice_type="competition", title=title, summary="模拟提取结果")
        return ExtractionOutcome(status="extracted", extraction=ext, error=None)

    def _bill(self, notice_id: int) -> None:
        conn = get_connection()
        try:
            log_llm_usage(
                conn,
                task="extraction",
                model="fake-model",
                input_tokens=100,
                output_tokens=50,
                success=True,
                retry_count=0,
                notice_id=notice_id,
            )
        finally:
            conn.close()


class FakeEmbeddings:
    """确定性 embedding 桩：相同文本 → 相同固定维度向量。"""

    _DIM = 8

    def embed_documents(self, texts):
        return [[float(abs(hash(t)) % 1000) / 1000.0] * self._DIM for t in texts]

    def embed_query(self, text):
        return [float(abs(hash(text)) % 1000) / 1000.0] * self._DIM


def reset_db():
    """删除临时库，下次 get_connection() 自动重建 SCHEMA（含 token_usage 表）。"""
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


def insert_notices(conn, rows):
    """批量插入 raw 通知。rows: list[(url, source, title)]"""
    for url, source, title in rows:
        conn.execute(
            """INSERT INTO notices (url, source, title, raw_content, crawled_at, status)
               VALUES (?, ?, ?, ?, ?, 'raw')""",
            (url, source, title, f"{title} 正文内容", "2026-01-01T00:00:00"),
        )
    conn.commit()


def run():
    reset_db()
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    print("== 0. token_usage 表由 SCHEMA 自动创建 ==")
    conn = get_connection()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(token_usage)")}
    check(
        "token_usage 列齐全",
        {"task", "model", "notice_id", "input_tokens", "output_tokens", "success", "retry_count", "error", "created_at"}
        <= cols,
        f"cols={sorted(cols)}",
    )
    conn.close()

    # ---------- Part A：extract.py run_batch 路径 ----------
    print("\n== A. extract.py run_batch：提取进行中 kill → 重启续跑 ==")
    conn = get_connection()
    insert_notices(conn, [(f"https://resume.example/notice/{i}.htm", "测试来源", f"通知{i}") for i in range(1, 11)])
    fake = FakeExtractor(kill_after=4)
    notices = get_notices_by_status(conn, "raw", limit=100)
    killed = False
    try:
        asyncio.run(run_batch(conn, notices, dry_run=False, limit=100, extractor=fake))
    except SimulatedKill:
        killed = True
    check("第一次运行在中途被模拟 kill 中断", killed)

    raw_after_1 = [r["id"] for r in get_notices_by_status(conn, "raw", limit=100)]
    check(
        "kill 后仅未完成项(5..10)仍为 raw，已完成(1..4)不再出现在游标",
        raw_after_1 == [5, 6, 7, 8, 9, 10],
        f"raw={raw_after_1}",
    )

    notices2 = get_notices_by_status(conn, "raw", limit=100)
    check("重启后捞起 6 条未完成项", len(notices2) == 6, f"n={len(notices2)}")
    asyncio.run(run_batch(conn, notices2, dry_run=False, limit=100, extractor=fake))

    non_raw = conn.execute("SELECT COUNT(*) FROM notices WHERE status != 'raw'").fetchone()[0]
    check("重启后全部通知非 raw", non_raw == 10, f"non_raw={non_raw}")

    for nid in range(1, 5):
        check(
            f"已完成通知 {nid} 全程只调 LLM 1 次（不再重复调用）",
            fake.calls_by_notice[nid] == 1,
            f"calls={fake.calls_by_notice[nid]}",
        )
    check(
        "被 kill 的通知 5 只被重调 1 次（at-least-once，可接受）",
        fake.calls_by_notice[5] == 2,
        f"calls={fake.calls_by_notice[5]}",
    )
    check("未处理通知 6..10 各调 1 次", all(fake.calls_by_notice[i] == 1 for i in range(6, 11)))

    billing = {
        r["notice_id"]: r["c"]
        for r in conn.execute(
            "SELECT notice_id, COUNT(*) AS c FROM token_usage WHERE task='extraction' GROUP BY notice_id"
        ).fetchall()
    }
    check(
        "计费：同一批 10 条通知各只有 1 条提取计费记录（重启不重复计费）",
        billing == {nid: 1 for nid in range(1, 11)},
        f"billing={billing}",
    )
    stat = count_token_usage_by_task(conn, "extraction")
    check("count_token_usage_by_task.calls == 10", stat["calls"] == 10, f"{stat}")
    conn.close()

    # ---------- Part B：scheduler 路径 services.notice_service.extract_batch ----------
    print("\n== B. services.extract_batch（scheduler 实际调用路径）崩溃续跑 ==")
    reset_db()
    conn = get_connection()
    insert_notices(conn, [(f"https://resume.example/notice/{i}.htm", "测试来源", f"通知{i}") for i in range(1, 11)])
    conn.close()

    fake2 = FakeExtractor(kill_after=2, fail_every=5)
    killed = False
    try:
        extract_batch(limit=100, auto_index=False, extractor=fake2, prefilter=False)
    except SimulatedKill:
        killed = True
    check("extract_batch 第一次运行被模拟 kill 中断", killed)

    conn = get_connection()
    raw_after = [r["id"] for r in get_notices_by_status(conn, "raw", limit=100)]
    check("kill 后仅 3..10 仍为 raw", raw_after == list(range(3, 11)), f"raw={raw_after}")
    conn.close()

    res = extract_batch(limit=100, auto_index=False, extractor=fake2, prefilter=False)
    check("extract_batch 重启后完成 8 条", res["processed"] == 8, f"processed={res['processed']}")

    conn = get_connection()
    non_raw = conn.execute("SELECT COUNT(*) FROM notices WHERE status != 'raw'").fetchone()[0]
    check("重启后全部通知非 raw", non_raw == 10, f"non_raw={non_raw}")
    check(
        "已完成通知 1 只调 LLM 1 次",
        fake2.calls_by_notice[1] == 1,
        f"calls={fake2.calls_by_notice[1]}",
    )
    check(
        "被 kill 的通知 3 共调 2 次（只多一次）",
        fake2.calls_by_notice[3] == 2,
        f"calls={fake2.calls_by_notice[3]}",
    )
    # 失败的也恰计 1 次
    billing = {
        r["notice_id"]: r["c"]
        for r in conn.execute(
            "SELECT notice_id, COUNT(*) AS c FROM token_usage WHERE task='extraction' GROUP BY notice_id"
        ).fetchall()
    }
    check(
        "extract_batch 路径同样：每条恰 1 次计费",
        billing == {nid: 1 for nid in range(1, 11)},
        f"billing={billing}",
    )
    conn.close()

    # ---------- Part C：游标语义 raw→提取，changed/raw→索引 ----------
    print("\n== C. 游标语义：changed→raw 重新捞起；提取后进索引游标；索引幂等 ==")
    reset_db()
    conn = get_connection()
    conn.execute(
        """INSERT INTO notices (url, source, title, raw_content, crawled_at, status, content_hash)
           VALUES (?, ?, ?, ?, ?, 'extracted', ?)""",
        (URL, "游标来源", "原始标题", "原始正文", "2026-01-01T00:00:00", compute_content_hash("原始正文")),
    )
    conn.commit()
    nid = conn.execute("SELECT id FROM notices WHERE url = ?", (URL,)).fetchone()["id"]

    update_notice_content(conn, URL, "新标题", "新正文", compute_content_hash("新正文"))
    raw_cursor = [r["id"] for r in get_notices_by_status(conn, "raw", limit=10)]
    check(
        "changed 通知被重置为 raw 并重新进入提取游标",
        raw_cursor == [nid],
        f"raw_cursor={raw_cursor}",
    )

    update_extraction(
        conn,
        nid,
        {"notice_type": "competition", "title": "新标题", "summary": "新", "key_dates": []},
        "extracted",
    )
    raw_cursor2 = [r["id"] for r in get_notices_by_status(conn, "raw", limit=10)]
    check("提取完成后退出 raw 游标", raw_cursor2 == [], f"raw_cursor={raw_cursor2}")
    indexable = get_indexable_notice_ids(conn)
    check("提取完成后进入索引游标(extracted)", nid in indexable, f"indexable={indexable}")
    conn.close()

    import storage.vectorstore as vs

    vs.get_embeddings = lambda: FakeEmbeddings()
    conn = get_connection()
    notice = dict(conn.execute("SELECT * FROM notices WHERE id = ?", (nid,)).fetchone())
    conn.close()

    index = vs.VectorIndex(persist_dir=TMP_CHROMA)
    info1 = index.add_notice(notice)
    info2 = index.add_notice(notice)  # 幂等：先删后加
    data = index._get_store()._collection.get(where={"notice_id": nid}, include=["documents"])
    docs = data.get("documents") or []
    check(
        "索引 add_notice 幂等：重复调用 chunk 数不变、无残留旧块",
        info1["chunks"] == info2["chunks"] and len(docs) == info1["chunks"],
        f"chunks={info1['chunks']} docs={len(docs)}",
    )
    try:
        index._get_store()._client.close()
    except Exception:
        pass

    # ---------- Part D：--source 过滤发生在 LIMIT 之前 ----------
    print("\n== D. --source 游标：过滤在 LIMIT 之前，不截断目标来源 ==")
    reset_db()
    conn = get_connection()
    insert_notices(
        conn,
        [
            ("https://d/a1", "A", "a1"),
            ("https://d/a2", "A", "a2"),
            ("https://d/b1", "B", "b1"),
            ("https://d/b2", "B", "b2"),
            ("https://d/b3", "B", "b3"),
            ("https://d/a3", "A", "a3"),
            ("https://d/a4", "A", "a4"),
        ],
    )
    a_rows = get_notices_by_status(conn, "raw", limit=3, source="A")
    check(
        "source=A limit=3 返回 A 的前 3 条（即使 B 夹在中间）",
        [r["title"] for r in a_rows] == ["a1", "a2", "a3"],
        f"titles={[r['title'] for r in a_rows]}",
    )
    b_rows = get_notices_by_status(conn, "raw", limit=2, source="B")
    check("source=B limit=2", [r["title"] for r in b_rows] == ["b1", "b2"])
    conn.close()

    cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


def cleanup():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass
    try:
        if TMP_CHROMA.exists():
            shutil.rmtree(TMP_CHROMA)
    except OSError:
        pass


if __name__ == "__main__":
    run()
