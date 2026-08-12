"""M2.3 过期处理三档实验。

对同一测试集（data/retrieval_testset.json）跑三种过期策略并对比：
    A 不过滤（基线）          —— 与模块 2.2 纯向量基线一致
    B 过期降权（时间衰减重排） —— 不剔除过期通知，按过期天数 decay 后重排
    C 过期排除（检索前过滤）   —— 检索前用 Chroma where($nin) 过滤过期通知

过期判定规则（与 scheduler.py 每日体检一致）：
    以 deadline 为准；无 deadline 的按 published_at + expire_days 兜底；
    expire_days 默认读配置 crawl.expire_days（app.yaml 中为 90）。

用法：
    python evaluate_expired.py                              # 三档各跑一遍，结果落 data/eval/expired/
    python evaluate_expired.py --rebuild-corpus             # 先用测试集 27 条语料重建索引再跑
    python evaluate_expired.py --decay-strength 0.1         # 换降权强度做敏感度检查
    python evaluate_expired.py --top-k 10                   # 换 K

输出：
    - 控制台三档对比表（含陷阱题单独一组）+ 结论
    - data/eval/expired/<时间戳>_comparison.json   （机器可读，供后续 2.4 混合检索对比）
    - data/eval/expired/<时间戳>_comparison.md     （可读对比表）

本实验只评估检索阶段，不调用 LLM。pollution 题（Q19/Q20）沿用 2.2 约定只评"删除前 TopK 命中"第一阶段。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

# Windows 控制台默认 cp1252 无法打印中文，统一用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from storage.vectorstore import VectorIndex, get_vector_index

TESTSET_PATH = Path(__file__).parent / "data" / "retrieval_testset.json"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "data" / "eval" / "expired"

TYPE_LABELS = {
    "semantic": "语义理解",
    "proper_noun": "专有名词",
    "expired_trap": "过期陷阱",
    "pollution": "污染用例",
}

# 2.2 基线（模块 2.2 留档值），用于 A 档一致性自检
BASELINE = {"overall_hit": 0.60, "trap_hit": 0.40, "mrr": 0.412}


def _pct(x: float) -> str:
    return f"{x:.0%}"


def _fmt_mrr(x: float) -> str:
    return f"{x:.3f}"


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def build_strategies(reference_date: date, expire_days: int, alpha: float, candidate_factor: int) -> dict:
    """定义三档策略及其 search_kwargs。"""
    return {
        "A": {
            "label": "A 不过滤（基线）",
            "search_kwargs": {},
        },
        "B": {
            "label": "B 过期降权（时间衰减重排）",
            "search_kwargs": {
                "strategy": "decay",
                "reference_date": reference_date,
                "expire_days": expire_days,
                "decay_strength": alpha,
                "candidate_factor": candidate_factor,
            },
        },
        "C": {
            "label": "C 过期排除（检索前过滤）",
            "search_kwargs": {
                "strategy": "filter",
                "reference_date": reference_date,
                "expire_days": expire_days,
            },
        },
    }


def build_comparison(testset: dict, index: VectorIndex, top_k: int, strategies: dict) -> dict:
    """跑三档策略并聚合为对比结果。"""
    questions = testset["questions"]
    expired_ids = sorted(index.get_expired_notice_ids(
        date.fromisoformat(testset["reference_date"]) if testset.get("reference_date") else date.today(),
        None,
    ))

    from evaluate_retrieval import evaluate

    runs = {}
    for key, spec in strategies.items():
        runs[key] = evaluate(testset, index, top_k, search_kwargs=spec["search_kwargs"])

    # 陷阱题单独一组：逐题 x 各档 的命中与 rank
    trap_qids = [q["id"] for q in questions if q.get("type") == "expired_trap"]
    trap_rows = {}
    for qid in trap_qids:
        trap_rows[qid] = {}
        for key, run in runs.items():
            row = next(r for r in run["questions"] if r["id"] == qid)
            trap_rows[qid][key] = {
                "hit": row["hit"],
                "first_rank": row["first_rank"] or 0,
                "top_notice_ids": row["top_notice_ids"],
            }

    trap_summary = {}
    for key, run in runs.items():
        agg = run["by_type"].get("expired_trap")
        trap_summary[key] = {
            "count": agg["count"] if agg else 0,
            "hit": agg["hit"] if agg else 0.0,
            "recall": agg["recall"] if agg else 0.0,
            "mrr": agg["mrr"] if agg else 0.0,
        }

    # 结论：陷阱题哪档最优 / 全体哪档最优（先比 hit，平局比 mrr）
    best_trap = max(
        runs.keys(),
        key=lambda k: (trap_summary[k]["hit"], trap_summary[k]["mrr"]),
    )
    best_overall = max(
        runs.keys(),
        key=lambda k: (runs[k]["overall"]["hit"], runs[k]["overall"]["mrr"]),
    )

    # 语义/专有名词题中期望来源已过期的题（C 档的连带误伤面）
    collateral = []
    for q in questions:
        if q.get("type") in ("semantic", "proper_noun"):
            expired_expected = [nid for nid in (q.get("expected_notice_ids") or []) if nid in expired_ids]
            if expired_expected:
                collateral.append(
                    {"id": q["id"], "type": q["type"], "question": q["question"],
                     "expected_expired": expired_expected}
                )

    return {
        "top_k": top_k,
        "reference_date": testset.get("reference_date", ""),
        "expired_notice_ids": expired_ids,
        "expired_count": len(expired_ids),
        "index_stats": index.stats(),
        "strategies": {
            key: {
                "label": spec["label"],
                "overall": runs[key]["overall"],
                "by_type": runs[key]["by_type"],
                "questions": runs[key]["questions"],
            }
            for key, spec in strategies.items()
        },
        "trap_comparison": {
            "question_ids": trap_qids,
            "rows": trap_rows,
            "summary": trap_summary,
        },
        "collateral": collateral,
        "conclusion": {
            "best_for_trap": best_trap,
            "best_overall": best_overall,
            "trap_summary": {k: {"hit": trap_summary[k]["hit"], "mrr": trap_summary[k]["mrr"]} for k in trap_summary},
            "overall_summary": {k: {"hit": runs[k]["overall"]["hit"], "mrr": runs[k]["overall"]["mrr"]} for k in runs},
        },
    }


def render_markdown(result: dict, meta: dict) -> str:
    """生成三档对比表 markdown。"""
    lines = []
    lines.append(f"# 过期处理三档实验对比 (top_k={result['top_k']})")
    lines.append("")
    lines.append(f"- 运行时间：{meta['run_at']}")
    lines.append(f"- 参考日期：{result['reference_date']}（测试集 reference_date）")
    lines.append(f"- 默认有效期 expire_days：{meta['expire_days']}（无 deadline 按 published_at 兜底）")
    lines.append(f"- 降权参数：decay = 1/(1+{meta['decay_strength']}*过期天数)，候选池 = k×{meta['candidate_factor']}")
    stats = result["index_stats"]
    lines.append(f"- 索引：`{stats.get('persist_dir', '-')}` 共 {stats.get('chunks', '-')} chunks")
    lines.append(f"- 过期通知数：{result['expired_count']}（{', '.join(map(str, result['expired_notice_ids']))}）")
    lines.append("")

    order = ["semantic", "proper_noun", "expired_trap", "pollution"]

    # 表 1：三档 × 全体/各题型
    lines.append("## 三档策略对比（Hit@K / MRR@K）")
    lines.append("")
    lines.append("| 策略 | 全体 | 语义理解 | 专有名词 | **过期陷阱** | 污染用例 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for key in ("A", "B", "C"):
        s = result["strategies"][key]
        o = s["overall"]
        cells = [s["label"], f"{_pct(o['hit'])} / {_fmt_mrr(o['mrr'])}"]
        for t in order:
            g = s["by_type"].get(t, {"count": 0, "hit": 0.0, "mrr": 0.0})
            cells.append(f"{_pct(g['hit'])} / {_fmt_mrr(g['mrr'])}")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("> 单元格格式：Hit@K / MRR@K")
    lines.append("")

    # 表 2：陷阱题单独一组
    lines.append("## 过期陷阱题单独一组")
    lines.append("")
    lines.append("| 题 | A 命中/rank | B 命中/rank | C 命中/rank |")
    lines.append("| --- | --- | --- | --- |")
    for qid in result["trap_comparison"]["question_ids"]:
        cells = [qid]
        for key in ("A", "B", "C"):
            r = result["trap_comparison"]["rows"][qid][key]
            cells.append(f"{'是' if r['hit'] else '否'} / {r['first_rank'] or '-'}")
        lines.append("| " + " | ".join(cells) + " |")
    tsum = result["trap_comparison"]["summary"]
    agg_cells = ["**汇总**"]
    for key in ("A", "B", "C"):
        agg_cells.append(f"**Hit {_pct(tsum[key]['hit'])} / MRR {_fmt_mrr(tsum[key]['mrr'])}**")
    lines.append("| " + " | ".join(agg_cells) + " |")
    lines.append("")

    # 表 3：C 档连带误伤面
    if result["collateral"]:
        lines.append("## 过期排除（C）的连带影响：期望来源已过期的非陷阱题")
        lines.append("")
        lines.append("| 题 | 题型 | 期望来源（已过期） |")
        lines.append("| --- | --- | --- |")
        for c in result["collateral"]:
            lines.append(f"| {c['id']} | {TYPE_LABELS.get(c['type'], c['type'])} | {', '.join(map(str, c['expected_expired']))} |")
        lines.append("")
        lines.append("> 这些题目问的是历史/旧文档相关内容，其唯一正确来源按兜底规则被判为过期。策略 C 会一并剔除，导致命中下降。")
        lines.append("")

    # 结论
    c = result["conclusion"]
    lines.append("## 结论")
    lines.append("")
    lines.append(f"- **陷阱题召回**：最优为 **策略 {c['best_for_trap']}**")
    for k in ("A", "B", "C"):
        lines.append(
            f"    - 策略 {k}：Hit={_pct(c['trap_summary'][k]['hit'])}，MRR={_fmt_mrr(c['trap_summary'][k]['mrr'])}"
        )
    lines.append(f"- **全体召回**：最优为 **策略 {c['best_overall']}**")
    for k in ("A", "B", "C"):
        lines.append(
            f"    - 策略 {k}：Hit={_pct(c['overall_summary'][k]['hit'])}，MRR={_fmt_mrr(c['overall_summary'][k]['mrr'])}"
        )
    lines.append("")
    return "\n".join(lines)


def report(result: dict, meta: dict, strategies: dict) -> None:
    """打印控制台对比表。"""
    print("\n" + "=" * 90)
    print(f"过期处理三档实验 (top_k={result['top_k']}, reference_date={result['reference_date']}, expire_days={meta['expire_days']})")
    print("=" * 90)
    stats = result["index_stats"]
    print(f"索引: {stats.get('persist_dir', '-')}  共 {stats.get('chunks', '-')} chunks")
    print(f"过期通知: {result['expired_count']} 条")

    print("\n三档对比（全体 + 各题型，Hit@K / MRR@K）：")
    print(f"{'策略':<26}{'全体':<14}{'语义':<14}{'专名':<14}{'陷阱':<14}{'污染':<14}")
    print("-" * 90)
    order = ["semantic", "proper_noun", "expired_trap", "pollution"]
    for key in ("A", "B", "C"):
        s = result["strategies"][key]
        o = s["overall"]
        cells = [s["label"], f"{_pct(o['hit'])}/{_fmt_mrr(o['mrr'])}"]
        for t in order:
            g = s["by_type"].get(t, {"count": 0, "hit": 0.0, "mrr": 0.0})
            cells.append(f"{_pct(g['hit'])}/{_fmt_mrr(g['mrr'])}")
        print(f"{cells[0]:<26}{cells[1]:<14}{cells[2]:<14}{cells[3]:<14}{cells[4]:<14}{cells[5]:<14}")

    print("\n过期陷阱题单独一组：")
    print(f"{'题':<6}{'A 命中/rank':<16}{'B 命中/rank':<16}{'C 命中/rank':<16}")
    print("-" * 54)
    for qid in result["trap_comparison"]["question_ids"]:
        cells = [qid]
        for key in ("A", "B", "C"):
            r = result["trap_comparison"]["rows"][qid][key]
            cells.append(f"{'是' if r['hit'] else '否'} / {r['first_rank'] or '-'}")
        print(f"{cells[0]:<6}{cells[1]:<16}{cells[2]:<16}{cells[3]:<16}")
    tsum = result["trap_comparison"]["summary"]
    print(
        f"{'汇总':<6}{_pct(tsum['A']['hit']) + ' / ' + _fmt_mrr(tsum['A']['mrr']):<16}"
        f"{_pct(tsum['B']['hit']) + ' / ' + _fmt_mrr(tsum['B']['mrr']):<16}"
        f"{_pct(tsum['C']['hit']) + ' / ' + _fmt_mrr(tsum['C']['mrr']):<16}"
    )

    if result["collateral"]:
        print("\nC 档连带影响（期望来源已过期的非陷阱题）：")
        for c in result["collateral"]:
            print(f"  ✗ {c['id']}（{TYPE_LABELS.get(c['type'], c['type'])}）期望 {c['expected_expired']} 被过滤")

    c = result["conclusion"]
    print("\n结论：")
    print(f"  陷阱题召回最优: 策略 {c['best_for_trap']}（A={_pct(c['trap_summary']['A']['hit'])} "
          f"B={_pct(c['trap_summary']['B']['hit'])} C={_pct(c['trap_summary']['C']['hit'])}）")
    print(f"  全体召回最优:   策略 {c['best_overall']}（A={_pct(c['overall_summary']['A']['hit'])} "
          f"B={_pct(c['overall_summary']['B']['hit'])} C={_pct(c['overall_summary']['C']['hit'])}）")
    print("=" * 90)


def consistency_check(result: dict, strategies: dict) -> None:
    """A 档应与 2.2 基线一致；不一致则告警。"""
    a_overall = result["strategies"]["A"]["overall"]
    a_trap = result["strategies"]["A"]["by_type"].get("expired_trap", {"hit": 0.0})
    mismatches = []
    if abs(a_overall["hit"] - BASELINE["overall_hit"]) > 0.001:
        mismatches.append(f"全体 Hit={_pct(a_overall['hit'])} vs 基线 {_pct(BASELINE['overall_hit'])}")
    if abs(a_trap["hit"] - BASELINE["trap_hit"]) > 0.001:
        mismatches.append(f"陷阱 Hit={_pct(a_trap['hit'])} vs 基线 {_pct(BASELINE['trap_hit'])}")
    if abs(a_overall["mrr"] - BASELINE["mrr"]) > 0.005:
        mismatches.append(f"全体 MRR={_fmt_mrr(a_overall['mrr'])} vs 基线 {_fmt_mrr(BASELINE['mrr'])}")
    if mismatches:
        print("!! 一致性自检：A 档与 2.2 基线不一致（可能索引状态或语料变了）：")
        for m in mismatches:
            print(f"   - {m}")
    else:
        print("A 档与 2.2 基线一致（全体 60% / 陷阱 40% / MRR 0.412）✓")


def main():
    parser = argparse.ArgumentParser(description="过期处理三档实验对比")
    parser.add_argument("--top-k", type=int, default=5, help="检索 Top-K（默认 5，与测试集约定一致）")
    parser.add_argument("--expire-days", type=int, default=None,
                        help="无 deadline 通知的默认有效期（默认读 config crawl.expire_days=90）")
    parser.add_argument("--reference-date", type=str, default=None,
                        help="过期判定参考日期 YYYY-MM-DD（默认取测试集 reference_date）")
    parser.add_argument("--decay-strength", type=float, default=0.05,
                        help="降权系数 decay=1/(1+strength*过期天数)（默认 0.05）")
    parser.add_argument("--candidate-factor", type=int, default=3,
                        help="降权重排候选池倍率 k*factor（默认 3）")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=f"结果落盘目录（默认 {DEFAULT_OUTPUT_DIR}）")
    parser.add_argument("--no-report", action="store_true", help="只写 JSON，不写 markdown 报告")
    parser.add_argument("--rebuild-corpus", action="store_true",
                        help="用测试集 corpus 的 27 条通知重建向量索引（默认只读现有索引）")
    args = parser.parse_args()

    if not TESTSET_PATH.exists():
        print(f"!! 测试集不存在: {TESTSET_PATH}")
        return

    testset = json.loads(TESTSET_PATH.read_text(encoding="utf-8"))

    reference_date = (
        date.fromisoformat(args.reference_date)
        if args.reference_date
        else date.fromisoformat(testset.get("reference_date") or date.today().isoformat())
    )

    if args.expire_days is not None:
        expire_days = args.expire_days
    else:
        from config.store import ConfigStore

        expire_days = ConfigStore.get_instance().get_crawl().expire_days

    if args.rebuild_corpus:
        from storage.db import get_connection

        corpus_ids = [n["id"] for n in testset["corpus"]]
        conn = get_connection()
        placeholders = ",".join("?" * len(corpus_ids))
        rows = conn.execute(
            f"SELECT * FROM notices WHERE id IN ({placeholders})",
            corpus_ids,
        ).fetchall()
        conn.close()
        notices = [dict(r) for r in rows]
        print(f"重建索引: 测试集 corpus {len(corpus_ids)} 条，DB 命中 {len(notices)} 条")
        index = get_vector_index()
        index.rebuild(notices)
        print(f"索引完成: {index.count()} chunks")
    else:
        index = get_vector_index()

    strategies = build_strategies(reference_date, expire_days, args.decay_strength, args.candidate_factor)
    result = build_comparison(testset, index, args.top_k, strategies)

    meta = {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "expire_days": expire_days,
        "decay_strength": args.decay_strength,
        "candidate_factor": args.candidate_factor,
    }

    # 落盘
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{ts}_comparison.json"
    json_path.write_text(
        json.dumps({"meta": meta, **result}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md_path = None
    if not args.no_report:
        md_path = output_dir / f"{ts}_comparison.md"
        md_path.write_text(render_markdown(result, meta), encoding="utf-8")

    consistency_check(result, strategies)
    report(result, meta, strategies)

    print(f"\n结果已落盘: {json_path}")
    if md_path:
        print(f"           {md_path}")


if __name__ == "__main__":
    main()
