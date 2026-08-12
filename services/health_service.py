"""模块 4.2 每日体检 + 7 天自运行汇总（服务层）。

不调用 LLM、不做任何写库，只基于现有 SQLite 表（crawl_log / scheduler_log /
token_usage / notices）只读计算健康指标并落盘报告，供"7 天自运行终检"做验收证据。

指标口径（模块 4.2 约定）：
    - 抓取成功率（主）  = (Σ discovered − Σ failed) / Σ discovered   （crawl_log，按日报表窗口）
    - 提取失败率（主）  = failed / (extracted + partial + failed)     （notices.extracted_at 落在窗口）
    - 提取失败率（辅）  = token_usage task=extraction 的调用失败率
    - token 消耗        = token_usage 按任务汇总
    - 异常日志          = scheduler_log 窗口内 status='failed' 的运行
    - 运行连续性        = 窗口内 crawl 运行数 + 相邻最大间隔缺口（证明无人干预连续运行）

用法（经 check_db.py 调用）：
    python check_db.py --report              # 体检昨日，落 data/health/daily/
    python check_db.py --report --date 2026-08-10
    python check_db.py --summary --days 7    # 7 天自运行验收，落 data/health/summary/
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from storage.db import (
    get_connection,
    get_crawl_log_stats,
    get_extraction_status_stats,
    get_run_gaps,
    get_scheduler_failed_runs,
    get_token_usage_stats,
)

logger = logging.getLogger(__name__)

HEALTH_DIR = Path(__file__).parent.parent / "data" / "health"
DAILY_DIR = HEALTH_DIR / "daily"
SUMMARY_DIR = HEALTH_DIR / "summary"
SPOTCHECK_DIR = HEALTH_DIR / "spotcheck"
CRASH_DIR = HEALTH_DIR / "crash"

# 验收阈值（模块 4.2）
CRAWL_SUCCESS_THRESHOLD = 0.90
EXTRACTION_FAILURE_THRESHOLD = 0.10
# 运行连续性容差：最大相邻间隔超过 interval * 2 分钟即视为运行中断（多轮 misfire）
GAP_TOLERANCE_MULTIPLIER = 2.0
DEFAULT_INTERVAL_MINUTES = 60


def _day_window(d: date) -> tuple[str, str]:
    """返回某天的 [start, end) ISO 窗口。"""
    start = datetime(d.year, d.month, d.day).isoformat()
    end = (d + timedelta(days=1)).isoformat()
    return start, end


def _crawl_interval() -> int:
    """读取配置的抓取间隔（分钟）；读取失败回退默认值。"""
    try:
        from config.store import ConfigStore

        return ConfigStore.get_instance().get_crawl().interval_minutes
    except Exception:  # noqa: BLE001 —— 体检不因配置读取失败中断
        logger.warning("读取 crawl.interval_minutes 失败，使用默认 %d 分钟", DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES


def _fmt_rate(rate: Optional[float]) -> str:
    return f"{rate:.1%}" if rate is not None else "N/A(无数据)"


# ---------- 每日体检 ----------


def run_daily_health_check(report_date: Optional[date] = None) -> dict:
    """执行一次每日体检，返回报告 dict 并落盘 data/health/daily/。

    Args:
        report_date: 被体检的日期（默认昨天，保证 03:00 运行时覆盖完整昨日）。
    """
    d = report_date or (date.today() - timedelta(days=1))
    start, end = _day_window(d)
    interval = _crawl_interval()

    conn = get_connection()
    try:
        crawl = get_crawl_log_stats(conn, start, end)
        extraction = get_extraction_status_stats(conn, start, end)
        token = get_token_usage_stats(conn, start, end)
        failed_runs = get_scheduler_failed_runs(conn, start, end)
        gaps = get_run_gaps(conn, "crawl", start, end)
    finally:
        conn.close()

    # LLM 级提取失败率（辅口径）
    llm_ext = next((r for r in token["rows"] if r["task"] == "extraction"), None)
    llm_calls = llm_ext["calls"] if llm_ext else 0
    llm_failed = llm_ext["failed"] if llm_ext else 0
    llm_failure_rate = llm_failed / llm_calls if llm_calls > 0 else None

    # 判定
    crawl_pass = crawl["success_rate"] is None or crawl["success_rate"] >= CRAWL_SUCCESS_THRESHOLD
    crawl_reason = (
        f"成功率 {_fmt_rate(crawl['success_rate'])} (阈值 {CRAWL_SUCCESS_THRESHOLD:.0%})"
        + (f"，来源 {crawl['sources_with_errors']}/{crawl['sources_total']} 有错误" if crawl["sources_with_errors"] else "")
    )
    ext_pass = extraction["failure_rate"] is None or extraction["failure_rate"] < EXTRACTION_FAILURE_THRESHOLD
    ext_reason = (
        f"提取失败率 {_fmt_rate(extraction['failure_rate'])} (阈值 <{EXTRACTION_FAILURE_THRESHOLD:.0%})"
        f"，LLM 级失败率 {_fmt_rate(llm_failure_rate)}"
    )

    # 连续性：窗口内有 crawl 运行且最大间隔缺口在容差内
    max_gap = gaps["max_gap_minutes"]
    gap_ok = max_gap is None or max_gap <= interval * GAP_TOLERANCE_MULTIPLIER
    has_run = gaps["runs"] > 0
    continuity_pass = has_run and gap_ok
    window_hours = 24
    expected_runs = max(1, int(window_hours * 60 // interval))
    continuity_reason = (
        f"crawl 运行 {gaps['runs']} 次 (期望约 {expected_runs})，"
        f"最大相邻间隔 {max_gap if max_gap is not None else 'N/A'} 分钟"
        + ("；当日无 crawl 运行！" if not has_run else "")
        + ("；间隔缺口超容差！" if max_gap is not None and not gap_ok else "")
    )

    checks = [
        {"name": "抓取成功率", "pass": crawl_pass, "detail": crawl_reason},
        {"name": "提取失败率", "pass": ext_pass, "detail": ext_reason},
        {"name": "运行连续性", "pass": continuity_pass, "detail": continuity_reason},
        {"name": "调度异常", "pass": len(failed_runs) == 0,
         "detail": f"失败 job {len(failed_runs)} 次" + (f"：{failed_runs[0].get('job_name')} - {(failed_runs[0].get('message') or '')[:80]}" if failed_runs else "")},
    ]

    report = {
        "report_date": d.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "window": {"start": start, "end": end},
        "crawl": {**crawl, "pass": crawl_pass, "reason": crawl_reason},
        "extraction": {
            **extraction,
            "llm_calls": llm_calls,
            "llm_failure_rate": llm_failure_rate,
            "pass": ext_pass,
            "reason": ext_reason,
        },
        "token_usage": token,
        "anomalies": {"count": len(failed_runs), "failed_jobs": failed_runs},
        "continuity": {**gaps, "expected_runs": expected_runs, "pass": continuity_pass, "reason": continuity_reason},
        "checks": checks,
        "overall_pass": all(c["pass"] for c in checks),
    }

    _write_daily_report(report)
    return report


def _write_daily_report(report: dict) -> Path:
    """把每日体检报告落盘（.md 可读 + .json 机器可读）。"""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    base = DAILY_DIR / f"{report['report_date']}_health"
    (base.with_suffix(".json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (base.with_suffix(".md")).write_text(_build_daily_md(report), encoding="utf-8")
    logger.info("每日体检报告已落盘: %s", base.with_suffix(".md"))
    return base.with_suffix(".md")


def _build_daily_md(report: dict) -> str:
    c, e, t, a, g = report["crawl"], report["extraction"], report["token_usage"], report["anomalies"], report["continuity"]
    lines = [
        f"# 每日体检报告（{report['report_date']}）",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 统计窗口：{report['window']['start']} ~ {report['window']['end']}",
        f"- **总体：{'达标 ✓' if report['overall_pass'] else '异常 ✗'}**",
        "",
        "## 抓取",
        "",
        f"- 尝试 {c['attempted']} 条，失败 {c['failed']} 条，成功率 **{_fmt_rate(c['success_rate'])}**（阈值 ≥{CRAWL_SUCCESS_THRESHOLD:.0%}）",
        f"- 来源 {c['sources_total']} 个，其中 {c['sources_with_errors']} 个有错误",
        "",
        "### 分来源",
        "",
        "| 来源 | 尝试 | 失败 | 运行次数 | 错误日志 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for s in c["per_source"]:
        lines.append(f"| {s['source']} | {s['discovered']} | {s['failed']} | {s['runs']} | {s['errors']} |")
    lines += [
        "",
        "## 提取",
        "",
        f"- 处理 {e['total']} 条：extracted {e['extracted']} / partial {e['partial']} / failed {e['failed']}",
        f"- 提取失败率 **{_fmt_rate(e['failure_rate'])}**（阈值 <{EXTRACTION_FAILURE_THRESHOLD:.0%}）",
        f"- LLM 级失败率 {_fmt_rate(e['llm_failure_rate'])}（调用 {e['llm_calls']} 次，辅口径）",
        "",
        "## Token 消耗",
        "",
        "| 任务 | 调用 | 成功 | 失败 | input | output |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in t["rows"]:
        lines.append(f"| {r['task']} | {r['calls']} | {r['success']} | {r['failed']} | {r['input_tokens']} | {r['output_tokens']} |")
    lines.append(f"| **合计** | {t['total']['calls']} | {t['total']['success']} | {t['total']['failed']} | {t['total']['input_tokens']} | {t['total']['output_tokens']} |")
    lines += [
        "",
        "## 异常日志",
        "",
        f"- 调度失败 {a['count']} 次",
    ]
    for f in a["failed_jobs"]:
        lines.append(f"- `{f['job_name']}`：{(f['message'] or '')[:200]}")
    lines += [
        "",
        "## 运行连续性",
        "",
        f"- crawl 运行 {g['runs']} 次（期望约 {g['expected_runs']}），最大相邻间隔 {g['max_gap_minutes'] if g['max_gap_minutes'] is not None else 'N/A'} 分钟",
        "",
        "## 逐项判定",
        "",
        "| 检查项 | 结果 | 说明 |",
        "| --- | --- | --- |",
    ]
    for chk in report["checks"]:
        lines.append(f"| {chk['name']} | {'✓' if chk['pass'] else '✗'} | {chk['detail']} |")
    lines.append("")
    return "\n".join(lines)


# ---------- 7 天自运行汇总 ----------


def summarize_runs(days: int = 7) -> dict:
    """对最近 N 天重算自运行指标，产出最终验收汇总。

    验收信号（模块 4.2）：
        1. 连续 N 天运行（每日有 crawl 活动 + 每日体检报告文件存在）
        2. 抓取成功率 ≥90%（窗口内聚合）
        3. 崩溃恢复演练证据（data/health/crash/ 下最新结果）
        4. 抽查表落盘（data/health/spotcheck/ 下存在抽查文件）
    """
    today = date.today()
    start_date = today - timedelta(days=days - 1)
    window_start = datetime(start_date.year, start_date.month, start_date.day).isoformat()
    window_end = (today + timedelta(days=1)).isoformat()
    interval = _crawl_interval()

    conn = get_connection()
    try:
        daily_rows = []
        agg_crawl = {"attempted": 0, "failed": 0}
        for i in range(days):
            d = start_date + timedelta(days=i)
            ws, we = _day_window(d)
            crawl = get_crawl_log_stats(conn, ws, we)
            extraction = get_extraction_status_stats(conn, ws, we)
            gaps = get_run_gaps(conn, "crawl", ws, we)
            daily_rows.append(
                {
                    "date": d.isoformat(),
                    "crawl_runs": gaps["runs"],
                    "crawl_attempted": crawl["attempted"],
                    "crawl_failed": crawl["failed"],
                    "crawl_success_rate": crawl["success_rate"],
                    "extraction_total": extraction["total"],
                    "extraction_failed": extraction["failed"],
                    "extraction_failure_rate": extraction["failure_rate"],
                }
            )
            agg_crawl["attempted"] += crawl["attempted"]
            agg_crawl["failed"] += crawl["failed"]
        token = get_token_usage_stats(conn, window_start, window_end)
        failed_runs = get_scheduler_failed_runs(conn, window_start, window_end)
        gaps_overall = get_run_gaps(conn, "crawl", window_start, window_end)
    finally:
        conn.close()

    # 覆盖天数：有 crawl 运行或当日有爬取数据的自然日
    coverage_days = sum(1 for r in daily_rows if r["crawl_runs"] > 0 or r["crawl_attempted"] > 0)
    daily_report_files = sorted(p.name for p in DAILY_DIR.glob("*_health.md")) if DAILY_DIR.exists() else []
    recent_report_files = [p for p in daily_report_files if p[:8] >= start_date.isoformat()[:8]]

    agg_rate = (
        (agg_crawl["attempted"] - agg_crawl["failed"]) / agg_crawl["attempted"]
        if agg_crawl["attempted"] > 0
        else None
    )
    crawl_pass = agg_rate is None or agg_rate >= CRAWL_SUCCESS_THRESHOLD

    # 崩溃演练证据：取 data/health/crash/ 下最新一次结果
    crash_files = sorted(CRASH_DIR.glob("*_crashdrill.json")) if CRASH_DIR.exists() else []
    crash_evidence = None
    if crash_files:
        try:
            with open(crash_files[-1], encoding="utf-8") as f:
                crash_evidence = json.load(f)
        except Exception:  # noqa: BLE001 —— 证据文件损坏不影响汇总
            crash_evidence = None
    crash_pass = bool(crash_evidence and crash_evidence.get("overall_pass"))

    # 抽查表证据
    spot_files = sorted(SPOTCHECK_DIR.glob("*_spotcheck.csv")) if SPOTCHECK_DIR.exists() else []
    spot_pass = len(spot_files) > 0

    # 每日常规体检报告落盘覆盖（作为"每日例行检查"证据）
    report_coverage = len(recent_report_files)
    report_pass = report_coverage == days

    summary = {
        "generated_at": datetime.now().isoformat(),
        "window": {"start_date": start_date.isoformat(), "end_date": today.isoformat(), "days": days},
        "coverage": {
            "days_with_activity": coverage_days,
            "daily_reports": report_coverage,
            "crash_drill_files": len(crash_files),
            "spotcheck_files": len(spot_files),
        },
        "daily": daily_rows,
        "aggregate": {
            "crawl_attempted": agg_crawl["attempted"],
            "crawl_failed": agg_crawl["failed"],
            "crawl_success_rate": agg_rate,
            "crawl_pass": crawl_pass,
            "token": token["total"],
            "scheduler_failed_runs": len(failed_runs),
            "scheduler_failed_detail": [
                {"job_name": f["job_name"], "started_at": f["started_at"], "message": (f.get("message") or "")[:200]}
                for f in failed_runs
            ],
            "max_gap_minutes": gaps_overall["max_gap_minutes"],
        },
        "signals": {
            "连续运行": {"pass": coverage_days == days, "detail": f"有活动天数 {coverage_days}/{days}"},
            "每日体检报告": {"pass": report_pass, "detail": f"落盘 {report_coverage}/{days} 天 ({DAILY_DIR})"},
            "抓取成功率≥90%": {"pass": crawl_pass, "detail": f"窗口聚合 {_fmt_rate(agg_rate)}（阈值 ≥{CRAWL_SUCCESS_THRESHOLD:.0%}）"},
            "崩溃恢复演练": {"pass": crash_pass, "detail": f"证据文件 {len(crash_files)} 个" + ("，最近一次通过" if crash_pass else "，未通过或缺失")},
            "抽查表落盘": {"pass": spot_pass, "detail": f"抽查表 {len(spot_files)} 份 ({SPOTCHECK_DIR})"},
        },
        "overall_pass": coverage_days == days and report_pass and crawl_pass and crash_pass and spot_pass,
    }

    _write_summary(summary)
    return summary


def _write_summary(summary: dict) -> Path:
    """把汇总报告落盘 data/health/summary/。"""
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = SUMMARY_DIR / f"{stamp}_final"
    (base.with_suffix(".json")).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (base.with_suffix(".md")).write_text(_build_summary_md(summary), encoding="utf-8")
    logger.info("7 天自运行汇总已落盘: %s", base.with_suffix(".md"))
    return base.with_suffix(".md")


def _build_summary_md(s: dict) -> str:
    w = s["window"]
    agg = s["aggregate"]
    lines = [
        f"# 7 天自运行终检汇总（{w['start_date']} ~ {w['end_date']}，{w['days']} 天）",
        "",
        f"- 生成时间：{s['generated_at']}",
        f"- **总体：{'达标 ✓' if s['overall_pass'] else '未达标 ✗'}**",
        "",
        "## 逐日明细",
        "",
        "| 日期 | crawl 运行 | 尝试 | 失败 | 成功率 | 提取处理 | 提取失败 | 提取失败率 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in s["daily"]:
        lines.append(
            f"| {r['date']} | {r['crawl_runs']} | {r['crawl_attempted']} | {r['crawl_failed']} "
            f"| {_fmt_rate(r['crawl_success_rate'])} | {r['extraction_total']} | {r['extraction_failed']} "
            f"| {_fmt_rate(r['extraction_failure_rate'])} |"
        )
    lines += [
        "",
        "## 聚合指标",
        "",
        f"- 抓取：尝试 {agg['crawl_attempted']}，失败 {agg['crawl_failed']}，成功率 **{_fmt_rate(agg['crawl_success_rate'])}**",
        f"- Token 消耗：{agg['token']['calls']} 次调用 / {agg['token']['input_tokens']} in + {agg['token']['output_tokens']} out"
        f"（成功 {agg['token']['success']} / 失败 {agg['token']['failed']}）",
        f"- 调度失败：{agg['scheduler_failed_runs']} 次，最大相邻间隔 {agg['max_gap_minutes'] if agg['max_gap_minutes'] is not None else 'N/A'} 分钟",
        "",
        "## 验收信号",
        "",
        "| 信号 | 结果 | 说明 |",
        "| --- | --- | --- |",
    ]
    for name, sig in s["signals"].items():
        lines.append(f"| {name} | {'✓' if sig['pass'] else '✗'} | {sig['detail']} |")
    lines.append("")
    return "\n".join(lines)
