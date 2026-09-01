"""回归统一入口：收集 test_*.py 逐个执行，输出汇总并可与基线比对。

为什么需要它：桌面化改造会同时动后端（中间件、调度、存储）与前端，
「跑一遍测试」必须是可复现、可比对、可追责的动作，而不是随手敲一行 for 循环。
本脚本把 32 个既有测试与 7 个 test_desktop_*.py 统一收口，
输出结构化结果（JSON）供批次验收记录归档（docs-local/acceptance/）。

用法：
    # 全量跑（B08 / B12 / B17 / B22 各一轮）
    python tools/run_regression.py

    # 只跑桌面新增用例（骨架阶段验证用）
    python tools/run_regression.py --pattern "test_desktop_*.py"

    # 建基线（B00.T5 执行，只需一次）
    python tools/run_regression.py --save-baseline docs-local/acceptance/regression-baseline.json

    # 与基线比对（回归轮次必带）
    python tools/run_regression.py --baseline docs-local/acceptance/regression-baseline.json

    # 只看清单不执行
    python tools/run_regression.py --list

退出码：任一用例 failed / timeout → 1；否则 0。
状态约定：依赖各脚本最后一行形如「结果: 全部通过 / 全部跳过（原因）/ N 项失败 -> [...]」，
         （由 tools/_desktop_testkit.py 保证；既有 32 个脚本原本就符合）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "docs-local" / "acceptance" / "regression-baseline.json"
DEFAULT_PATTERN = "test_*.py"

# 不参与回归收集的文件
EXCLUDE = {
    "_test_adopt.py",  # 下划线开头的一次性脚本，非验收用例
}


def collect(pattern: str) -> list[Path]:
    return sorted(
        p for p in ROOT.glob(pattern) if p.is_file() and p.name not in EXCLUDE
    )


def classify(summary: str, returncode: int) -> str:
    if "项失败" in summary:
        return "failed"
    if "全部跳过" in summary:
        return "skipped"
    if returncode != 0:
        return "failed"
    return "passed"


def _skip_reason(stdout: str, summary: str) -> str:
    if "全部跳过（" in summary:
        return summary.split("全部跳过（", 1)[1].rstrip("）")
    for line in stdout.splitlines():
        if "[SKIP]" in line and "(" in line:
            return line.split("(", 1)[1].rstrip(")")
    return ""


def run_one(path: Path, timeout: int) -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        stdout, returncode = proc.stdout or "", proc.returncode
    except subprocess.TimeoutExpired:
        return {
            "file": path.name,
            "status": "timeout",
            "duration": round(time.time() - t0, 2),
            "summary": f"超过 {timeout}s 未结束",
            "reason": "",
        }

    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    summary = lines[-1].strip() if lines else ""
    status = classify(summary, returncode)
    return {
        "file": path.name,
        "status": status,
        "duration": round(time.time() - t0, 2),
        "summary": summary,
        "reason": _skip_reason(stdout, summary) if status == "skipped" else "",
        "note": "" if summary else "无标准收尾行（结果: ...），按退出码判定",
        "stderr_tail": "" if returncode == 0 else (proc.stderr or "")[-500:],
    }


def compare(baseline: dict, results: list[dict]) -> dict:
    base_files = baseline.get("files", {})
    now_files = {r["file"]: r for r in results}
    regressed, recovered, missing, new = [], [], [], []

    for name, item in base_files.items():
        if name not in now_files:
            missing.append(name)
            continue
        old, new_status = item.get("status"), now_files[name]["status"]
        if old in ("passed", "skipped") and new_status in ("failed", "timeout"):
            regressed.append(f"{name} ({old} -> {new_status})")
        elif old in ("failed", "timeout") and new_status in ("passed", "skipped"):
            recovered.append(f"{name} ({old} -> {new_status})")

    for name in now_files:
        if name not in base_files:
            new.append(name)

    return {
        "regressed": regressed,
        "recovered": recovered,
        "missing": missing,
        "new": new,
        "consistent": not regressed and not missing,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="回归统一入口")
    ap.add_argument("--pattern", default=DEFAULT_PATTERN, help='收集模式，默认 "test_*.py"')
    ap.add_argument("--timeout", type=int, default=300, help="单个用例超时秒数，默认 300")
    ap.add_argument("--baseline", type=Path, help="基线 JSON 路径，提供则比对")
    ap.add_argument("--save-baseline", type=Path, help="把本次结果存为基线")
    ap.add_argument("--json-out", type=Path, help="额外把结果 JSON 写到指定路径")
    ap.add_argument("--list", action="store_true", help="只列清单不执行")
    args = ap.parse_args()

    files = collect(args.pattern)
    if not files:
        print(f"未匹配到任何用例：{args.pattern}")
        return 1

    print(f"收集到 {len(files)} 个用例（pattern={args.pattern}）")
    if args.list:
        for p in files:
            print(f"  - {p.name}")
        return 0

    results = [run_one(p, args.timeout) for p in files]

    counts = {s: 0 for s in ("passed", "skipped", "failed", "timeout")}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    print("=" * 78)
    print(f"回归汇总  {datetime.now().isoformat(timespec='seconds')}  Python {sys.version.split()[0]}")
    print("=" * 78)
    for r in results:
        icon = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP", "timeout": "TIMEOUT"}[r["status"]]
        extra = f"  {r['reason']}" if r["status"] == "skipped" and r["reason"] else ""
        print(f"[{icon:>7}] {r['file']:<46} {r['duration']:>6.2f}s{extra}")
        if r["status"] in ("failed", "timeout"):
            print(f"          {r['summary']}")
            if r.get("stderr_tail"):
                tail = r["stderr_tail"].strip().splitlines()[-1]
                print(f"          stderr: {tail[:160]}")
    print("-" * 78)
    print(
        f"通过 {counts['passed']} ｜ 跳过 {counts['skipped']} ｜ "
        f"失败 {counts['failed']} ｜ 超时 {counts['timeout']}   （共 {len(results)}）"
    )

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "pattern": args.pattern,
        "counts": counts,
        "files": {r["file"]: {"status": r["status"], "summary": r["summary"]} for r in results},
    }

    exit_code = 1 if (counts["failed"] or counts["timeout"]) else 0

    if args.baseline:
        if not args.baseline.exists():
            print(f"基线不存在：{args.baseline}（先跑一次 --save-baseline）")
            exit_code = 1
        else:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            diff = compare(baseline, results)
            print("-" * 78)
            print(f"基线比对（{args.baseline.name} @ {baseline.get('generated_at', '?')}）")
            print(f"  新增失败（回归）：{diff['regressed'] or '无'}")
            print(f"  已恢复：{diff['recovered'] or '无'}")
            print(f"  缺失文件：{diff['missing'] or '无'}")
            print(f"  新增文件：{len(diff['new'])} 个 {diff['new'] if diff['new'] else ''}")
            if diff["regressed"] or diff["missing"]:
                exit_code = 1
            payload["diff"] = diff

    if args.save_baseline:
        args.save_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.save_baseline.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"基线已写入：{args.save_baseline}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果 JSON：{args.json_out}")

    print("=" * 78)
    print("结论：" + ("不通过（存在失败/超时/回归）" if exit_code else "通过"))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
