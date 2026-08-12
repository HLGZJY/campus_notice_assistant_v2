"""模块 4.2 每周人工抽查：抽样 10 条抓取/提取结果，生成抽查表落盘。

抽查表 = data/health/spotcheck/<时间戳>_spotcheck.csv/.md，含人工判定列
（判定/问题描述/判定人/判定日期），由人工每周填写作为验收证据。

用法：
    python spot_check.py                        # 抽样 10 条生成抽查表
    python spot_check.py --count 5              # 抽样 5 条
    python spot_check.py --seed 42              # 固定种子（默认 42，确定性抽样）
    python spot_check.py --with-eval            # 附跑 W2 提取评估（evaluate_extraction --use-db，0 LLM 成本）

抽样策略：优先近 7 天有抓取/提取活动的通知（按来源轮转分层），不足用最近抓取的补齐。
"""
from __future__ import annotations

import argparse
import csv
import random
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

# Windows 控制台默认 cp1252 无法打印中文，统一用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from storage.db import get_connection  # noqa: E402

SPOTCHECK_DIR = Path(__file__).parent / "data" / "health" / "spotcheck"
DEFAULT_COUNT = 10
DEFAULT_SEED = 42
RECENT_DAYS = 7

# 人工判定列（抽查表留空待填）
JUDGE_COLUMNS = ["判定", "问题描述", "判定人", "判定日期"]


def sample_notices(
    count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED, recent_days: int = RECENT_DAYS
) -> list[dict]:
    """抽样：近 7 天有活动的通知（来源轮转分层），用固定种子保证可复现。"""
    cutoff = (datetime.now() - timedelta(days=recent_days)).isoformat()
    conn = get_connection()
    try:
        recent = conn.execute(
            """SELECT * FROM notices
               WHERE crawled_at >= ?
                  OR (extracted_at IS NOT NULL AND extracted_at >= ?)
               ORDER BY id""",
            (cutoff, cutoff),
        ).fetchall()
        recent = [dict(r) for r in recent]

        # 补齐池：最近的未入选记录（按 id 倒序）
        if len(recent) < count:
            chosen = {r["id"] for r in recent}
            placeholders = ",".join("?" * len(chosen)) if chosen else "-1"
            extra = conn.execute(
                f"""SELECT * FROM notices WHERE id NOT IN ({placeholders})
                    ORDER BY id DESC LIMIT ?""",
                (*chosen, count - len(recent)),
            ).fetchall()
            recent += [dict(r) for r in extra]

        rows = recent[:count]
    finally:
        conn.close()

    # 固定种子洗牌后再按来源轮转分层：同一种子结果确定，换种子换样本
    rng = random.Random(seed)
    rng.shuffle(rows)

    by_source: dict[str, list[dict]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)
    order = sorted(by_source.items(), key=lambda kv: kv[0])

    selected: list[dict] = []
    idx = 0
    while len(selected) < count:
        progressed = False
        for _, items in order:
            if idx < len(items):
                selected.append(items[idx])
                progressed = True
                if len(selected) >= count:
                    break
        if not progressed:
            break
        idx += 1
    return selected


def _short(s: str | None, limit: int = 60) -> str:
    if not s:
        return ""
    s = str(s).replace("\n", " ").replace("\r", " ")
    return s if len(s) <= limit else s[:limit] + "…"


def _to_csv_row(n: dict, seq: int) -> list:
    return [
        seq,
        n["id"],
        n["source"],
        n["title"],
        n["url"],
        n["crawled_at"],
        n["status"],
        n.get("notice_type") or "",
        n.get("deadline") or "",
        n.get("signup_url") or "",
        n.get("location") or "",
        _short(n.get("summary") or ""),
        "",  # 判定
        "",  # 问题描述
        "",  # 判定人
        "",  # 判定日期
    ]


def write_spotcheck(notices: list[dict], with_eval: bool = False) -> list[Path]:
    """生成抽查表（CSV + MD）并落盘，返回文件列表。"""
    SPOTCHECK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = SPOTCHECK_DIR / f"{stamp}_spotcheck"

    header = ["序号", "notice_id", "来源", "标题", "url", "抓取时间", "状态", "类型", "截止", "报名链接", "地点", "摘要"] + JUDGE_COLUMNS

    csv_path = base.with_suffix(".csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for i, n in enumerate(notices, start=1):
            writer.writerow(_to_csv_row(n, i))

    md_path = base.with_suffix(".md")
    lines = [
        f"# 人工抽查表（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）",
        "",
        "> 每周人工抽查抓取/提取结果。`判定` 填「正确/错误」，错误时在 `问题描述` 说明，"
        "并填 `判定人` 与 `判定日期`。抽样数：%d 条。落盘路径：%s" % (len(notices), csv_path.name),
        "",
        "| 序号 | notice_id | 来源 | 标题 | 状态 | 类型 | 截止 | 判定 | 问题描述 | 判定人 | 判定日期 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, n in enumerate(notices, start=1):
        lines.append(
            f"| {i} | {n['id']} | {n['source']} | {_short(n['title'], 40)} | {n['status']} "
            f"| {n.get('notice_type') or '-'} | {n.get('deadline') or '-'} "
            f"| | | | |"
        )
    lines.append("")
    lines.append("## 明细")
    lines.append("")
    for i, n in enumerate(notices, start=1):
        lines.append(f"### {i}. [{n['id']}] {n['title']}")
        lines.append(f"- 来源：{n['source']}　抓取：{n['crawled_at']}　状态：{n['status']}")
        lines.append(f"- url：{n['url']}")
        if n.get("summary"):
            lines.append(f"- 摘要：{_short(n['summary'], 200)}")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    written = [csv_path, md_path]

    if with_eval:
        eval_path = _run_extraction_eval(stamp)
        if eval_path:
            written.append(eval_path)

    print(f"抽查表已落盘：{csv_path}")
    print(f"抽查表已落盘：{md_path}")
    return written


def _run_extraction_eval(stamp: str) -> Path | None:
    """附跑 W2 提取评估（evaluate_extraction --use-db，0 LLM 成本），输出并入抽查目录。"""
    eval_path = SPOTCHECK_DIR / f"{stamp}_extraction_eval.txt"
    try:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "evaluate_extraction.py"), "--use-db"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except Exception as e:  # noqa: BLE001 —— 附跑失败不阻断抽查表生成
        print(f"!! 附跑提取评估失败：{e}")
        return None
    out = proc.stdout or ""
    if proc.returncode != 0:
        out += f"\n[stderr]\n{(proc.stderr or '')[:500]}"
    if not out.strip():
        print("!! 提取评估无输出（黄金集通知可能不在库中）")
    eval_path.write_text(out, encoding="utf-8")
    print(f"提取评估已并入：{eval_path}")
    return eval_path


def main():
    parser = argparse.ArgumentParser(description="模块 4.2 每周人工抽查")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"抽样条数（默认 {DEFAULT_COUNT}）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"随机种子（默认 {DEFAULT_SEED}）")
    parser.add_argument("--with-eval", action="store_true", help="附跑 W2 提取评估（evaluate_extraction --use-db）")
    args = parser.parse_args()

    notices = sample_notices(count=args.count)
    if not notices:
        print("!! 数据库中没有可抽查的通知")
        return
    print(f"抽样 {len(notices)} 条通知（seed={args.seed}）")
    for n in notices:
        print(f"  #{n['id']:<6} [{n['source']}] {n['title'][:50]}")
    write_spotcheck(notices, with_eval=args.with_eval)


if __name__ == "__main__":
    main()
