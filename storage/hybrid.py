"""混合检索：BM25 + RRF（W2 模块 2.4）。

在向量检索（Chroma）旁新增 BM25 稀疏检索，用 RRF（k=60）融合两路排名。

设计要点：
  - BM25 语料与向量库同源：从 Chroma collection.get() 直接拉取全部 chunk
    （含 header 前缀），保证"同一批 chunk 建索引"，杜绝重切分漂移。
  - 中文分词用 jieba：中文段保留原词，英文/数字提取并小写归一化。
  - RRF 是"排名倒数求和"：score = Σ 1/(k + rank)，不做分数加权。
  - 接口与 VectorIndex.search 对齐，可无缝替换到 evaluate_* 与 core/qa.py。

用法：
    from storage.hybrid import HybridIndex
    hybrid = HybridIndex()                      # 包装默认 VectorIndex
    docs = hybrid.search("ICPC 校赛报名", k=5)
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

import jieba
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

from storage.vectorstore import (
    DEFAULT_CANDIDATE_FACTOR,
    DEFAULT_DECAY_STRENGTH,
    VectorIndex,
    _days_expired,
    _default_expire_days,
)

logger = logging.getLogger(__name__)

DEFAULT_RRF_K = 60  # RRF 常数（模块 2.4 规格）
DEFAULT_CANDIDATE_K = 20  # 每路候选池大小：k=5 时各取 top20 再融合，给重排留空间

_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
_ALNUM = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> list[str]:
    """jieba 中文分词 + 英文/数字小写归一化，返回词元列表。

    中文段（如"数学建模"）保留原词；混合段（如 "ICPC大学生"、"MCM/ICM"）
    提取其中的英文/数字词并小写，保证查询与语料两侧词元一致。
    """
    tokens: list[str] = []
    for seg in jieba.cut((text or "").lower()):
        seg = seg.strip()
        if not seg:
            continue
        if _CJK.fullmatch(seg):
            tokens.append(seg)
        else:
            tokens.extend(_ALNUM.findall(seg))
    return tokens


def _chunk_key(meta: dict) -> Optional[tuple[int, int]]:
    """从 metadata 取 chunk 唯一键 (notice_id, chunk_idx)。"""
    nid = meta.get("notice_id")
    cidx = meta.get("chunk_idx")
    if nid is None or cidx is None:
        return None
    return (nid, cidx)


def rrf_merge(
    vector_docs: list[Document],
    bm25_docs: list[Document],
    k: int = DEFAULT_RRF_K,
) -> list[tuple[tuple[int, int], float, Document]]:
    """RRF 融合两路 Document 排名，返回按融合分降序的 (key, score, doc)。

    RRF 分数 = Σ 1/(k + rank)，rank 为 1-based；同一 chunk 双路命中则分数累积。
    这是纯排名融合，与任何检索分数无关。
    """
    score_map: dict[tuple[int, int], float] = {}
    doc_map: dict[tuple[int, int], Document] = {}

    def _add(docs: list[Document]) -> None:
        for rank, doc in enumerate(docs, start=1):
            key = _chunk_key(doc.metadata)
            if key is None:
                continue
            score_map[key] = score_map.get(key, 0.0) + 1.0 / (k + rank)
            doc_map[key] = doc

    _add(vector_docs)
    _add(bm25_docs)

    ranked = sorted(score_map.items(), key=lambda kv: kv[1], reverse=True)
    return [(key, score, doc_map[key]) for key, score in ranked]


class HybridIndex:
    """向量（Chroma）+ BM25 混合检索，RRF 融合。

    包装一个 VectorIndex，复用其 none/decay/filter 过期策略语义；
    search() 返回的 Document 与 VectorIndex.search 同构（metadata 含 notice_id）。
    """

    def __init__(
        self,
        index: Optional[VectorIndex] = None,
        rrf_k: int = DEFAULT_RRF_K,
        candidate_k: int = DEFAULT_CANDIDATE_K,
    ):
        self._index = index or VectorIndex()
        self.rrf_k = rrf_k
        self.candidate_k = candidate_k
        self._corpus_docs: list[Document] = []
        self._bm25: Optional[BM25Okapi] = None

    # ---------- 委托给内层 VectorIndex ----------

    def _get_store(self):
        """委托内层 VectorIndex 的底层 Chroma store（evaluate 脚本会用）。"""
        return self._index._get_store()

    def stats(self) -> dict:
        return self._index.stats()

    def count(self) -> int:
        return self._index.count()

    def get_expired_notice_ids(
        self,
        reference_date: Optional[date] = None,
        expire_days: Optional[int] = None,
    ) -> set[int]:
        return self._index.get_expired_notice_ids(reference_date, expire_days)

    # ---------- BM25 索引 ----------

    def _load_corpus(self) -> None:
        """从 Chroma 拉取全部 chunk 建 BM25 索引（与向量库同批语料）。"""
        collection = self._index._get_store()._collection
        data = collection.get(include=["documents", "metadatas"])
        texts = data.get("documents") or []
        metas = data.get("metadatas") or []
        docs = [
            Document(page_content=text, metadata=meta or {})
            for text, meta in zip(texts, metas)
        ]
        self._corpus_docs = docs
        if docs:
            self._bm25 = BM25Okapi([tokenize(d.page_content) for d in docs])
            logger.info("BM25 索引已构建: %d chunks", len(docs))
        else:
            self._bm25 = None
            logger.info("空语料，跳过 BM25 索引构建")

    def _get_bm25(self) -> Optional[BM25Okapi]:
        if self._bm25 is None and not self._corpus_docs:
            self._load_corpus()
        return self._bm25

    # ---------- 检索 ----------

    def search(
        self,
        query: str,
        k: int = 6,
        strategy: str = "none",
        reference_date: Optional[date] = None,
        expire_days: Optional[int] = None,
        decay_strength: float = DEFAULT_DECAY_STRENGTH,
        candidate_factor: int = DEFAULT_CANDIDATE_FACTOR,
        min_score: Optional[float] = None,
    ) -> list[Document]:
        """混合检索 Top-K chunk（RRF 融合向量路与 BM25 路）。"""
        candidate_k = max(self.candidate_k, k)

        # 向量路：复用 VectorIndex.search 的 none/decay/filter 语义
        vector_docs = self._index.search(
            query,
            k=candidate_k,
            strategy=strategy,
            reference_date=reference_date,
            expire_days=expire_days,
            decay_strength=decay_strength,
            candidate_factor=candidate_factor,
            min_score=min_score,
        )

        # BM25 路（过期策略与向量路对齐）
        bm25_docs = self._bm25_search(
            query, candidate_k, strategy, reference_date, expire_days, decay_strength
        )

        merged = rrf_merge(vector_docs, bm25_docs, k=self.rrf_k)
        return [doc for _, _, doc in merged[:k]]

    def _bm25_search(
        self,
        query: str,
        candidate_k: int,
        strategy: str,
        reference_date: Optional[date],
        expire_days: Optional[int],
        decay_strength: float,
    ) -> list[Document]:
        """BM25 稀疏检索 top-candidate_k。

        strategy="filter" 先剔除过期通知的 chunk；
        strategy="decay"   对过期 chunk 的分数乘时间衰减（复用 _days_expired）。
        """
        bm25 = self._get_bm25()
        if bm25 is None:
            return []
        ref = reference_date or date.today()
        days = expire_days if expire_days is not None else _default_expire_days()
        expired_ids = (
            self._index.get_expired_notice_ids(ref, days)
            if strategy == "filter"
            else None
        )

        q_tokens = tokenize(query)
        scores = bm25.get_scores(q_tokens)
        scored: list[tuple[float, Document]] = []
        for doc, score in zip(self._corpus_docs, scores):
            meta = doc.metadata
            if _chunk_key(meta) is None:
                continue
            if expired_ids and meta.get("notice_id") in expired_ids:
                continue
            if strategy == "decay":
                overdue = _days_expired(meta, ref, days)
                if overdue > 0:
                    score *= 1.0 / (1.0 + decay_strength * overdue)
            scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:candidate_k]]
