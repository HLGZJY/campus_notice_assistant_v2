"""模块 4.2 崩溃恢复演练：真实运行中 kill 调度器并重启，验证续跑与不重复计费。

双模式：
    --auto  自动编排：子进程起调度器 → 等一轮抓取/提取 → 硬杀（taskkill /F）
             → 重启 → 等新周期 → 校验续跑 + 不重复计费。
    --before / --after  手动辅助：--before 落快照，人工 kill/重启调度器后跑 --after 校验。

校验项（验收信号）：
    1. 续跑      ：重启后 scheduler_log 出现新 crawl 运行、id 单调递增、无永久停机。
    2. 不重复计费：kill 前已处理（extracted/partial/failed）的通知，其 token_usage 计费
                   计数在重启后保持不变（不会因续跑再次计费）。

用法：
    python crash_drill.py --auto                       # 自动编排（默认跑提取，会消耗 LLM）
    python crash_drill.py --auto --no-extract          # 纯续跑演练（不调 LLM）
    python crash_drill.py --before                     # 手动模式：落快照
    python crash_drill.py --after                      # 手动模式：加载最新快照并校验
    python crash_drill.py --auto --sleep-after-extract 5   # 提取开始后 5 秒再 kill

结果落盘：data/health/crash/<时间戳>_crashdrill.{json,md}
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

# Windows 控制台默认 cp1252 无法打印中文，统一用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from storage.db import (  # noqa: E402
    get_billing_snapshot,
    get_connection,
    get_notice_status_snapshot,
)

ROOT = Path(__file__).parent
CRASH_DIR = ROOT / "data" / "health" / "crash"
PROCESSED_STATUSES = {"extracted", "partial", "failed"}
POLL_SECONDS = 5
DEFAULT_TIMEOUT = 900  # 15 分钟
DRILL_LOG = ROOT / "data" / "logs" / "crash_drill_scheduler.log"


def snapshot() -> dict:
    """采集当前计费 + 通知状态 + 日志水位，作为"kill 前"基线。"""
    conn = get_connection()
    try:
        billing = get_billing_snapshot(conn, "extraction")
        status = get_notice_status_snapshot(conn)
        sched_max = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM scheduler_log").fetchone()["m"]
        crawl_max = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM crawl_log").fetchone()["m"]
    finally:
        conn.close()
    processed = [nid for nid, st in status.items() if st in PROCESSED_STATUSES]
    return {
        "taken_at": datetime.now().isoformat(),
        "scheduler_log_max_id": sched_max,
        "crawl_log_max_id": crawl_max,
        "processed": processed,
        "billing": billing,
        "notice_status": status,
    }


def _sched_runs_after(job: str, before_max: int) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM scheduler_log WHERE job_name = ? AND id > ?",
            (job, before_max),
        ).fetchone()
        return row["n"]
    finally:
        conn.close()


def _sched_max_id(job: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM scheduler_log WHERE job_name = ?",
            (job,),
        ).fetchone()
        return row["m"]
    finally:
        conn.close()


def _token_calls(task: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM token_usage WHERE task = ?", (task,)
        ).fetchone()
        return row["n"]
    finally:
        conn.close()


def _wait_extraction_started(baseline_tokens: int, before_max: int, timeout: float, proc) -> bool:
    """等待提取开始：出现新的 extraction 计费行，或 extract job 完成记录。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _token_calls("extraction") > baseline_tokens or _sched_runs_after("extract", before_max) > 0:
            return True
        if proc.poll() is not None:
            raise RuntimeError(f"调度器子进程提前退出（rc={proc.returncode}），见日志 {DRILL_LOG}")
        time.sleep(POLL_SECONDS)
    return False


