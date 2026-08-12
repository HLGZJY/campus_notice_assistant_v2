"""M2 验收：用黄金集评估结构化提取准确率。

用法：
    python evaluate_extraction.py                # 重新调用 LLM 提取并评分
    python evaluate_extraction.py --use-db       # 用 DB 中已有的提取结果评分

输出：字段级准确率 + 总体准确率。
验收标准（PRD）：关键字段准确率 > 80%，截止时间准确率 > 85%。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

# Windows 控制台默认 cp1252 无法打印中文，统一用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.extractor import NoticeExtractor
from core.models import NoticeExtraction
from storage.db import get_connection

GOLDEN_PATH = Path(__file__).parent / "data" / "golden_extraction.json"


def _norm(s: str) -> str:
    """归一化：去空白和常见标点，转小写。"""
    return re.sub(r"[\s、，。；：；（）()·：'\"“”]+", "", (s or "").lower())


def compare_deadline(got_date: str | None, expected_date: str | None) -> bool:
    """截止时间比对（只比日期部分）。"""
    got_date = _norm(got_date or "")[:10]
    expected_date = _norm(expected_date or "")[:10]
    if expected_date == "":
        return got_date == ""  # 期望无截止，提取也必须无
    return got_date == expected_date


def score_entry(ext: NoticeExtraction | None, expected: dict) -> dict:
    """单条黄金样本评分。"""
    fields = {}
    if ext is None:
        return {"notice_type": False, "deadline": False, "target": False, "signup": False}

    # notice_type：期望值可以是字符串或可接受值列表
    expected_types = expected.get("notice_type")
    if isinstance(expected_types, str):
        expected_types = [expected_types]
    fields["notice_type"] = any(
        _norm(ext.notice_type) == _norm(t) for t in expected_types
    )

    # deadline
    fields["deadline"] = compare_deadline(ext.deadline, expected.get("deadline_date"))

    # target_audience（expected 含多个 token，都要出现）
    targets = expected.get("target_contains") or []
    if not targets:
        fields["target"] = True  # 期望未检查该字段
    else:
        got = _norm(ext.target_audience or "")
        fields["target"] = all(_norm(t) in got for t in targets)

    # signup
    keywords = expected.get("signup_keywords") or []
    if not keywords:
        fields["signup"] = True
    else:
        got = _norm((ext.signup_method or "") + " " + (ext.signup_url or ""))
        fields["signup"] = all(_norm(k) in got for k in keywords)

    return fields


async def evaluate() -> dict:
    """重新调用 LLM 提取黄金集并评分。"""
    conn = get_connection()
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["entries"]
    extractor = NoticeExtractor()

    rows = []
    for entry in golden:
        title = entry["title"]
        row = conn.execute(
            "SELECT id, title, raw_content, published_at, crawled_at FROM notices WHERE title = ?",
            (title,),
        ).fetchone()
        if row is None:
            print(f"!! 找不到通知: {title}")
            continue
        outcome = await extractor.extract_one(
            title=row["title"],
            content=row["raw_content"] or "",
            published_at=row["published_at"],
            crawled_at=row["crawled_at"],
        )
        fields = score_entry(outcome.extraction, entry["expected"])
        rows.append(
            {
                "title": title,
                "extraction": outcome.extraction,
                "status": outcome.status,
                "fields": fields,
            }
        )
    conn.close()
    return rows


def evaluate_from_db() -> dict:
    """用 DB 中已存的提取结果评分。"""
    conn = get_connection()
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["entries"]

    rows = []
    for entry in golden:
        row = conn.execute(
            "SELECT * FROM notices WHERE title = ?",
            (entry["title"],),
        ).fetchone()
        if row is None:
            print(f"!! 找不到通知: {entry['title']}")
            continue
        ext = None
        if row["notice_type"]:
            ext = NoticeExtraction(
                notice_type=row["notice_type"],
                title=row["title"],
                target_audience=row["target_audience"],
                signup_method=row["signup_method"],
                signup_url=row["signup_url"],
                location=row["location"],
                deadline_raw=row["deadline_raw"],
                deadline=row["deadline"],
                summary=row["summary"],
            )
        rows.append(
            {
                "title": entry["title"],
                "extraction": ext,
                "status": row["status"],
                "fields": score_entry(ext, entry["expected"]),
            }
        )
    conn.close()
    return rows


def report(rows: list[dict]) -> None:
    """打印评分报告。"""
    field_names = ["notice_type", "deadline", "target", "signup"]
    field_labels = {
        "notice_type": "通知类型",
        "deadline": "截止时间",
        "target": "面向对象",
        "signup": "报名方式",
    }

    print("\n" + "=" * 80)
    print("M2 黄金集评估报告")
    print("=" * 80)

    for r in rows:
        ext = r["extraction"]
        print(
            f"\n{r['title']}  (status={r['status']})"
        )
        if ext:
            print(
                f"  类型={ext.notice_type:<12} 截止={ext.deadline or '-':<26} "
                f"对象={ext.target_audience or '-'}"
            )
            if ext.signup_method:
                print(f"  报名={ext.signup_method[:60]}")
        for f, ok in r["fields"].items():
            mark = "✓" if ok else "✗"
            print(f"    [{mark}] {field_labels[f]}")
        if r["status"] == "failed":
            print("    [✗] 提取失败")

    # 汇总
    total_fields = 0
    correct_fields = 0
    per_field = {f: [0, 0] for f in field_names}  # [correct, total]
    for r in rows:
        for f in field_names:
            per_field[f][1] += 1
            total_fields += 1
            if r["fields"][f]:
                per_field[f][0] += 1
                correct_fields += 1

    print("\n" + "-" * 80)
    print("字段级准确率：")
    for f in field_names:
        c, t = per_field[f]
        acc = c / t if t else 0
        print(f"  {field_labels[f]:<8} {acc:.0%}  ({c}/{t})")
    overall = correct_fields / total_fields if total_fields else 0
    print(f"  总体     {overall:.0%}  ({correct_fields}/{total_fields})")
    print("-" * 80)

    # 验收结论
    deadline_acc = per_field["deadline"][0] / per_field["deadline"][1] if per_field["deadline"][1] else 0
    passed_overall = overall >= 0.8
    passed_deadline = deadline_acc >= 0.85
    print("验收标准（PRD）：关键字段 > 80%，截止时间 > 85%")
    print(f"  总体准确率 {'达标 ✓' if passed_overall else '未达标 ✗'}  (>=80%)")
    print(f"  截止时间准确率 {'达标 ✓' if passed_deadline else '未达标 ✗'}  (>=85%)")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="黄金集评估")
    parser.add_argument("--use-db", action="store_true", help="用 DB 已有结果评估（不重新调用 LLM）")
    args = parser.parse_args()

    if args.use_db:
        rows = evaluate_from_db()
    else:
        rows = asyncio.run(evaluate())
    report(rows)


if __name__ == "__main__":
    main()
