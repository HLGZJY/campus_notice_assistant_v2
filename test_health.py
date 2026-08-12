"""模块 4.2 健康体系（每日体检 + 7 天汇总 + 崩溃演练 + 抽查）离线验收。

不依赖真实 LLM / 网络 / 调度器进程：全部用临时 SQLite 库 + 临时报告目录，
直接调用服务层函数验证核心判定逻辑。

覆盖验收信号：
  A. crash_drill.verify()   ：续跑（scheduler_log 水位推进 + 新 crawl 运行）
                              + 不重复计费（已处理通知的计费计数重启后不变）
  B. run_daily_health_check()：抓取成功率 ≥90%、提取失败率 <10%、token 汇总、
                              异常日志提取、运行连续性判定、报告落盘
  C. summarize_runs()       ：7 天连续运行 / 每日报告覆盖 / 聚合成功率 /
                              崩溃演练证据 / 抽查表证据 → overall_pass
  D. spot_check.sample_notices()：固定种子确定性抽样 + 来源分层；抽查表落盘

用法：python test_health.py
"""
import datetime
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import storage.db
from storage.db import get_connection

TMP_DB = Path(__file__).parent / "data" / "test_health.db"
TMP_HEALTH = Path(__file__).parent / "data" / "test_health"

storage.db.DB_PATH = TMP_DB

import services.health_service as hs  # noqa: E402
import crash_drill  # noqa: E402
import spot_check  # noqa: E402

# 报告目录全部重定向到临时目录，避免污染 data/health/
hs.DAILY_DIR = TMP_HEALTH / "daily"
hs.SUMMARY_DIR = TMP_HEALTH / "summary"
hs.SPOTCHECK_DIR = TMP_HEALTH / "spotcheck"
hs.CRASH_DIR = TMP_HEALTH / "crash"
spot_check.SPOTCHECK_DIR = hs.SPOTCHECK_DIR
crash_drill.CRASH_DIR = hs.CRASH_DIR

# 固定"今天"，让 summarize 的 7 天窗口确定
class FakeDate(datetime.date):
    @classmethod
    def today(cls):
        return datetime.date(2026, 8, 12)


def reset_db():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass
    try:
        if TMP_HEALTH.exists():
            shutil.rmtree(TMP_HEALTH)
    except OSError:
        pass


def insert_crawl(conn, source, discovered, failed, at, errors=""):
    conn.execute(
        """INSERT INTO crawl_log
           (source, total_discovered, total_new, total_skipped, total_changed,
            total_failed, errors, crawled_at)
           VALUES (?, ?, 0, 0, 0, ?, ?, ?)""",
        (source, discovered, failed, errors, at),
    )


def insert_sched(conn, job, status, at, message=""):
    conn.execute(
        """INSERT INTO scheduler_log
           (job_name, status, started_at, finished_at, duration_ms,
            failure_count, message, details)
           VALUES (?, ?, ?, ?, 100, 0, ?, '{}')""",
        (job, status, at, at, message),
    )


def insert_notice(conn, i, status="extracted", extracted_at=None):
    conn.execute(
        """INSERT INTO notices (url, source, title, raw_content, crawled_at, status, extracted_at)
           VALUES (?, '测试来源', ?, '正文', '2026-08-10T00:00:00', ?, ?)""",
        (f"https://health.example/{i}", f"通知{i}", status, extracted_at),
    )


def insert_billing(conn, notice_id, task="extraction", created_at="2026-08-10T00:00:00", success=1):
    conn.execute(
        """INSERT INTO token_usage
           (task, model, notice_id, input_tokens, output_tokens, success, retry_count, error, created_at)
           VALUES (?, 'fake-model', ?, 100, 50, ?, 0, NULL, ?)""",
        (task, notice_id, success, created_at),
    )