def _wait_sched_run(job: str, before_max: int, timeout: float, proc=None) -> bool:
    """轮询等待某 job 出现 id > before_max 的新运行记录。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _sched_runs_after(job, before_max) > 0:
            return True
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"调度器子进程提前退出（rc={proc.returncode}），见日志 {DRILL_LOG}")
        time.sleep(POLL_SECONDS)
    return False


def verify(before: dict) -> dict:
    """基于 kill 前快照校验：续跑 + 不重复计费。"""
    conn = get_connection()
    try:
        billing_after = get_billing_snapshot(conn, "extraction")
        sched_max_after = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM scheduler_log").fetchone()["m"]
        new_crawl_runs = _sched_runs_after("crawl", before["scheduler_log_max_id"])
    finally:
        conn.close()

    # 1. 续跑
    resumed = sched_max_after > before["scheduler_log_max_id"] and new_crawl_runs > 0
    resume_detail = (
        f"scheduler_log id {before['scheduler_log_max_id']} -> {sched_max_after}，"
        f"重启后 crawl 运行 {new_crawl_runs} 次"
    )

    # 2. 不重复计费：kill 前已处理的通知，重启后计费计数必须不变
    re_billed: list[int] = []
    missing_before: list[int] = []
    for nid in before["processed"]:
        before_count = before["billing"].get(nid, 0)
        after_count = billing_after.get(nid, 0)
        if before_count == 0:
            missing_before.append(nid)  # 快照时已处理但无计费记录（历史遗留），仅提示
        if after_count != before_count:
            re_billed.append(nid)
    no_duplicate = len(re_billed) == 0
    duplicate_detail = (
        f"已处理 {len(before['processed'])} 条中重复计费 {len(re_billed)} 条"
        + (f"：{re_billed[:10]}" if re_billed else "")
        + (f"；无计费记录的已处理通知 {len(missing_before)} 条（历史遗留，不影响判定）" if missing_before else "")
    )

    checks = [
        {"name": "重启续跑", "pass": resumed, "detail": resume_detail},
        {"name": "不重复计费", "pass": no_duplicate, "detail": duplicate_detail},
    ]
    return {
        "verified_at": datetime.now().isoformat(),
        "before_taken_at": before["taken_at"],
        "scheduler_log_max_id": sched_max_after,
        "checks": checks,
        "overall_pass": all(c["pass"] for c in checks),
    }


# ---------- 自动编排 ----------


def _launch_scheduler(extra_args: list[str]) -> subprocess.Popen:
    cmd = [
        sys.executable,
        str(ROOT / "scheduler.py"),
        "--interval", "1",
        "--log", str(DRILL_LOG),
    ] + extra_args
    DRILL_LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = open(DRILL_LOG, "w", encoding="utf-8")
    return subprocess.Popen(cmd, cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT)


def _kill_scheduler(proc: subprocess.Popen) -> None:
    """硬杀调度器进程（含子进程树）。"""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(proc.pid), "/T"],
            capture_output=True,
            timeout=30,
        )
    else:
        os.kill(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def run_auto(args) -> dict:
    """自动编排：起 → 跑一轮 → kill → 重启 → 等新周期 → 校验。"""
    before = snapshot()
    print(f"[1/5] 快照已采集（已处理通知 {len(before['processed'])} 条，scheduler_log 水位 {before['scheduler_log_max_id']}）")

    extra = []
    if args.no_extract:
        extra.append("--no-extract")
    if args.no_reminder:
        extra.append("--no-reminder")
    if args.no_daily:
        extra.append("--no-daily")

    print("[2/5] 启动调度器子进程（--interval 1）...")
    proc1 = _launch_scheduler(extra)
    baseline_tokens = _token_calls("extraction")
    print("      等待第一轮 crawl 运行...")
    if not _wait_sched_run("crawl", before["scheduler_log_max_id"], args.timeout, proc1):
        raise RuntimeError("第一轮 crawl 未在超时内出现")
    if not args.no_extract:
        print("      等待第一轮提取开始（新计费行出现）...")
        if not _wait_extraction_started(baseline_tokens, before["scheduler_log_max_id"], args.timeout, proc1):
            print("      !! 未检测到提取活动（可能无 raw 积压），将等待 extract job 完成后 kill")
            if not _wait_sched_run("extract", before["scheduler_log_max_id"], args.timeout, proc1):
                raise RuntimeError("第一轮 extract 未在超时内出现")
        print(f"      提取已开始，{args.sleep_after_extract} 秒后硬杀（模拟提取进行中崩溃）...")
        time.sleep(args.sleep_after_extract)

    print("[3/5] 硬杀调度器（taskkill /F）...")
    _kill_scheduler(proc1)
    kill_at = datetime.now().isoformat()
    restart_marker = _sched_max_id("crawl")  # kill 时刻 crawl 水位

    print("[4/5] 重启调度器子进程...")
    proc2 = _launch_scheduler(extra)
    if not _wait_sched_run("crawl", restart_marker, args.timeout, proc2):
        raise RuntimeError("重启后未在超时内出现新 crawl 运行")
    print("      等待重启后 extract 运行（确保续跑闭环）...")
    if not args.no_extract:
        if not _wait_sched_run("extract", _sched_max_id("extract"), args.timeout, proc2):
            raise RuntimeError("重启后未在超时内出现新 extract 运行")

    print("[5/5] 校验...")
    result = verify(before)
    result["mode"] = "auto"
    result["kill_at"] = kill_at
    result["log_tail"] = _log_tail(2000)
    return _write_result(result)


# ---------- 手动辅助 ----------


def run_before() -> Path:
    """落 kill 前快照，提示人工操作。"""
    before = snapshot()
    CRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = CRASH_DIR / f"{stamp}_before.json"
    path.write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"快照已落盘：{path}")
    print(f"（已处理通知 {len(before['processed'])} 条，scheduler_log 水位 {before['scheduler_log_max_id']}）")
    print("下一步：请手动 kill 调度器（taskkill /F /IM python.exe 慎用，或按计划停止后重启），")
    print("等调度器跑过至少一轮后，再执行：python crash_drill.py --after")
    return path


def run_after() -> dict:
    """加载最新 before 快照并校验。"""
    before_files = sorted(CRASH_DIR.glob("*_before.json")) if CRASH_DIR.exists() else []
    if not before_files:
        print("!! 未找到 before 快照，请先执行 python crash_drill.py --before")
        sys.exit(1)
    before = json.loads(before_files[-1].read_text(encoding="utf-8"))
    result = verify(before)
    result["mode"] = "manual"
    result["before_file"] = before_files[-1].name
    return _write_result(result)


def _log_tail(max_chars: int = 2000) -> str:
    if not DRILL_LOG.exists():
        return ""
    text = DRILL_LOG.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def _write_result(result: dict) -> dict:
    CRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = CRASH_DIR / f"{stamp}_crashdrill"
    (base.with_suffix(".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        f"# 崩溃恢复演练报告（{result['verified_at']}）",
        "",
        f"- 模式：{result['mode']}　总评：**{'通过 ✓' if result['overall_pass'] else '未通过 ✗'}**",
        f"- 快照时间：{result.get('before_taken_at', '-')}",
        f"- scheduler_log 水位：{result.get('scheduler_log_max_id', '-')}",
        "",
        "| 校验项 | 结果 | 说明 |",
        "| --- | --- | --- |",
    ]
    for chk in result["checks"]:
        lines.append(f"| {chk['name']} | {'✓' if chk['pass'] else '✗'} | {chk['detail']} |")
    if result.get("log_tail"):
        lines += ["", "## 调度器日志尾部", "", "```text", result["log_tail"], "```", ""]
    (base.with_suffix(".md")).write_text("\n".join(lines), encoding="utf-8")
    print("=" * 70)
    print(f"崩溃恢复演练 {'通过 ✓' if result['overall_pass'] else '未通过 ✗'}（{result['mode']}）")
    for chk in result["checks"]:
        print(f"  [{'✓' if chk['pass'] else '✗'}] {chk['name']}: {chk['detail']}")
    print(f"报告已落盘：{base.with_suffix('.md')}")
    print("=" * 70)
    return result


def main():
    parser = argparse.ArgumentParser(description="模块 4.2 崩溃恢复演练（真实 kill 重启）")
    parser.add_argument("--auto", action="store_true", help="自动编排：起/杀/重启/校验")
    parser.add_argument("--before", action="store_true", help="手动模式：落 kill 前快照")
    parser.add_argument("--after", action="store_true", help="手动模式：加载最新快照并校验")
    parser.add_argument("--no-extract", action="store_true", help="--auto 时不跑提取（纯续跑演练，不消耗 LLM）")
    parser.add_argument("--no-reminder", action="store_true", help="--auto 时跳过提醒 job")
    parser.add_argument("--no-daily", action="store_true", help="--auto 时跳过每日 job")
    parser.add_argument("--sleep-after-extract", type=int, default=10, help="--auto 时提取开始后多少秒再 kill")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="等待调度的超时秒数")
    args = parser.parse_args()

    if args.before:
        run_before()
        return
    if args.after:
        run_after()
        return
    if args.auto:
        run_auto(args)
        return
    parser.print_help()


if __name__ == "__main__":
    main()
