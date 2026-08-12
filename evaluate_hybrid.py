"""M2.4 混合检索（BM25 + RRF）对比实验。

对同一测试集（data/retrieval_testset.json）各跑一遍：
    vector：纯向量检索（strategy=none，即 2.2 基线）
    hybrid：向量 + BM25 用 RRF（k=60）融合

指标沿用 evaluate_retrieval 约定：Hit@K / Recall@K / MRR@K，全体 + 按题型分组。

上/不上决策规则（与 W2 门控 #4 一致）：
    通过 = MRR 与 Recall@K 相对提升均 ≥10% 且各题型（hit/recall/mrr）无回归
    （容差 0.005，且全体 Hit@K 不降）。
    否则输出"不上"，并列出未达标指标与短板题型。

用法：
    python evaluate_hybrid.py                          # 默认 top_k=5，结果落 data/eval/hybrid/
    python evaluate_hybrid.py --rebuild-corpus         # 用测试集 27 条语料重建索引再对比
    python evaluate_hybrid.py --candidate-k 40         # 换每路候选池大小
    python evaluate_hybrid.py --rrf-k 20               # 换 RRF 常数
    python evaluate_hybrid.py --no-report              # 只写 JSON，不写 markdown

输出：
    - 控制台对比表 + 上/不上结论
    - data/eval/hybrid/<时间戳>_comparison.json   （机器可读，含每题明细）
    - data/eval/hybrid/<时间戳>_comparison.md     （可读对比表）

本实验只评估检索阶段，不调用 LLM。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

# Windows 控制台默认 cp1252 无法打印中文，统一用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from evaluate_retrieval import TYPE_LABELS, evaluate
from storage.hybrid import DEFAULT_CANDIDATE_K, DEFAULT_RRF_K, HybridIndex
from storage.vectorstore import VectorIndex, get_vector_index

TESTSET_PATH = Path(__file__).parent / "data" / "retrieval_testset.json"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "data" / "eval" / "hybrid"

# 2.2 基线留档值，用于 vector 路一致性自检
BASELINE = {"overall_hit": 0.60, "trap_hit": 0.40, "mrr": 0.412}

# 门控 #4：相对提升阈值与回归容差
MIN_REL_GAIN = 0.10  # MRR / Recall@K 相对提升必须 ≥10%
REGRESSION_EPS = 0.005  # 各题型指标低于基线的容差

TYPE_ORDER = ["semantic", "proper_noun", "expired_trap", "pollution"]


def _pct(x: float) -> str:
    return f"{x:.0%}"


def _fmt_mrr(x: float) -> str:
    return f"{x:.3f}"


def _delta_str(new: float, old: float) -> str:
    if old == 0:
        return "-"
    rel = (new - old) / old
    return f"{rel:+.0%}"


def compute_decision(vec: dict, hyb: dict) -> dict:
    """按门控 #4 判定上/不上，返回结论明细。"""
    v, h = vec["overall"], hyb["overall"]

    def rel(a: float, b: float) -> float:
        return (a - b) / b if b > 0 else 0.0

    mrr_gain = rel(h["mrr"], v["mrr"])
    recall_gain = rel(h["recall"], v["recall"])
    hit_gain = rel(h["hit"], v["hit"])

    # 各题型回归检查：hybrid 任一 hit/recall/mrr 低于 vector 超过容差 → 回归
    regressions = []
    for t in TYPE_ORDER:
        gv = vec["by_type"].get(t, {"count": 0, "hit": 0.0, "recall": 0.0, "mrr": 0.0})
        gh = hyb["by_type"].get(t, {"count": 0, "hit": 0.0, "recall": 0.0, "mrr": 0.0})
        if not gv["count"]:
            continue
        for metric in ("hit", "recall", "mrr"):
            if gh[metric] < gv[metric] - REGRESSION_EPS:
                regressions.append({"type": t, "metric": metric,
                                    "vec": gv[metric], "hyb": gh[metric]})

    overall_hit_regress = h["hit"] < v["hit"] - REGRESSION_EPS

    pass_improvement = mrr_gain >= MIN_REL_GAIN and recall_gain >= MIN_REL_GAIN
    no_regression = not regressions and not overall_hit_regress
    adopt = pass_improvement and no_regression

    return {
        "min_rel_gain": MIN_REL_GAIN,
        "mrr": {"vec": v["mrr"], "hyb": h["mrr"], "rel_gain": mrr_gain,
                "pass": mrr_gain >= MIN_REL_GAIN},
        "recall": {"vec": v["recall"], "hyb": h["recall"], "rel_gain": recall_gain,
                   "pass": recall_gain >= MIN_REL_GAIN},
        "hit": {"vec": v["hit"], "hyb": h["hit"], "rel_gain": hit_gain},
        "regressions": regressions,
        "overall_hit_regress": overall_hit_regress,
        "pass_improvement": pass_improvement,
        "no_regression": no_regression,
        "adopt": adopt,
    }


