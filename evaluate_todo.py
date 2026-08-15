"""M3 待办生成 prompt 回归评估。

用法：
    python evaluate_todo.py --runs 3        # 多轮调用待办生成 Agent 并评分（需要 LLM，默认 3 轮）
    python evaluate_todo.py --list          # 只打印 golden 条目（校验数据集，不调 LLM）

评分规则：
  - 只评 decision（空/非空）与 action（语义内容），due_at / priority 属代码职责不参与总体分
  - action_contains 支持日期语义等价（2026-09-30 ≡ 9月30日 ≡ 2026年9月30日）
  - action_not_contains 为禁止出现的伪造内容
  - 多轮取均值，报告每字段通过率 x/N

验收标准（回归线）：总体 ≥ 0.8；「空 items 决策」准确率 ≥ 0.9。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.todo import PROMPT_VERSION, TodoGenerator

GOLDEN_PATH = Path(__file__).parent / "data" / "eval" / "todo" / "golden_todo.json"

FIELD_NAMES = ["decision", "action"]
FIELD_LABELS = {
    "decision": "空/非空决策",
    "action": "action 内容",
}


def _norm(s: str) -> str:
    """归一化：去空白与常见标点，转小写。"""
    return re.sub(r"[\s、，。；：；（）()·：'\"“”]+", "", (s or "").lower())


def _date_variants(s: str) -> list[str]:
    """把日期表述展开为常见等价写法，用于语义等价匹配。"""
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s or "")
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return [
            f"{y:04d}-{mo:02d}-{d:02d}",
            f"{y}-{mo}-{d}",
            f"{y:04d}/{mo:02d}/{d:02d}",
            f"{y}年{mo}月{d}日",
            f"{y}年{mo:02d}月{d:02d}日",
            f"{mo}月{d}日",
            f"{mo:02d}月{d:02d}日",
        ]
    return [s]


def _matches(got_norm: str, token: str) -> bool:
    """token 是否在 got 中出现（日期按等价写法匹配）。"""
    return any(_norm(v) in got_norm for v in _date_variants(token))


def _has_time_ref(action: str) -> bool:
    """action 是否包含具体时间表述（无截止输入时不应出现）。"""
    return bool(re.search(r"\d{4}[-年/]\d{1,2}[-月/]\d{1,2}|\d{1,2}月\d{1,2}日", action or ""))


def score_entry(items: list, expected: dict) -> tuple[dict, dict]:
    """单条黄金样本评分。

    Returns:
        (fields, info)：fields 参与总体分；info 仅展示（due/priority 对齐）。
    """
    fields = {f: False for f in FIELD_NAMES}
    info = {"due": None, "priority": None}
    exp_items = expected.get("items", 1)
    if exp_items == 0:
        ok = len(items) == 0
        fields["decision"] = ok
        fields["action"] = ok
        return fields, info
    fields["decision"] = len(items) == 1
    if len(items) != 1:
        return fields, info
    item = items[0]
    got = _norm(item.action)
    contains = expected.get("action_contains") or []
    not_contains = expected.get("action_not_contains") or []
    tokens_ok = all(_matches(got, c) for c in contains)
    no_fake = not any(_matches(got, n) for n in not_contains)
    exp_due = expected.get("due_at")
    if exp_due is None:
        fields["action"] = tokens_ok and no_fake and not _has_time_ref(item.action)
    else:
        fields["action"] = tokens_ok and no_fake
    info["due"] = item.due_at == exp_due
    info["priority"] = item.priority == expected.get("priority")
    return fields, info


async def evaluate(runs: int = 3) -> list[dict]:
    """多轮调用待办生成 Agent 评估 golden 集（固定 temperature 提升可复现性）。"""
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    print(f"评估 prompt 版本: {golden.get('prompt_version', '?')}（当前代码 PROMPT_VERSION={PROMPT_VERSION}）")
    generator = TodoGenerator(temperature=0.0)
    rows: list[dict] = []
    for run in range(runs):
        for entry in golden["entries"]:
            notice = dict(entry["notice"])
            try:
                items = await generator.generate_one(notice)
            except Exception as e:  # noqa: BLE001
                print(f"!! 生成失败: {entry['id']} (run={run}) ({type(e).__name__}: {e})")
                rows.append(
                    {
                        "id": entry["id"],
                        "title": notice["title"],
                        "items": [],
                        "status": "failed",
                        "fields": {f: False for f in FIELD_NAMES},
                        "info": {"due": None, "priority": None},
                    }
                )
                continue
            fields, info = score_entry(items, entry["expected"])
            rows.append(
                {
                    "id": entry["id"],
                    "title": notice["title"],
                    "items": items,
                    "status": "generated",
                    "fields": fields,
                    "info": info,
                }
            )
    return rows


def list_entries() -> None:
    """只读打印 golden 条目（不调 LLM）。"""
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    print(f"prompt_version: {golden.get('prompt_version')}  条目数: {len(golden['entries'])}")
    for entry in golden["entries"]:
        n = entry["expected"].get("items", 1)
        print(
            f"  [{entry['id']}] items={n}  {entry['notice']['notice_type']:<16} "
            f"{entry['notice']['title']}"
        )


def report(rows: list[dict]) -> None:
    """打印多轮评分报告（每条每字段 x/N 通过率 + 总体汇总）。"""
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    runs = len({r["id"] for r in rows})
    runs = len(rows) // len(golden["entries"]) if golden["entries"] else 0

    print("\n" + "=" * 80)
    print("M3 待办生成黄金集评估报告")
    print("=" * 80)

    agg: dict[str, dict[str, list[bool]]] = {}
    for r in rows:
        bucket = agg.setdefault(r["id"], {f: [] for f in FIELD_NAMES})
        for f in FIELD_NAMES:
            bucket[f].append(r["fields"][f])

    for entry in golden["entries"]:
        eid = entry["id"]
        bucket = agg[eid]
        title = entry["notice"]["title"]
        print(f"\n[{eid}] {title}  (runs={runs})")
        for f in FIELD_NAMES:
            marks = bucket[f]
            n = len(marks)
            ok = sum(marks)
            mark = "✓" if ok == n else ("△" if ok > 0 else "✗")
            print(f"    [{mark}] {FIELD_LABELS[f]:<10} {ok}/{n}")

    total_fields = 0
    correct_fields = 0
    per_field = {f: [0, 0] for f in FIELD_NAMES}
    for bucket in agg.values():
        for f in FIELD_NAMES:
            n = len(bucket[f])
            ok = sum(bucket[f])
            per_field[f][0] += ok
            per_field[f][1] += n
            total_fields += n
            correct_fields += ok

    print("\n" + "-" * 80)
    print("字段级准确率（跨全部 runs）：")
    for f in FIELD_NAMES:
        c, t = per_field[f]
        acc = c / t if t else 0
        print(f"  {FIELD_LABELS[f]:<10} {acc:.0%}  ({c}/{t})")
    overall = correct_fields / total_fields if total_fields else 0
    print(f"  总体     {overall:.0%}  ({correct_fields}/{total_fields})")
    print("-" * 80)

    decision_acc = per_field["decision"][0] / per_field["decision"][1] if per_field["decision"][1] else 0
    print("验收标准：总体 ≥ 80%，空/非空决策 ≥ 90%")
    print(f"  总体准确率 {'达标 ✓' if overall >= 0.8 else '未达标 ✗'}  (>=80%)")
    print(f"  空/非空决策 {'达标 ✓' if decision_acc >= 0.9 else '未达标 ✗'}  (>=90%)")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="待办生成 prompt 回归评估")
    parser.add_argument("--runs", type=int, default=3, help="评估轮数（默认 3，多轮取均值）")
    parser.add_argument("--list", action="store_true", help="只打印 golden 条目，不调 LLM")
    args = parser.parse_args()
    if args.list:
        list_entries()
        return
    rows = asyncio.run(evaluate(runs=max(1, args.runs)))
    report(rows)


if __name__ == "__main__":
    main()