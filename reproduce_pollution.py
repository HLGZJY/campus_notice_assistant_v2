"""模块 2.5：RAG 污染复现脚本（沙箱，不污染真实数据）。

对测试集污染用例（Q19/Q20，删除目标通知 202/205）做两阶段判定：
    ① 删除前：检索 Top-K 须命中目标通知（证明问题确实指向它）；
    ② 删除后：目标通知不得再出现在 Top-K 中（幽灵结果 = 失败）。

运行方式：在临时沙箱中拷贝一份 notices.db + data/chroma，临时把
storage.db.DB_PATH / storage.vectorstore.DEFAULT_PERSIST_DIR 指向副本，
再走真实删除链路（services.admin_service.delete_notice + VectorIndex.search），
全程不触碰真实数据，跑完自动清理。

用法：
    python reproduce_pollution.py                    # 沙箱跑 Q19/Q20 两阶段判定
    python reproduce_pollution.py --targets 202      # 只复现指定通知
    python reproduce_pollution.py --top-k 5
    python reproduce_pollution.py --keep             # 保留沙箱目录（调试用）
    python reproduce_pollution.py --sandbox-dir <dir> # 指定沙箱目录

退出码：0 = 全部通过（删除后检索不到）；1 = 任一阶段失败。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

# Windows 控制台默认 cp1252 无法打印中文，统一用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import storage.db as storage_db
import storage.vectorstore as vectorstore
from services.admin_service import delete_notice
from storage.db import get_connection
from storage.vectorstore import VectorIndex, check_consistency

TESTSET_PATH = Path(__file__).parent / "data" / "retrieval_testset.json"
REAL_DB_PATH = storage_db.DB_PATH
REAL_CHROMA_DIR = vectorstore.DEFAULT_PERSIST_DIR


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


def _load_pollution_cases() -> list[dict]:
    """从测试集挑出 deletion_target=true 的题目。"""
    if not TESTSET_PATH.exists():
        raise SystemExit(f"!! 测试集不存在: {TESTSET_PATH}")
    testset = json.loads(TESTSET_PATH.read_text(encoding="utf-8"))
    cases = [q for q in testset["questions"] if q.get("deletion_target")]
    if not cases:
        raise SystemExit("!! 测试集中没有 deletion_target=true 的题目")
    return cases


def _make_sandbox(sandbox_dir: Path | None) -> Path:
    """拷贝真实库 + Chroma 到沙箱，返回沙箱目录。"""
    if sandbox_dir is None:
        sandbox_dir = Path(tempfile.mkdtemp(prefix="pollution_sandbox_"))
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    # Chroma 可能较大，不复制向量文件无法检索；直接整目录拷贝
    chroma_dst = sandbox_dir / "chroma"
    if not chroma_dst.exists() and REAL_CHROMA_DIR.exists():
        shutil.copytree(REAL_CHROMA_DIR, chroma_dst)
    if not chroma_dst.exists():
        chroma_dst.mkdir(parents=True, exist_ok=True)

    db_dst = sandbox_dir / "notices.db"
    if not db_dst.exists() and REAL_DB_PATH.exists():
        shutil.copy2(REAL_DB_PATH, db_dst)

    # 临时把路径常量指向沙箱副本（真实链路读取的就是这些常量）
    storage_db.DB_PATH = db_dst
    vectorstore.DEFAULT_PERSIST_DIR = chroma_dst
    return sandbox_dir


def _sandbox_has_target(target: int) -> bool:
    index = VectorIndex()
    try:
        data = index._get_store()._collection.get(include=["metadatas"])
        return any(
            (m or {}).get("notice_id") == target for m in (data.get("metadatas") or [])
        )
    except Exception as e:  # noqa: BLE001
        print(f"!! 读取沙箱向量库失败: {e}")
        return False


def _rebuild_sandbox_from_corpus(testset: dict) -> int:
    """用测试集 corpus 的 27 条通知重建沙箱索引（目标缺失时的兜底）。"""
    corpus_ids = [n["id"] for n in testset.get("corpus", [])]
    if not corpus_ids:
        return 0
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(corpus_ids))
        rows = conn.execute(
            f"SELECT * FROM notices WHERE id IN ({placeholders})", corpus_ids
        ).fetchall()
        notices = [dict(r) for r in rows]
    finally:
        conn.close()
    index = VectorIndex()
    result = index.rebuild(notices)
    print(f"[沙箱] 用测试集 corpus 重建索引: {result['notices']} 条 / {result['chunks']} chunks")
    return result["chunks"]


def run_case(case: dict, top_k: int) -> dict:
    """对单个污染用例跑两阶段判定。"""
    target = case["expected_notice_ids"][0]
    question = case["question"]

    print(f"\n=== {case['id']} target=通知#{target} ===")
    print(f"问题: {question}")

    # 阶段①：删除前检索（每次独立创建 VectorIndex，避免跨删除持有 Chroma 客户端）
    docs = VectorIndex().search(question, k=top_k)
    top_ids = _dedup_top_notice_ids(docs)
    phase1_hit = target in top_ids
    print(f"[阶段①删除前] Top{top_k}={top_ids}")
    print(f"  → 命中目标 #{target}: {'✅ 是' if phase1_hit else '❌ 否（该问题未指向目标，跳过删除阶段）'}")

    if not phase1_hit:
        return {
            "id": case["id"], "target": target, "phase1_hit": False,
            "phase1_top": top_ids, "phase2_pass": None,
        }

    # 删除（真实链路：services.admin_service.delete_notice）
    result = delete_notice(target)
    if not result.get("ok"):
        raise SystemExit(f"!! 沙箱删除通知 #{target} 失败: {result}")

    # 阶段②：删除后检索
    docs2 = VectorIndex().search(question, k=top_k)
    top_ids2 = _dedup_top_notice_ids(docs2)
    phase2_pass = target not in top_ids2
    print(f"[阶段②删除后] Top{top_k}={top_ids2}")
    print(f"  → 目标 #{target} 已检索不到: {'✅ 是' if phase2_pass else '❌ 否（幽灵结果，失败）'}")

    return {
        "id": case["id"], "target": target, "phase1_hit": True,
        "phase1_top": top_ids, "phase2_pass": phase2_pass, "phase2_top": top_ids2,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RAG 污染复现：删除通知后验证检索不到（沙箱，不污染真实数据）"
    )
    parser.add_argument(
        "--targets",
        type=str,
        default=None,
        help="只复现指定通知 ID，逗号分隔（默认测试集全部污染用例 Q19/Q20）",
    )
    parser.add_argument("--top-k", type=int, default=5, help="检索 Top-K（默认 5）")
    parser.add_argument(
        "--sandbox-dir",
        type=Path,
        default=None,
        help="沙箱目录（默认自动创建临时目录）",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="保留沙箱目录不清理（调试用）",
    )
    args = parser.parse_args()

    cases = _load_pollution_cases()
    if args.targets:
        wanted = {int(x) for x in args.targets.split(",") if x.strip()}
        cases = [c for c in cases if c["expected_notice_ids"][0] in wanted]
        if not cases:
            print(f"!! 指定 targets={wanted} 没有对应污染用例")
            return 1

    testset = json.loads(TESTSET_PATH.read_text(encoding="utf-8"))

    sandbox_dir = _make_sandbox(args.sandbox_dir)
    print(f"沙箱目录: {sandbox_dir}")

    try:
        # 兜底：目标不在沙箱索引中时，用测试集 corpus 重建沙箱索引
        missing_targets = [
            c["expected_notice_ids"][0] for c in cases if not _sandbox_has_target(c["expected_notice_ids"][0])
        ]
        if missing_targets:
            print(f"[沙箱] 目标 {missing_targets} 未在沙箱索引中，重建索引兜底")
            _rebuild_sandbox_from_corpus(testset)

        print("=" * 60)
        print("RAG 污染复现：两阶段判定")
        print("=" * 60)
        results = []
        for case in cases:
            results.append(run_case(case, args.top_k))

        # 沙箱内一致性校验
        print("\n=== 沙箱向量一致性校验 ===")
        consistency = check_consistency(fix_ghosts=False)
        print(
            f"SQLite={consistency['sqlite_notices']} Chroma通知={consistency['chroma_notices']} "
            f"残留={len(consistency['ghosts'])} 缺失={len(consistency['missing'])}"
        )
        if consistency.get("consistent"):
            print("✅ 向量一致（无残留）")
        else:
            print(f"❌ 残留: {consistency['ghosts']}")

        # 汇总
        print("\n=== 汇总 ===")
        all_pass = True
        for r in results:
            ok = r["phase1_hit"] and r.get("phase2_pass") is True
            all_pass = all_pass and ok
            status = "✅" if ok else "❌"
            print(f"{status} {r['id']} target=#{r['target']} 删前命中={r['phase1_hit']} 删后无幽灵={r.get('phase2_pass')}")
        ok = all_pass and bool(consistency.get("consistent"))
        print("\n" + ("✅ 复现通过：删除通知后检索不到，无幽灵结果" if ok else "❌ 复现失败：存在幽灵结果或一致性不一致"))
        return 0 if ok else 1
    finally:
        if not args.keep:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
            print(f"\n沙箱已清理: {sandbox_dir}")


if __name__ == "__main__":
    sys.exit(main())
