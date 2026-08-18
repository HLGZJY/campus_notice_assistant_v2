"""批次 D2 验收：Agent 压测脚本（耗时 + Token 统计 + 重试归因）。

用法：
    python benchmark_agent.py --task extraction --samples 50
    python benchmark_agent.py --task todo --samples 20 --concurrency 2
    python benchmark_agent.py --task qa --samples 10
    python benchmark_agent.py --task extraction --dry-run   # 只调 LLM 不写库

输出：
  - 控制台表格：avg/p50/p95 耗时、成功率、平均重试次数、
    total_input_tokens / total_output_tokens、单条平均 Token
  - data/benchmark_<task>_<ts>.json：完整明细 + 汇总（含 created_at，便于与优化前 baseline 对比）

Token 归因：
  - usage_cb（run_agent 成功后回调）逐样本累计 input/output tokens 与成功调用次数；
  - extraction/todo 的 LLM 调用次数以 token_usage 表按 notice_id 精确统计
    （成功/失败每次调用各记一行），重试次数 = 调用次数 - 1；
  - qa 无 notice_id 关联，重试次数按成功调用次数 - 1 估算（首调用失败不触发 usage_cb，
    该场景下会低估；日志仍以 token_usage 全量计量为准）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

# Windows 控制台默认 cp1252 无法打印中文，统一用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

from core.extractor import NoticeExtractor
from core.qa import QAAgent
from core.todo import TodoGenerator
from storage.db import get_connection

DATA_DIR = Path(__file__).parent / "data"

# qa 任务的内置问题样本（无检索集时循环使用）
DEFAULT_QA_QUESTIONS = [
    "最近有哪些竞赛可以报名？",
    "有哪些奖学金通知？",
    "最近有什么讲座？",
    "有哪些比赛的结果公布了？",
    "学校近期有什么招聘或实习机会？",
    "帮我总结近一周的关键通知",
]


class UsageCollector:
    """逐样本累计 usage_cb 回调（每次成功 LLM 调用触发一次）。"""

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    def record(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls += 1


def _llm_attempts(notice_id: int, task: str, since: str) -> tuple[int, int]:
    """按 notice_id + 时间窗精确统计 LLM 调用次数与失败次数（token_usage 每次调用记一行）。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failed "
            "FROM token_usage WHERE notice_id=? AND task=? AND created_at >= ?",
            (notice_id, task, since),
        ).fetchone()
    finally:
        conn.close()
    n = int(row["n"] or 0)
    failed = int(row["failed"] or 0)
    return n, failed


