"""模块 4.2：每日体检 + 数据库巡检脚本。

默认（不带参数）保留原查看功能；扩展为"每日体检"CLI：

用法：
    python check_db.py                            # 查看库内容（原功能）
    python check_db.py --report                   # 体检昨日，落 data/health/daily/
    python check_db.py --report --date 2026-08-10 # 体检指定日期
    python check_db.py --summary --days 7         # 7 天自运行验收汇总
"""
import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))


def _legacy_check() -> None:
    """原 check_db.py 的查看逻辑。"""
    conn = sqlite3.connect("data/notices.db")
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
    print(f"总计: {total} 条通知\n")

    print("前5条:")
    rows = conn.execute(
        "SELECT title, length(raw_content) as content_len, status FROM notices LIMIT 5"
    ).fetchall()
    for r in rows:
        print(f"  {r['title'][:50]} | {r['content_len']}字 | {r['status']}")

    print(f"\n状态分布:")
    for r in conn.execute("SELECT status, COUNT(*) as cnt FROM notices GROUP BY status").fetchall():
        print(f"  {r['status']}: {r['cnt']}条")

    print(f"\n抓取日志:")
    for r in conn.execute(
        "SELECT source, total_discovered, total_new, total_skipped, total_changed, total_failed FROM crawl_log"
    ).fetchall():
        print(
            f"  {r['source'][:30]} | 发现{r['total_discovered']} | 新增{r['total_new']} "
            f"| 跳过{r['total_skipped']} | 变更{r['total_changed']} | 失败{r['total_failed']}"
        )
    conn.close()


def _print_daily_report(report: dict) -> None:
    """控制台打印每日体检报告摘要。"""
    c, e, t, a, g = (
        report["crawl"],
        report["extraction"],
        report["token_usage"],
        report["anomalies"],
        report["continuity"],
    )
    rate = f"{c['success_rate']:.1%}" if c["success_rate"] is not None else "N/A(无数据)"
    ext_rate = f"{e['failure_rate']:.1%}" if e["failure_rate"] is not None else "N/A(无数据)"
    llm_rate = f"{e['llm_failure_rate']:.1%}" if e["llm_failure_rate"] is not None else "N/A"
    max_gap = g["max_gap_minutes"] if g["max_gap_minutes"] is not None else "N/A"
    first_fail = ""
    if a["failed_jobs"]:
        first = a["failed_jobs"][0]
        first_fail = f"首个: {first['job_name']} - {(first.get('message') or '')[:80]}"

    print("=" * 70)
    print(f"每日体检报告: {report['report_date']}  总体: {'达标 ✓' if report['overall_pass'] else '异常 ✗'}")
    print("=" * 70)
    print(f"抓取: 尝试 {c['attempted']} 失败 {c['failed']} 成功率 {rate} (阈值 ≥90%)  来源 {c['sources_total']} 个")
    print(f"提取: 处理 {e['total']} extracted={e['extracted']} partial={e['partial']} failed={e['failed']} "
          f"失败率 {ext_rate} (阈值 <10%)  LLM级 {llm_rate}")
    print(f"Token: 调用 {t['total']['calls']} 成功 {t['total']['success']} 失败 {t['total']['failed']} "
          f"in {t['total']['input_tokens']} out {t['total']['output_tokens']}")
    print(f"调度异常: {a['count']} 次失败  {first_fail}")
    print(f"连续性: crawl 运行 {g['runs']} 次 最大间隔 {max_gap} 分钟")
    for chk in report["checks"]:
        print(f"  [{'✓' if chk['pass'] else '✗'}] {chk['name']}: {chk['detail']}")


def _print_summary(s: dict) -> None:
    """控制台打印 7 天自运行汇总与验收结论。"""
    w, agg = s["window"], s["aggregate"]
    print("=" * 70)
    print(f"7 天自运行终检汇总: {w['start_date']} ~ {w['end_date']} ({w['days']} 天)  "
          f"总体: {'达标 ✓' if s['overall_pass'] else '未达标 ✗'}")
    print("=" * 70)
    print(f"  [✓/✗] 连续运行     : {s['signals']['连续运行']['detail']}")
    print(f"  [✓/✗] 每日体检报告 : {s['signals']['每日体检报告']['detail']}")
    print(f"  [✓/✗] 抓取成功率   : {s['signals']['抓取成功率≥90%']['detail']}")
    print(f"  [✓/✗] 崩溃恢复演练 : {s['signals']['崩溃恢复演练']['detail']}")
    print(f"  [✓/✗] 抽查表落盘   : {s['signals']['抽查表落盘']['detail']}")
    print("-" * 70)
    print(f"  聚合抓取: 尝试 {agg['crawl_attempted']} 失败 {agg['crawl_failed']}")
    print(f"  Token: {agg['token']['calls']} 次调用 / in {agg['token']['input_tokens']} + out {agg['token']['output_tokens']}")
    print(f"  调度失败: {agg['scheduler_failed_runs']} 次，最大相邻间隔 {agg['max_gap_minutes']} 分钟")
    print("=" * 70)


def main():
    # Windows 控制台默认 cp1252 无法打印中文，统一用 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="校园通知数据库巡检 / 每日体检（模块 4.2）")
    parser.add_argument(
        "--report",
        action="store_true",
        help="执行每日体检并落盘 data/health/daily/（默认体检昨天）",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="--report 时指定被体检日期 YYYY-MM-DD（默认昨天）",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="执行 7 天自运行验收汇总并落盘 data/health/summary/",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="--summary 时的统计天数（默认 7）",
    )
    args = parser.parse_args()

    if args.report or args.summary:
        from services.health_service import run_daily_health_check, summarize_runs

        if args.report:
            d = date.fromisoformat(args.date) if args.date else None
            report = run_daily_health_check(report_date=d)
            _print_daily_report(report)
            return
        if args.summary:
            summary = summarize_runs(days=max(1, args.days))
            _print_summary(summary)
            return

    _legacy_check()


if __name__ == "__main__":
    main()