def run():
    reset_db()
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    # ---------- A. crash_drill.verify ----------
    print("== A. crash_drill.verify：续跑 + 不重复计费 ==")
    conn = get_connection()
    # 3 条已处理（1 extracted / 2 partial / 3 failed）+ 1 条未处理（raw）
    insert_notice(conn, 1, "extracted")
    insert_notice(conn, 2, "partial")
    insert_notice(conn, 3, "failed")
    insert_notice(conn, 4, "raw", extracted_at=None)
    # 计费：通知 1 与 3 各有 1 次；通知 2 无计费（模拟历史遗留）
    insert_billing(conn, 1)
    insert_billing(conn, 3)
    # kill 前已有若干调度运行
    insert_sched(conn, "crawl", "success", "2026-08-10T00:00:00")
    insert_sched(conn, "crawl", "success", "2026-08-10T01:00:00")
    conn.commit()
    conn.close()

    before = crash_drill.snapshot()
    check("A. before 快照识别 3 条已处理通知", len(before["processed"]) == 3, f"{before['processed']}")
    check("A. before 快照含计费快照", before["billing"] == {1: 1, 3: 1}, f"{before['billing']}")

    # 模拟重启：新增 crawl 运行，且已处理通知计费不变 → 应通过
    conn = get_connection()
    insert_sched(conn, "crawl", "success", "2026-08-10T02:00:00")
    conn.commit()
    conn.close()
    res = crash_drill.verify(before)
    check("A. 续跑通过（水位推进 + 新 crawl 运行）",
          res["checks"][0]["pass"] and res["overall_pass"], res["checks"][0]["detail"])
    check("A. 不重复计费通过", res["checks"][1]["pass"], res["checks"][1]["detail"])

    # 模拟重复计费：通知 1 又多记一条 → 应判失败
    conn = get_connection()
    insert_billing(conn, 1)
    conn.commit()
    conn.close()
    res2 = crash_drill.verify(before)
    check("A. 重复计费被检出（overall 失败）", not res2["checks"][1]["pass"] and not res2["overall_pass"],
          res2["checks"][1]["detail"])
    check("A. 重复计费列出具体通知", res2["checks"][1]["detail"] and "1" in res2["checks"][1]["detail"],
          res2["checks"][1]["detail"])

    # ---------- B. 每日体检：健康日 ----------
    print("\n== B. run_daily_health_check：健康日全部达标 ==")
    reset_db()
    conn = get_connection()
    # 抓取：A 100 发现 0 失败、B 50 发现 2 失败 → 成功率 (150-2)/150 ≈ 98.7%
    insert_crawl(conn, "A", 100, 0, "2026-08-10T01:00:00")
    insert_crawl(conn, "B", 50, 2, "2026-08-10T02:00:00", errors="source B 有错误")
    # 提取：9 成功 0 失败 → 失败率 0%
    for i in range(1, 10):
        insert_notice(conn, i, "extracted", extracted_at="2026-08-10T03:00:00")
    # token：extraction 5 次 + embedding 2 次
    for i in range(5):
        insert_billing(conn, None)
    for i in range(2):
        insert_billing(conn, None, task="embedding")
    # 连续性：crawl 运行 3 次，间隔 1 分钟
    insert_sched(conn, "crawl", "success", "2026-08-10T01:00:00")
    insert_sched(conn, "crawl", "success", "2026-08-10T01:01:00")
    insert_sched(conn, "crawl", "success", "2026-08-10T01:02:00")
    insert_sched(conn, "extract", "success", "2026-08-10T01:05:00")
    conn.commit()
    conn.close()

    report = hs.run_daily_health_check(datetime.date(2026, 8, 10))
    check("B. 抓取成功率 ≈98.7% 且通过", report["crawl"]["success_rate"] == pytest_approx(0.9866667) and report["crawl"]["pass"],
          f"{report['crawl']['success_rate']}")
    check("B. 提取失败率 0 且通过", report["extraction"]["failure_rate"] == 0.0 and report["extraction"]["pass"],
          f"{report['extraction']['failure_rate']}")
    check("B. 连续性通过（3 次运行、最大间隔 1 分钟）", report["continuity"]["pass"], report["continuity"]["reason"])
    check("B. 无调度异常", report["anomalies"]["count"] == 0, f"{report['anomalies']['count']}")
    check("B. token 汇总 5+2 次调用", report["token_usage"]["total"]["calls"] == 7,
          f"{report['token_usage']['total']['calls']}")
    check("B. 健康日 overall_pass", report["overall_pass"])
    check("B. 每日报告已落盘", (hs.DAILY_DIR / "2026-08-10_health.md").exists())

    # ---------- B2. 每日体检：异常日 ----------
    print("\n== B2. 每日体检：异常日正确判失败 ==")
    reset_db()
    conn = get_connection()
    insert_crawl(conn, "A", 100, 40, "2026-08-10T01:00:00")  # 成功率 60% < 90%
    for i in range(5):
        insert_notice(conn, i, "extracted", extracted_at="2026-08-10T03:00:00")
    for i in range(5, 8):
        insert_notice(conn, i, "failed", extracted_at="2026-08-10T03:00:00")  # 3/8 = 37.5% > 10%
    insert_sched(conn, "crawl", "failed", "2026-08-10T01:00:00", message="抓取超时")
    insert_sched(conn, "crawl", "success", "2026-08-10T05:00:00")  # 间隔 4 小时 > 容差 2×60 分钟
    conn.commit()
    conn.close()
    report_bad = hs.run_daily_health_check(datetime.date(2026, 8, 10))
    check("B2. 低抓取成功率被检出", not report_bad["crawl"]["pass"], report_bad["crawl"]["reason"])
    check("B2. 高提取失败率被检出", not report_bad["extraction"]["pass"], report_bad["extraction"]["reason"])
    check("B2. 调度异常被检出", report_bad["anomalies"]["count"] == 1, f"{report_bad['anomalies']['count']}")
    check("B2. 连续性失败（间隔 240 分钟超容差）", not report_bad["continuity"]["pass"], report_bad["continuity"]["reason"])
    check("B2. 异常日 overall 失败", not report_bad["overall_pass"])

    # 空日：无数据 → 成功率为 None，判定为通过（N/A），但连续性失败
    print("\n== B3. 空日：N/A 不误判、连续性失败 ==")
    reset_db()
    report_empty = hs.run_daily_health_check(datetime.date(2026, 8, 10))
    check("B3. 无数据成功率为 None 且判定通过", report_empty["crawl"]["success_rate"] is None and report_empty["crawl"]["pass"])
    check("B3. 无数据提取失败率 None 且判定通过", report_empty["extraction"]["failure_rate"] is None and report_empty["extraction"]["pass"])
    check("B3. 无运行连续性失败", not report_empty["continuity"]["pass"])
    check("B3. 空日 overall 失败", not report_empty["overall_pass"])

    # ---------- C. summarize_runs：7 天验收 ----------
    print("\n== C. summarize_runs：7 天连续运行终检 ==")
    hs.date = FakeDate  # 固定今天 2026-08-12
    reset_db()
    conn = get_connection()
    start_day = datetime.date(2026, 8, 6)
    for i in range(7):
        d = start_day + datetime.timedelta(days=i)
        day = d.isoformat()
        insert_crawl(conn, "A", 100, 0, f"{day}T01:00:00")
        insert_crawl(conn, "A", 100, 1, f"{day}T02:00:00")
        insert_sched(conn, "crawl", "success", f"{day}T01:00:00")
        insert_sched(conn, "crawl", "success", f"{day}T02:00:00")
    conn.commit()
    conn.close()
    # 生成 7 天每日报告文件
    for i in range(7):
        hs.run_daily_health_check(start_day + datetime.timedelta(days=i))
    # 崩溃演练证据：一份通过的 crashdrill.json
    hs.CRASH_DIR.mkdir(parents=True, exist_ok=True)
    (hs.CRASH_DIR / "20260812_000000_crashdrill.json").write_text(
        json.dumps({"overall_pass": True, "mode": "auto"}, ensure_ascii=False), encoding="utf-8"
    )
    # 抽查表证据：一份 spotcheck.csv
    hs.SPOTCHECK_DIR.mkdir(parents=True, exist_ok=True)
    (hs.SPOTCHECK_DIR / "20260812_000000_spotcheck.csv").write_text("序号\n1\n", encoding="utf-8")

    summary = hs.summarize_runs(days=7)
    sig = summary["signals"]
    check("C. 连续运行 7/7 天", sig["连续运行"]["pass"], sig["连续运行"]["detail"])
    check("C. 每日体检报告 7/7 落盘", sig["每日体检报告"]["pass"], sig["每日体检报告"]["detail"])
    check("C. 聚合抓取成功率 ≥90%", sig["抓取成功率≥90%"]["pass"],
          f"{summary['aggregate']['crawl_success_rate']}")
    check("C. 崩溃演练证据通过", sig["崩溃恢复演练"]["pass"], sig["崩溃恢复演练"]["detail"])
    check("C. 抽查表落盘", sig["抽查表落盘"]["pass"], sig["抽查表落盘"]["detail"])
    check("C. 7 天终检 overall_pass", summary["overall_pass"])

    # 缺失抽查表 → 该信号失败、overall 失败
    (hs.SPOTCHECK_DIR / "20260812_000000_spotcheck.csv").unlink()
    summary2 = hs.summarize_runs(days=7)
    check("C2. 抽查表缺失 → 信号失败且 overall 失败",
          not summary2["signals"]["抽查表落盘"]["pass"] and not summary2["overall_pass"],
          summary2["signals"]["抽查表落盘"]["detail"])
    # 崩溃演练证据不通过 → 信号失败
    (hs.CRASH_DIR / "20260812_000000_crashdrill.json").write_text(
        json.dumps({"overall_pass": False}, ensure_ascii=False), encoding="utf-8"
    )
    summary3 = hs.summarize_runs(days=7)
    check("C3. 崩溃演练未通过 → 信号失败", not summary3["signals"]["崩溃恢复演练"]["pass"],
          summary3["signals"]["崩溃恢复演练"]["detail"])

    # ---------- D. spot_check 抽样 ----------
    print("\n== D. spot_check：确定性抽样 + 来源分层 + 落盘 ==")
    reset_db()
    conn = get_connection()
    for i in range(1, 11):
        source = ["A", "A", "B", "B", "C", "C", "C", "D", "D", "E"][i - 1]
        conn.execute(
            """INSERT INTO notices (url, source, title, raw_content, crawled_at, status)
               VALUES (?, ?, ?, '正文', '2026-08-10T00:00:00', 'extracted')""",
            (f"https://spot.example/{i}", source, f"通知{i}"),
        )
    conn.commit()
    conn.close()

    s1 = spot_check.sample_notices(count=5, seed=42)
    s2 = spot_check.sample_notices(count=5, seed=42)
    check("D. 同种子两次抽样结果一致（确定性）",
          [r["id"] for r in s1] == [r["id"] for r in s2], f"{[r['id'] for r in s1]}")
    s3 = spot_check.sample_notices(count=5, seed=7)
    check("D. 换种子样本变化", [r["id"] for r in s1] != [r["id"] for r in s3])
    check("D. 抽样数正确", len(s1) == 5, f"n={len(s1)}")
    sources = [r["source"] for r in s1]
    check("D. 来源分层覆盖多个来源", len(set(sources)) >= 2, f"sources={sources}")
    check("D. 近 7 天优先抽样", all(r["id"] in range(1, 11) for r in s1))

    written = spot_check.write_spotcheck(s1, with_eval=False)
    check("D. 抽查表 CSV 落盘", written[0].exists())
    check("D. 抽查表 MD 落盘", written[1].exists())
    csv_text = written[0].read_text(encoding="utf-8-sig")
    check("D. 抽查表含人工判定列", "判定" in csv_text and "判定人" in csv_text)

    cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


def pytest_approx(value, abs_eps=1e-4):
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) <= abs_eps

        def __repr__(self):
            return f"~{value}"

    return _Approx()


def cleanup():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass
    try:
        if TMP_HEALTH.exists():
            shutil.rmtree(TMP_HEALTH)
    except OSError:
        pass


if __name__ == "__main__":
    run()
