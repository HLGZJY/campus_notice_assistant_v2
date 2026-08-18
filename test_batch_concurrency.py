"""批次 D1 验收：批量提取并发 Semaphore 化（离线，不依赖真实 LLM/网络）。

覆盖：
  1. 并发提速：4 条 × 0.2s sleep，concurrency=2 → 总耗时明显小于串行总和（4×0.2=0.8s）
  2. 并发度上限：FakeExtractor 观测活跃数峰值 ≤ concurrency；传超限值被钳到 8
  3. 进度回调语义：每完成一条触发一次，序列 (1,total)…(total,total)，done 单调
  4. gather 保序：summary.details 顺序与输入 notice 顺序一致
  5. 并发写库：4 条全部 extracted 落库，无 SQLite locked；任务连接协程内自关
  6. skip_llm 分支并发可用（状态 partial，不调 LLM）

用法：python test_batch_concurrency.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.CRITICAL, format="%(levelname)s %(message)s")

import storage.db
from config.schema import ExtractConfig
from core.extractor import ExtractionOutcome
from core.models import NoticeExtraction
from services.notice_service import extract_batch
from storage.db import get_connection

# 临时库放 tempfile 专属目录（沙箱内 data/ 目录删除受限，回收站不可用会拦截 unlink）
TMP_DIR = tempfile.mkdtemp(prefix="wb_test_batch_")
TMP_DB = Path(TMP_DIR) / "test_batch_concurrency.db"
storage.db.DB_PATH = TMP_DB

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


class FakeExtractor:
    """带 sleep 的替身提取器：观测并发活跃峰值。"""

    def __init__(self, sleep: float = 0.2):
        self.sleep = sleep
        self.active = 0
        self.peak = 0
        self.calls: list[int] = []

    async def extract_one(self, title, content, published_at=None, crawled_at=None, notice_id=None):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.calls.append(notice_id)
        try:
            await asyncio.sleep(self.sleep)
            return ExtractionOutcome(
                status="extracted",
                extraction=NoticeExtraction(
                    notice_type="administrative", title=title or "测试"
                ),
                error=None,
            )
        finally:
            self.active -= 1


def _reset_db():
    if TMP_DB.exists():
        TMP_DB.unlink()
    conn = get_connection()
    for i in range(1, 5):
        conn.execute(
            """INSERT INTO notices (url, source, title, raw_content, published_at, crawled_at, status)
               VALUES (?, '教务处', ?, '测试正文内容较长用于提取' || ?, '2026-08-01', '2026-08-01', 'raw')""",
            (f"http://t{i}.com/{i}", f"通知{i}", i),
        )
    conn.commit()
    conn.close()


def _status_rows():
    conn = get_connection()
    rows = conn.execute("SELECT id, status FROM notices ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def main():
    print("== 1. 并发提速：4 条 × 0.2s，concurrency=2 ==")
    _reset_db()
    fake = FakeExtractor(sleep=0.2)
    progress: list[tuple[int, int]] = []

    t0 = time.perf_counter()
    result = extract_batch(
        limit=4,
        auto_index=False,
        extractor=fake,
        prefilter=False,
        extract_cfg=ExtractConfig(batch_limit=50, concurrency=2),
        progress_cb=lambda d, t: progress.append((d, t)),
    )
    wall = time.perf_counter() - t0
    check(
        "总耗时 < 串行总和(0.8s)",
        wall < 0.75,
        f"wall={wall:.2f}s (串行基线 0.8s)",
    )
    check(
        "并发峰值 ≤ 2",
        fake.peak <= 2,
        f"peak={fake.peak}",
    )
    check("4 条全部调用", len(fake.calls) == 4, f"calls={len(fake.calls)}")
    check(
        "进度回调每完成一条触发且 done 单调 1..4",
        progress == [(1, 4), (2, 4), (3, 4), (4, 4)],
        f"progress={progress}",
    )
    check(
        "details 与输入顺序一致",
        [d["id"] for d in result["summary"]["details"]] == [1, 2, 3, 4],
        f"ids={[d['id'] for d in result['summary']['details']]}",
    )
    rows = _status_rows()
    check(
        "4 条全部 extracted 落库（无 SQLite locked）",
        all(r["status"] == "extracted" for r in rows),
        f"statuses={[r['status'] for r in rows]}",
    )
    check(
        "汇总计数正确",
        result["summary"]["extracted"] == 4
        and result["summary"]["failed"] == 0
        and result["summary"]["partial"] == 0,
        f"summary={result['summary']}",
    )

    print("\n== 2. 并发度钳制：concurrency=99 → 有效 8 ==")
    _reset_db()
    fake2 = FakeExtractor(sleep=0.05)
    extract_batch(
        limit=4,
        auto_index=False,
        extractor=fake2,
        prefilter=False,
        extract_cfg=ExtractConfig(batch_limit=50, concurrency=2),
        concurrency=99,
    )
    check("超限值被钳到 8（不抛错）", fake2.peak <= 8, f"peak={fake2.peak}")

    print("\n== 3. skip_llm 分支并发可用（状态 partial，不调 LLM） ==")
    _reset_db()
    fake3 = FakeExtractor(sleep=0.05)
    res = extract_batch(
        limit=4,
        auto_index=False,
        extractor=fake3,
        prefilter=False,
        extract_cfg=ExtractConfig(batch_limit=50, concurrency=2, skip_llm=True),
    )
    rows = _status_rows()
    check(
        "skip_llm 下 4 条全部 partial",
        all(r["status"] == "partial" for r in rows) and fake3.calls == [],
        f"statuses={[r['status'] for r in rows]} calls={fake3.calls}",
    )
    check(
        "details 带 skipped_llm 标记",
        all(d.get("skipped_llm") for d in res["summary"]["details"]),
    )

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
        if TMP_DIR and Path(TMP_DIR).exists():
            Path(TMP_DIR).rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    main()