def _pick_notices(limit: int, prefer_status: str | None = None) -> list[dict]:
    """取有正文的通知样本（优先指定状态；不足时放宽）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM notices WHERE raw_content IS NOT NULL AND length(raw_content) > 0 "
            "ORDER BY id DESC LIMIT ?",
            (limit * 4,),
        ).fetchall()
    finally:
        conn.close()
    items = [dict(r) for r in rows]
    if prefer_status:
        ordered = [n for n in items if n.get("status") == prefer_status] + [
            n for n in items if n.get("status") != prefer_status
        ]
        items = ordered
    return items[:limit]


async def _run_extraction(notices: list[dict], dry_run: bool, concurrency: int, since: str) -> list[dict]:
    """extraction 压测：每样本独立 NoticeExtractor + usage_cb 归因；非 dry-run 写库。"""
    from storage.db import close_task_connection, get_task_connection, mark_failed, update_extraction

    sem = asyncio.Semaphore(max(1, min(8, concurrency)))
    results: list[dict] = []

    async def _one(notice: dict) -> dict:
        collector = UsageCollector()
        extractor = NoticeExtractor(usage_cb=collector.record)
        t0 = time.perf_counter()
        try:
            outcome = await extractor.extract_one(
                title=notice["title"],
                content=notice["raw_content"] or "",
                published_at=notice.get("published_at"),
                crawled_at=notice.get("crawled_at"),
                notice_id=notice["id"],
            )
            status = outcome.status
            error = outcome.error
            if not dry_run:
                conn2 = get_task_connection()
                if status == "failed" or outcome.extraction is None:
                    mark_failed(conn2, notice["id"], outcome.error or "提取失败")
                else:
                    update_extraction(conn2, notice["id"], outcome.extraction.model_dump(), status)
        except Exception as e:
            status = "failed"
            error = f"{type(e).__name__}: {e}"
        finally:
            close_task_connection()
        latency = time.perf_counter() - t0
        attempts, failed_attempts = _llm_attempts(notice["id"], "extraction", since)
        return {
            "id": notice["id"],
            "title": notice["title"],
            "status": status,
            "error": error,
            "latency": latency,
            "input_tokens": collector.input_tokens,
            "output_tokens": collector.output_tokens,
            "attempts": attempts,
            "failed_attempts": failed_attempts,
        }

    async def _guarded(notice: dict) -> dict:
        async with sem:
            return await _one(notice)

    for r in await asyncio.gather(*(_guarded(n) for n in notices)):
        results.append(r)
    return results


async def _run_todo(notices: list[dict], concurrency: int, since: str) -> list[dict]:
    """todo 压测：每样本独立 TodoGenerator.generate_one + usage_cb 归因（不写库）。"""
    sem = asyncio.Semaphore(max(1, min(8, concurrency)))
    results: list[dict] = []

    async def _one(notice: dict) -> dict:
        collector = UsageCollector()
        generator = TodoGenerator(usage_cb=collector.record)
        t0 = time.perf_counter()
        try:
            items = await generator.generate_one(dict(notice))
            status = "generated" if items else "none"
            error = None
        except Exception as e:
            status = "failed"
            error = f"{type(e).__name__}: {e}"
        latency = time.perf_counter() - t0
        attempts, failed_attempts = _llm_attempts(notice["id"], "todo", since)
        return {
            "id": notice["id"],
            "title": notice["title"],
            "status": status,
            "error": error,
            "latency": latency,
            "input_tokens": collector.input_tokens,
            "output_tokens": collector.output_tokens,
            "attempts": attempts,
            "failed_attempts": failed_attempts,
        }

    async def _guarded(notice: dict) -> dict:
        async with sem:
            return await _one(notice)

    for r in await asyncio.gather(*(_guarded(n) for n in notices)):
        results.append(r)
    return results


async def _run_qa(questions: list[str], concurrency: int) -> list[dict]:
    """qa 压测：每样本独立 QAAgent.ask + usage_cb 归因（无写库语义）。"""
    sem = asyncio.Semaphore(max(1, min(8, concurrency)))
    results: list[dict] = []

    async def _one(question: str) -> dict:
        collector = UsageCollector()
        agent = QAAgent(usage_cb=collector.record)
        t0 = time.perf_counter()
        try:
            result = await agent.ask(question)
            status = "ok" if result.answer else "empty"
            error = None
        except Exception as e:
            status = "failed"
            error = f"{type(e).__name__}: {e}"
        latency = time.perf_counter() - t0
        return {
            "question": question[:60],
            "status": status,
            "error": error,
            "latency": latency,
            "input_tokens": collector.input_tokens,
            "output_tokens": collector.output_tokens,
            "attempts": collector.calls,
            "failed_attempts": 0,
        }

    async def _guarded(question: str) -> dict:
        async with sem:
            return await _one(question)

    for r in await asyncio.gather(*(_guarded(q) for q in questions)):
        results.append(r)
    return results


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p
    f = int(k)
    c = f + 1 if f + 1 < len(values) else f
    return values[f] + (values[c] - values[f]) * (k - f)


def summarize(task: str, results: list[dict]) -> dict:
    latencies = [r["latency"] for r in results]
    ok = [r for r in results if r["status"] not in ("failed",)]
    total_in = sum(r["input_tokens"] for r in results)
    total_out = sum(r["output_tokens"] for r in results)
    attempts = [max(1, r["attempts"]) for r in results]
    return {
        "task": task,
        "samples": len(results),
        "success_count": len(ok),
        "success_rate": len(ok) / len(results) if results else 0.0,
        "avg_latency": statistics.mean(latencies) if latencies else 0.0,
        "p50_latency": _percentile(latencies, 0.5),
        "p95_latency": _percentile(latencies, 0.95),
        "avg_attempts": statistics.mean(attempts) if attempts else 0.0,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "avg_input_tokens_per_sample": total_in / len(results) if results else 0.0,
        "avg_output_tokens_per_sample": total_out / len(results) if results else 0.0,
        "total_tokens": total_in + total_out,
    }


def report(task: str, summary: dict) -> None:
    print("\n" + "=" * 72)
    print(f"Agent 压测报告 — task={task}  samples={summary['samples']}")
    print("=" * 72)
    print(f"  成功率            {summary['success_rate']:.1%}  ({summary['success_count']}/{summary['samples']})")
    print(f"  平均耗时          {summary['avg_latency'] * 1000:.1f} ms")
    print(f"  P50 耗时          {summary['p50_latency'] * 1000:.1f} ms")
    print(f"  P95 耗时          {summary['p95_latency'] * 1000:.1f} ms")
    print(f"  平均 LLM 调用次数 {summary['avg_attempts']:.2f} 次/条")
    print(f"  total_input_tokens   {summary['total_input_tokens']}")
    print(f"  total_output_tokens  {summary['total_output_tokens']}")
    print(f"  单条平均 input       {summary['avg_input_tokens_per_sample']:.1f}")
    print(f"  单条平均 output      {summary['avg_output_tokens_per_sample']:.1f}")
    print("  " + "-" * 68)
    print(f"  Token 合计         {summary['total_tokens']}")
    print("=" * 72)
    print("对照验收：平均耗时应较优化前（串行 ~7s/条）下降 >60%；input_tokens 总量下降 >30%。")


def main():
    parser = argparse.ArgumentParser(description="Agent 压测（耗时 + Token 统计）")
    parser.add_argument("--task", choices=["extraction", "todo", "qa"], default="extraction")
    parser.add_argument("--samples", type=int, default=20, help="样本条数")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数（默认 3，上限 8）")
    parser.add_argument("--dry-run", action="store_true", help="只调 LLM 不写库（qa 无写库语义）")
    parser.add_argument("--output", type=str, default=None, help="结果 JSON 路径（默认 data/benchmark_<task>_<ts>.json）")
    args = parser.parse_args()

    if args.samples < 1:
        parser.error("--samples 至少为 1")

    run_start = datetime.now().isoformat(timespec="seconds")

    if args.task == "extraction":
        notices = _pick_notices(args.samples)
        if not notices:
            print("!! 数据库无可用通知样本（需先抓取）")
            return
        print(f"样本: {len(notices)} 条通知（dry_run={args.dry_run}）")
        results = asyncio.run(_run_extraction(notices, args.dry_run, args.concurrency, run_start))
    elif args.task == "todo":
        notices = _pick_notices(args.samples, prefer_status="extracted")
        if not notices:
            print("!! 数据库无可用通知样本（需先提取）")
            return
        print(f"样本: {len(notices)} 条已提取通知")
        results = asyncio.run(_run_todo(notices, args.concurrency, run_start))
    else:
        questions = (DEFAULT_QA_QUESTIONS * (args.samples // len(DEFAULT_QA_QUESTIONS) + 1))[: args.samples]
        print(f"样本: {len(questions)} 个问题")
        results = asyncio.run(_run_qa(questions, args.concurrency))

    for r in results:
        print(
            f"  {r.get('id', r.get('question', '?')):<8} [{r['status']:<9}] "
            f"{r['latency'] * 1000:7.1f}ms  in={r['input_tokens']:>6} out={r['output_tokens']:>6} "
            f"calls={r['attempts']}"
            + (f"  error: {r['error'][:80]}" if r.get("error") else "")
        )

    summary = summarize(args.task, results)
    report(args.task, summary)

    output_path = args.output or (DATA_DIR / f"benchmark_{args.task}_{datetime.now():%Y%m%d_%H%M%S}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"created_at": datetime.now().isoformat(timespec="seconds"), "summary": summary, "results": results}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入: {output_path}")


if __name__ == "__main__":
    main()
