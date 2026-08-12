"""M2.2 纯向量检索基线评测。

读取 data/retrieval_testset.json，对每题跑 Chroma 语义检索（Top-K），
按测试集 judge_conventions 判定命中，输出基线指标与按题型分组结果。

用法：
    python evaluate_retrieval.py                    # 默认 top_k=5，结果落 data/eval/retrieval/
    python evaluate_retrieval.py --top-k 10          # 换 K
    python evaluate_retrieval.py --no-report         # 只写 JSON，不落 markdown 报告

输出：
    - 控制台基线表 + 每题明细 + 短板结论
    - data/eval/retrieval/<时间戳>_results.json   （机器可读，供后续对比）
    - data/eval/retrieval/<时间戳>_report.md      （可读基线表）

本脚本为纯向量基线：不调用 LLM，只评估检索阶段（Top-K 是否召回期望来源）。
pollution 题（Q19/Q20）仅评估"删除前 Top5 命中期望"第一阶段，第二阶段见模块 2.5。
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

from storage.vectorstore import VectorIndex, get_vector_index

TESTSET_PATH = Path(__file__).parent / "data" / "retrieval_testset.json"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "data" / "eval" / "retrieval"

TYPE_LABELS = {
    "semantic": "语义理解",
    "proper_noun": "专有名词",
    "expired_trap": "过期陷阱",
    "pollution": "污染用例",
}


def _dedup_top_notice_ids(docs: list) -> list[int]:
    """按首次出现顺序去重 Top-K 结果中的 notice_id。"""
    seen: set[int] = set()
    result: list[int] = []
    for doc in docs:
        nid = doc.metadata.get("notice_id")
        if nid is None or nid in seen:
            continue
        seen.add(nid)
        result.append(nid)
    return result


def evaluate_question(index: VectorIndex, q: dict, top_k: int, search_kwargs: dict | None = None) -> dict:
    """对单题跑检索并判定。

    search_kwargs: 透传给 index.search 的额外参数（模块 2.3 策略实验用），默认 None 即基线。
    """
    question = q["question"]
    expected = q.get("expected_notice_ids") or []
    must_not = q.get("must_not_notice_ids") or []
    require_all = bool(q.get("require_all", False))
    deletion_target = bool(q.get("deletion_target", False))
    qtype = q.get("type", "semantic")

    docs = index.search(question, k=top_k, **(search_kwargs or {}))
    top_ids = _dedup_top_notice_ids(docs)

    # 期望命中情况
    expected_found = [nid for nid in expected if nid in top_ids]
    hit = bool(expected_found)
    if require_all:
        hit = len(expected_found) == len(expected)

    # MRR：首个期望来源在 Top5 的 1/rank（按去重后的 notice 顺序，1-based）
    first_rank = 0
    for idx, nid in enumerate(top_ids, start=1):
        if nid in expected:
            first_rank = idx
            break
    mrr = 1.0 / first_rank if first_rank else 0.0

    # Recall@K：命中期望数 / 期望总数
    recall = len(expected_found) / len(expected) if expected else 0.0

    # 污染诊断：非 pollution 题检查 must_not 是否混入（如 Q04 的 206）
    polluted = False
    if not deletion_target and must_not:
        polluted = any(nid in top_ids for nid in must_not)

    # 期望来源是否在索引中存在（排除语料/索引不一致的干扰）
    expected_in_index = sum(1 for nid in expected if _notice_in_index(index, nid))

    return {
        "id": q["id"],
        "type": qtype,
        "question": question,
        "top_notice_ids": top_ids,
        "expected_notice_ids": expected,
        "expected_found": expected_found,
        "expected_in_index": expected_in_index,
        "require_all": require_all,
        "deletion_target": deletion_target,
        "hit": hit,
        "first_rank": first_rank,
        "mrr": mrr,
        "recall": recall,
        "polluted": polluted,
    }


_NOTICE_CACHE: set[int] | None = None


def _notice_in_index(index: VectorIndex, notice_id: int) -> bool:
    """判断某通知是否已被索引（缓存结果）。"""
    global _NOTICE_CACHE
    if _NOTICE_CACHE is None:
        _NOTICE_CACHE = _load_indexed_notice_ids(index)
    return notice_id in _NOTICE_CACHE


def _load_indexed_notice_ids(index: VectorIndex) -> set[int]:
    """从 Chroma 全量拉取 notice_id 集合（用于判定期望来源是否已索引）。"""
    try:
        collection = index._get_store()._collection
        all_meta = collection.get(include=["metadatas"])["metadatas"]
        return {m["notice_id"] for m in all_meta if m.get("notice_id") is not None}
    except Exception as e:  # noqa: BLE001
        print(f"!! 拉取索引 notice_id 集合失败: {e}")
        return set()


def evaluate(testset: dict, index: VectorIndex, top_k: int, search_kwargs: dict | None = None) -> dict:
    """跑完全部题目，聚合指标。

    search_kwargs: 透传给 index.search 的额外参数（模块 2.3 策略实验用），默认 None 即基线。
    """
    questions = testset["questions"]
    rows = [evaluate_question(index, q, top_k, search_kwargs) for q in questions]

    def aggregate(sub_rows: list[dict]) -> dict:
        n = len(sub_rows)
        if not n:
            return {"count": 0, "hit": 0.0, "recall": 0.0, "mrr": 0.0}
        hit = sum(1 for r in sub_rows if r["hit"]) / n
        recall = sum(r["recall"] for r in sub_rows) / n
        mrr = sum(r["mrr"] for r in sub_rows) / n
        return {"count": n, "hit": hit, "recall": recall, "mrr": mrr}

    groups: dict[str, dict] = {}
    for r in rows:
        groups.setdefault(r["type"], []).append(r)

    by_type = {t: aggregate(sub) for t, sub in sorted(groups.items())}

    return {
        "testset": testset.get("说明", ""),
        "reference_date": testset.get("reference_date", ""),
        "top_k": top_k,
        "index_stats": index.stats(),
        "overall": aggregate(rows),
        "by_type": by_type,
        "questions": rows,
    }


def _pct(x: float) -> str:
    return f"{x:.0%}"


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def report(result: dict) -> None:
    """打印控制台报告。"""
    print("\n" + "=" * 80)
    print(f"纯向量检索基线评估报告  (top_k={result['top_k']})")
    print("=" * 80)
    stats = result["index_stats"]
    print(f"索引: {stats.get('persist_dir', '-')}  共 {stats.get('chunks', '-')} chunks")
    print(f"测试集: {result['reference_date']}  共 {result['overall']['count']} 题")

    # 基线表
    print("\n按题型分组指标：")
    print(f"{'题型':<10}{'题数':<6}{'Hit@K':<10}{'Recall@K':<10}{'MRR@K':<10}")
    print("-" * 46)
    order = ["semantic", "proper_noun", "expired_trap", "pollution"]
    for t in order:
        g = result["by_type"].get(t, {"count": 0, "hit": 0.0, "recall": 0.0, "mrr": 0.0})
        label = TYPE_LABELS.get(t, t)
        print(
            f"{label:<10}{g['count']:<6}{_pct(g['hit']):<10}"
            f"{_pct(g['recall']):<10}{g['mrr']:<10.3f}"
        )
    o = result["overall"]
    print("-" * 46)
    print(f"{'全体':<10}{o['count']:<6}{_pct(o['hit']):<10}{_pct(o['recall']):<10}{o['mrr']:<10.3f}")

    # 每题明细
    print("\n每题明细：")
    print(f"{'题':<6}{'题型':<10}{'命中':<4}{'rank':<6}{'Recall':<8}{'Top5 notice_ids'}")
    print("-" * 80)
    for r in result["questions"]:
        label = TYPE_LABELS.get(r["type"], r["type"])
        rank = r["first_rank"] or "-"
        ids = ",".join(str(nid) for nid in r["top_notice_ids"])
        flags = []
        if r["polluted"]:
            flags.append("含污染")
        if r["expected_in_index"] != len(r["expected_notice_ids"]):
            flags.append(f"期望{len(r['expected_notice_ids'])}-索引{r['expected_in_index']}")
        flag_str = (" " + "|".join(flags)) if flags else ""
        print(
            f"{r['id']:<6}{label:<10}{_mark(r['hit']):<4}{str(rank):<6}"
            f"{_pct(r['recall']):<8}{ids}{flag_str}"
        )

    # 短板结论
    print("\n短板结论：")
    baseline = result["by_type"].get("semantic", {"hit": 1.0, "recall": 1.0, "mrr": 1.0})
    weak = []
    for t in order:
        g = result["by_type"].get(t)
        if g is None or not g["count"]:
            continue
        if t == "semantic":
            continue
        if g["hit"] < baseline["hit"] or g["recall"] < baseline["recall"] or g["mrr"] < baseline["mrr"]:
            weak.append((TYPE_LABELS.get(t, t), g))
    if weak:
        for label, g in weak:
            print(
                f"  ✗ {label}题：Hit@K={_pct(g['hit'])} (全体 {_pct(result['overall']['hit'])})，"
                f"MRR={g['mrr']:.3f}"
            )
    else:
        print("  各题型均不低于 semantic 基线。")
    print("=" * 80)


def render_markdown(result: dict) -> str:
    """生成可读基线表 markdown。"""
    lines = []
    lines.append(f"# 纯向量检索基线评估 (top_k={result['top_k']})")
    lines.append("")
    lines.append(f"- 运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 测试集：{result['reference_date']}（{result['testset']}）")
    stats = result["index_stats"]
    lines.append(f"- 索引：`{stats.get('persist_dir', '-')}` 共 {stats.get('chunks', '-')} chunks")
    lines.append("")

    lines.append("## 按题型分组")
    lines.append("")
    lines.append("| 题型 | 题数 | Hit@K | Recall@K | MRR@K |")
    lines.append("| --- | --- | --- | --- | --- |")
    order = ["semantic", "proper_noun", "expired_trap", "pollution"]
    for t in order:
        g = result["by_type"].get(t, {"count": 0, "hit": 0.0, "recall": 0.0, "mrr": 0.0})
        lines.append(
            f"| {TYPE_LABELS.get(t, t)} | {g['count']} | {_pct(g['hit'])} | "
            f"{_pct(g['recall'])} | {g['mrr']:.3f} |"
        )
    o = result["overall"]
    lines.append(f"| **全体** | **{o['count']}** | **{_pct(o['hit'])}** | **{_pct(o['recall'])}** | **{o['mrr']:.3f}** |")
    lines.append("")

    lines.append("## 每题明细")
    lines.append("")
    lines.append("| 题 | 题型 | 命中 | rank | Recall | Top5 notice_ids |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in result["questions"]:
        rank = r["first_rank"] or "-"
        ids = ", ".join(str(nid) for nid in r["top_notice_ids"]) or "-"
        lines.append(
            f"| {r['id']} | {TYPE_LABELS.get(r['type'], r['type'])} | "
            f"{'是' if r['hit'] else '否'} | {rank} | {_pct(r['recall'])} | {ids} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_results(result: dict, output_dir: Path, write_report: bool) -> tuple[Path, Path | None]:
    """落盘：时间戳 JSON + 可选 markdown 报告。返回 (json_path, md_path)。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{ts}_results.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = None
    if write_report:
        md_path = output_dir / f"{ts}_report.md"
        md_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, md_path


def main():
    parser = argparse.ArgumentParser(description="纯向量检索基线评测")
    parser.add_argument("--top-k", type=int, default=5, help="检索 Top-K（默认 5）")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"结果落盘目录（默认 {DEFAULT_OUTPUT_DIR}）",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="只写 results.json，不写 markdown 报告",
    )
    parser.add_argument(
        "--rebuild-corpus",
        action="store_true",
        help="用测试集 corpus 的 27 条通知重建向量索引（默认只读现有索引）",
    )
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

    result = evaluate(testset, index, top_k=args.top_k)

    json_path, md_path = write_results(result, args.output_dir, write_report=not args.no_report)
    report(result)
    print(f"\n结果已落盘: {json_path}")
    if md_path:
        print(f"           {md_path}")


if __name__ == "__main__":
    main()