def build_comparison(testset: dict, index: VectorIndex, top_k: int,
                     candidate_k: int, rrf_k: int) -> dict:
    """跑纯向量与混合各一遍，聚合对比结果。"""
    vec = evaluate(testset, index, top_k)
    hyb = evaluate(testset, HybridIndex(index, rrf_k=rrf_k, candidate_k=candidate_k), top_k)

    decision = compute_decision(vec, hyb)

    # 每题横向对比：vector vs hybrid 的命中与 rank
    per_question = []
    vec_rows = {r["id"]: r for r in vec["questions"]}
    hyb_rows = {r["id"]: r for r in hyb["questions"]}
    for qid in vec_rows:
        v, h = vec_rows[qid], hyb_rows[qid]
        per_question.append({
            "id": qid,
            "type": v["type"],
            "question": v["question"],
            "vec": {"hit": v["hit"], "first_rank": v["first_rank"] or 0,
                    "top_notice_ids": v["top_notice_ids"]},
            "hyb": {"hit": h["hit"], "first_rank": h["first_rank"] or 0,
                    "top_notice_ids": h["top_notice_ids"]},
            "turned_hit": (not v["hit"]) and h["hit"],
            "turned_miss": v["hit"] and (not h["hit"]),
        })

    return {
        "top_k": top_k,
        "candidate_k": candidate_k,
        "rrf_k": rrf_k,
        "reference_date": testset.get("reference_date", ""),
        "testset": testset.get("说明", ""),
        "index_stats": index.stats(),
        "vector": vec,
        "hybrid": hyb,
        "per_question": per_question,
        "decision": decision,
    }


