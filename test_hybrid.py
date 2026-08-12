"""W2 模块 2.4 混合检索离线验收。

不依赖真实 Chroma，用假索引（FakeIndex）验证：
  1. jieba 分词：中文保留原词、英文/数字小写归一化
  2. RRF 是"排名倒数求和"（k=60），双路命中的 chunk 分数累积、排在单路之上
  3. BM25 对中文关键词能召回对应 chunk
  4. HybridIndex 与 VectorIndex 接口对齐：search 返回 Document 列表、metadata 带 notice_id
  5. 过期策略在 BM25 路上的对齐（filter 剔除 / decay 降权）

用法：
    python test_hybrid.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from langchain_core.documents import Document

from storage.hybrid import HybridIndex, rrf_merge, tokenize

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def mk_doc(nid: int, cidx: int, text: str) -> Document:
    return Document(
        page_content=text,
        metadata={"notice_id": nid, "chunk_idx": cidx, "title": f"notice{nid}"},
    )


def test_tokenize() -> None:
    print("\n[1] 分词")
    toks = tokenize("2026年ICPC大学生程序设计竞赛校赛在哪里报名？")
    check("英文缩写小写保留", "icpc" in toks)
    check("年份数字保留", "2026" in toks)
    check("中文词保留", "大学生" in toks and "程序设计" in toks and "竞赛" in toks)

    toks2 = tokenize("教高〔2019〕6号文件")
    check("文号数字保留", "2019" in toks2 and "6" in toks2)
    check("文号中文保留", "教高" in toks2 and "号" in toks2)

    toks3 = tokenize("NECCS是什么比赛，报名对象包括哪些？")
    check("英文缩写全大写下小写", "neccs" in toks3)

    check("空文本为空列表", tokenize("") == [])
    check("纯标点无词元", tokenize("？？！") == [])


def test_rrf() -> None:
    print("\n[2] RRF 排名倒数求和（k=60）")
    d_a = mk_doc(1, 0, "A")
    d_b = mk_doc(2, 0, "B")
    d_c = mk_doc(3, 0, "C")

    # 向量路：[A, B, C]，BM25 路：[B, C, A]
    # 双路命中累积：B = 1/61 + 1/62，A = 1/61 + 1/63，C = 1/62 + 1/63
    merged = rrf_merge([d_a, d_b, d_c], [d_b, d_c, d_a])
    keys = [(nid, cidx) for (nid, cidx), _, _ in merged]
    scores = {key: s for key, s, _ in merged}

    check("双路命中的 chunk 排最前", keys[0] == (2, 0), f"got {keys}")
    expected_b = 1 / 61 + 1 / 62
    expected_a = 1 / 61 + 1 / 63
    expected_c = 1 / 62 + 1 / 63
    check("B 分数=1/61+1/62", abs(scores[(2, 0)] - expected_b) < 1e-9)
    check("A 分数=1/61+1/63", abs(scores[(1, 0)] - expected_a) < 1e-9)
    check("C 分数=1/62+1/63", abs(scores[(3, 0)] - expected_c) < 1e-9)
    check("排序为 B>A>C", scores[(2, 0)] > scores[(1, 0)] > scores[(3, 0)])

    # 仅单路出现：分数 = 1/(k+rank)，不因分数值被加权
    merged2 = rrf_merge([mk_doc(1, 0, "x"), mk_doc(2, 0, "y")], [mk_doc(2, 0, "y")])
    scores2 = {key: s for key, s, _ in merged2}
    check("双路 y 分高于单路 x", scores2[(2, 0)] > scores2[(1, 0)])

    # k=60 参数生效
    merged3 = rrf_merge([d_a], [], k=60)
    check("k=60 单路 rank1 分=1/61", abs(merged3[0][1] - 1 / 61) < 1e-9)
    merged4 = rrf_merge([d_a], [], k=10)
    check("k=10 单路 rank1 分=1/11", abs(merged4[0][1] - 1 / 11) < 1e-9)


class _FakeCollection:
    def __init__(self, docs: list[Document]):
        self._docs = docs

    def get(self, include=None):
        return {
            "documents": [d.page_content for d in self._docs],
            "metadatas": [d.metadata for d in self._docs],
        }


class _FakeStore:
    def __init__(self, docs: list[Document]):
        self._collection = _FakeCollection(docs)


class FakeIndex:
    """假的 VectorIndex：corpus + 固定向量排名，不接真实 Chroma。"""

    def __init__(self, docs: list[Document], vector_rank: list[Document]):
        self._docs = docs
        self._vector_rank = vector_rank

    def _get_store(self) -> _FakeStore:
        return _FakeStore(self._docs)

    def search(self, query: str, k: int = 6, **kwargs) -> list[Document]:
        return self._vector_rank[:k]

    def stats(self) -> dict:
        return {"chunks": len(self._docs), "persist_dir": "fake"}

    def count(self) -> int:
        return len(self._docs)

    def get_expired_notice_ids(self, reference_date=None, expire_days=None) -> set[int]:
        return set()


def _corpus() -> list[Document]:
    return [
        mk_doc(1, 0, "关于启动2026年全国大学生数学建模竞赛暑期培训报名的通知"),
        mk_doc(2, 0, "ICPC大学生程序设计竞赛校赛报名方式"),
        mk_doc(3, 0, "2026年本科新生课程分级教学相关说明"),
        mk_doc(4, 0, "2026年美国大学生数学建模竞赛（MCM/ICM）报名通知"),
    ]


def test_bm25_recall() -> None:
    print("\n[3] BM25 中文关键词召回（假语料）")
    docs = _corpus()
    # 向量路置空，纯看 BM25 路的召回能力（隔离 RRF 双路累积干扰）
    hybrid = HybridIndex(FakeIndex(docs, vector_rank=[]), candidate_k=20)

    top = hybrid.search("ICPC 校赛 报名", k=3)
    ids = [d.metadata["notice_id"] for d in top]
    check("BM25 召回 ICPC 通知", 2 in ids, f"got {ids}")

    top2 = hybrid.search("数学建模 暑期培训 报名", k=3)
    ids2 = [d.metadata["notice_id"] for d in top2]
    check("BM25 召回数学建模培训通知", 1 in ids2, f"got {ids2}")


def test_interface_parity() -> None:
    print("\n[4] 接口对齐 + 过期策略透传")
    docs = _corpus()
    fake = FakeIndex(docs, vector_rank=docs)
    hybrid = HybridIndex(fake, candidate_k=20)

    res = hybrid.search("ICPC", k=5, strategy="none")
    check("search 返回 Document 列表", all(isinstance(d, Document) for d in res))
    check("返回数 <= k", len(res) <= 5, f"got {len(res)}")
    check("metadata 含 notice_id", all(d.metadata.get("notice_id") is not None for d in res))
    check("stats 委托", hybrid.stats()["chunks"] == 4)
    check("count 委托", hybrid.count() == 4)
    check("get_expired_notice_ids 委托", hybrid.get_expired_notice_ids() == set())

    # decay/filter 参数能透传不报错（假索引返回空过期集，行为与 none 一致）
    for strategy in ("decay", "filter"):
        r = hybrid.search("ICPC", k=3, strategy=strategy, reference_date=None, expire_days=90)
        check(f"strategy={strategy} 不报错", isinstance(r, list))

    # 空语料边界：BM25 无得分，仅返回向量路
    empty = FakeIndex([], vector_rank=[])
    he = HybridIndex(empty)
    check("空语料返回空列表", he.search("随便", k=3) == [])


def main() -> None:
    test_tokenize()
    test_rrf()
    test_bm25_recall()
    test_interface_parity()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    if FAIL:
        sys.exit(1)
    print("全部通过 ✓")


if __name__ == "__main__":
    main()