def render_markdown(result: dict) -> str:
    """生成对比表 markdown。"""
    lines = []
    lines.append(f"# 混合检索对比：纯向量 vs BM25+RRF (top_k={result['top_k']})")
    lines.append("")
    lines.append(f"- 运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 测试集：{result['reference_date']}（{result['testset']}）")
    lines.append(f"- RRF 常数 k={result['rrf_k']}，每路候选池 {result['candidate_k']}")
    stats = result["index_stats"]
    lines.append(f"- 索引：`{stats.get('persist_dir', '-')}` 共 {stats.get('chunks', '-')} chunks")
    lines.append("")

    # 表 1：全体 + 各题型对比
    lines.append("## 全体 + 按题型对比（Hit@K / Recall@K / MRR@K）")
    lines.append("")
    lines.append("| 组别 | 纯向量 Hit/Rec/MRR | 混合 Hit/Rec/MRR | ΔRec | ΔMRR |")
    lines.append("| --- | --- | --- | --- | --- |")

    rows = [("全体", "overall")] + [(TYPE_LABELS[t], t) for t in TYPE_ORDER]
    for label, key in rows:
        v = result["vector"]["by_type"].get(key) if key != "overall" else result["vector"]["overall"]
        h = result["hybrid"]["by_type"].get(key) if key != "overall" else result["hybrid"]["overall"]
        v = v or {"count": 0, "hit": 0.0, "recall": 0.0, "mrr": 0.0}
        h = h or {"count": 0, "hit": 0.0, "recall": 0.0, "mrr": 0.0}
        vec_cell = " / ".join([_pct(v["hit"]), _pct(v["recall"]), _fmt_mrr(v["mrr"])])
        hyb_cell = " / ".join([_pct(h["hit"]), _pct(h["recall"]), _fmt_mrr(h["mrr"])])
        bold = "**" if key == "overall" else ""
        lines.append(
            f"| {bold}{label}{bold} | {vec_cell} | {hyb_cell} | "
            f"{_delta_str(h['recall'], v['recall'])} | {_delta_str(h['mrr'], v['mrr'])} |"
        )
    lines.append("")
    lines.append("> 单元格格式：Hit@K / Recall@K / MRR@K；ΔRec / ΔMRR 为相对变化（hybrid - vector）/vector")
    lines.append("")

    # 表 2：每题明细
    lines.append("## 每题明细")
    lines.append("")
    lines.append("| 题 | 题型 | 纯向量 命中/rank | 混合 命中/rank | 变化 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in result["per_question"]:
        change = "→命中" if r["turned_hit"] else ("→失手" if r["turned_miss"] else "=")
        lines.append(
            f"| {r['id']} | {TYPE_LABELS.get(r['type'], r['type'])} | "
            f"{'是' if r['vec']['hit'] else '否'}/{r['vec']['first_rank'] or '-'} | "
            f"{'是' if r['hyb']['hit'] else '否'}/{r['hyb']['first_rank'] or '-'} | {change} |"
        )
    lines.append("")

    # 结论
    d = result["decision"]
    lines.append("## 结论")
    lines.append("")
    lines.append(f"- **判定：{'上（保留混合检索）' if d['adopt'] else '不上（维持纯向量）'}**")
    lines.append("")
    lines.append("门控 #4 检查（MRR 与 Recall@K 相对提升均 ≥10% 且无回归）：")
    lines.append(f"- MRR：{_fmt_mrr(d['mrr']['vec'])} → {_fmt_mrr(d['mrr']['hyb'])}（{d['mrr']['rel_gain']:+.0%}，{'达标' if d['mrr']['pass'] else '未达标'}）")
    lines.append(f"- Recall@K：{_pct(d['recall']['vec'])} → {_pct(d['recall']['hyb'])}（{d['recall']['rel_gain']:+.0%}，{'达标' if d['recall']['pass'] else '未达标'}）")
    lines.append(f"- 全体 Hit@K：{_pct(d['hit']['vec'])} → {_pct(d['hit']['hyb'])}（{d['hit']['rel_gain']:+.0%}）")
    if d["regressions"]:
        lines.append("- 回归项（低于基线超容差）：")
        for reg in d["regressions"]:
            lines.append(
                f"    - {TYPE_LABELS.get(reg['type'], reg['type'])} {reg['metric']}："
                f"{_fmt_mrr(reg['vec']) if reg['metric'] == 'mrr' else _pct(reg['vec'])} → "
                f"{_fmt_mrr(reg['hyb']) if reg['metric'] == 'mrr' else _pct(reg['hyb'])}"
            )
    else:
        lines.append("- 回归项：无")
    lines.append("")
    if d["adopt"]:
        lines.append("> 提升达标且无回归，按门控 #4 结论为「上」；可在 `core/qa.py` 用 `search_mode='hybrid'` 启用。")
    else:
        lines.append("> 提升未达标或存在回归，测不出稳定差异 → 按模块要求明确写「不上」，维持纯向量基线。")
    lines.append("")
    return "\n".join(lines)


def report(result: dict) -> None:
    """打印控制台对比表。"""
    print("\n" + "=" * 96)
    print(f"混合检索对比：纯向量 vs BM25+RRF  (top_k={result['top_k']}, rrf_k={result['rrf_k']}, candidate_k={result['candidate_k']})")
    print("=" * 96)
    stats = result["index_stats"]
    print(f"索引: {stats.get('persist_dir', '-')}  共 {stats.get('chunks', '-')} chunks")

    print("\n全体 + 按题型对比（Hit / Rec / MRR）：")
    print(f"{'组别':<10}{'纯向量':<26}{'混合':<26}{'ΔRec':<8}{'ΔMRR':<8}")
    print("-" * 78)
    for label, key in [("全体", "overall")] + [(TYPE_LABELS[t], t) for t in TYPE_ORDER]:
        v = (result["vector"]["overall"] if key == "overall"
             else result["vector"]["by_type"].get(key, {"hit": 0, "recall": 0, "mrr": 0}))
        h = (result["hybrid"]["overall"] if key == "overall"
             else result["hybrid"]["by_type"].get(key, {"hit": 0, "recall": 0, "mrr": 0}))
        print(f"{label:<10}"
              f"{_pct(v['hit'])}/{_pct(v['recall'])}/{_fmt_mrr(v['mrr']):<14}"
              f"{_pct(h['hit'])}/{_pct(h['recall'])}/{_fmt_mrr(h['mrr']):<14}"
              f"{_delta_str(h['recall'], v['recall']):<8}{_delta_str(h['mrr'], v['mrr']):<8}")

    changed = [r for r in result["per_question"] if r["turned_hit"] or r["turned_miss"]]
    if changed:
        print("\n每题命中变化：")
        for r in changed:
            arrow = "→命中 ✓" if r["turned_hit"] else "→失手 ✗"
            print(f"  {r['id']}（{TYPE_LABELS.get(r['type'], r['type'])}）{arrow}")

    d = result["decision"]
    print("\n" + "=" * 96)
    print(f"门控 #4 判定: {'上（保留混合检索）' if d['adopt'] else '不上（维持纯向量）'}")
    print(f"  MRR      : {_fmt_mrr(d['mrr']['vec'])} → {_fmt_mrr(d['mrr']['hyb'])} ({d['mrr']['rel_gain']:+.0%}, {'达标' if d['mrr']['pass'] else '未达标'})")
    print(f"  Recall@K : {_pct(d['recall']['vec'])} → {_pct(d['recall']['hyb'])} ({d['recall']['rel_gain']:+.0%}, {'达标' if d['recall']['pass'] else '未达标'})")
    print(f"  Hit@K    : {_pct(d['hit']['vec'])} → {_pct(d['hit']['hyb'])} ({d['hit']['rel_gain']:+.0%})")
    if d["regressions"]:
        for reg in d["regressions"]:
            print(f"  回归: {TYPE_LABELS.get(reg['type'], reg['type'])} {reg['metric']} "
                  f"{reg['vec']:.3f} → {reg['hyb']:.3f}")
    print("=" * 96)


def consistency_check(result: dict) -> None:
    """vector 路应与 2.2 基线一致；不一致则告警。"""
    v = result["vector"]["overall"]
    trap = result["vector"]["by_type"].get("expired_trap", {"hit": 0.0})
    mismatches = []
    if abs(v["hit"] - BASELINE["overall_hit"]) > 0.001:
        mismatches.append(f"全体 Hit={_pct(v['hit'])} vs 基线 {_pct(BASELINE['overall_hit'])}")
    if abs(trap["hit"] - BASELINE["trap_hit"]) > 0.001:
        mismatches.append(f"陷阱 Hit={_pct(trap['hit'])} vs 基线 {_pct(BASELINE['trap_hit'])}")
    if abs(v["mrr"] - BASELINE["mrr"]) > 0.005:
        mismatches.append(f"全体 MRR={_fmt_mrr(v['mrr'])} vs 基线 {_fmt_mrr(BASELINE['mrr'])}")
    if mismatches:
        print("!! 一致性自检：纯向量路与 2.2 基线不一致（可能索引状态或语料变了）：")
        for m in mismatches:
            print(f"   - {m}")
    else:
        print("纯向量路与 2.2 基线一致（全体 60% / 陷阱 40% / MRR 0.412）✓")


def main():
    parser = argparse.ArgumentParser(description="混合检索 BM25+RRF 对比实验")
    parser.add_argument("--top-k", type=int, default=5, help="检索 Top-K（默认 5，与测试集约定一致）")
    parser.add_argument("--candidate-k", type=int, default=DEFAULT_CANDIDATE_K,
                        help=f"每路候选池大小（默认 {DEFAULT_CANDIDATE_K}）")
    parser.add_argument("--rrf-k", type=int, default=DEFAULT_RRF_K,
                        help=f"RRF 常数（默认 {DEFAULT_RRF_K}）")
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

    result = build_comparison(testset, index, args.top_k, args.candidate_k, args.rrf_k)

    # 落盘
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{ts}_comparison.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = None
    if not args.no_report:
        md_path = output_dir / f"{ts}_comparison.md"
        md_path.write_text(render_markdown(result), encoding="utf-8")

    consistency_check(result)
    report(result)

    print(f"\n结果已落盘: {json_path}")
    if md_path:
        print(f"           {md_path}")


if __name__ == "__main__":
    main()
