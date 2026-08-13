"""Chroma 向量索引（M4）。

基于 langchain 的 Chroma + HuggingFaceEmbeddings/OpenAIEmbeddings fallback，
提供通知的切分、索引、增量更新和语义检索能力。

检索入口 search() 支持三档过期策略（模块 2.3）：
  - strategy="none"   ：不过滤（基线）
  - strategy="decay"   ：过期降权（按过期天数做时间衰减重排，不剔除）
  - strategy="filter"  ：过期排除（检索前用 Chroma where 过滤过期通知）
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from utils.embedding import get_embeddings

logger = logging.getLogger(__name__)

DEFAULT_PERSIST_DIR = Path(__file__).parent.parent / "data" / "chroma"
COLLECTION_NAME = "notices"

# 模块 2.3 过期策略
DEFAULT_EXPIRE_DAYS = 90  # 无 deadline 通知的默认有效期兜底（天）
DEFAULT_DECAY_STRENGTH = 0.05  # 降权系数：decay = 1 / (1 + strength * overdue_days)
DEFAULT_CANDIDATE_FACTOR = 3  # 降权重排候选池倍率（k * factor）

# 中文-aware 切分器：优先在段落、句子、中文标点处断开，避免把单个字切碎
TEXT_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    separators=["\n\n", "\n", "。", "；", "！", "？", "，", " ", ""],
)


def _doc_id(notice_id: int, chunk_idx: int) -> str:
    return f"notice_{notice_id}_chunk_{chunk_idx}"


def _split_notice(notice: dict) -> list[Document]:
    """把一条通知切分为多个 chunk，首块附加标题/类型/摘要等元信息。"""
    notice_id = notice["id"]
    title = notice.get("title") or ""
    notice_type = notice.get("notice_type") or ""
    summary = notice.get("summary") or ""
    deadline = notice.get("deadline") or ""
    raw_content = notice.get("raw_content") or ""

    header_parts = [f"标题：{title}"]
    if notice_type:
        header_parts.append(f"类型：{notice_type}")
    if summary:
        header_parts.append(f"摘要：{summary}")
    if deadline:
        header_parts.append(f"截止时间：{deadline}")
    header = "\n".join(header_parts)

    # 把 header 拼在正文前面一起切分：header 会自然落在首块，帮助检索
    text_with_header = f"{header}\n\n{raw_content}"
    docs = TEXT_SPLITTER.create_documents([text_with_header])

    result = []
    for idx, doc in enumerate(docs):
        doc.metadata.update({
            "notice_id": notice_id,
            "title": title,
            "notice_type": notice_type,
            "source": notice.get("source") or "",
            "url": notice.get("url") or "",
            "deadline": deadline,
            "published_at": notice.get("published_at") or "",
            "chunk_idx": idx,
            "status": notice.get("status") or "",
        })
        result.append(doc)
    return result


# ---------- 过期判定（模块 2.3） ----------


def _parse_date(value: str) -> Optional[date]:
    """从 ISO 字符串提取日期部分；失败返回 None。"""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _default_expire_days() -> int:
    """读取配置中的 expire_days（无 deadline 通知的默认有效期），失败兜底 DEFAULT_EXPIRE_DAYS。"""
    try:
        from config.store import ConfigStore

        return ConfigStore.get_instance().get_crawl().expire_days
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 expire_days 失败，使用默认 %d: %s", DEFAULT_EXPIRE_DAYS, e)
        return DEFAULT_EXPIRE_DAYS


def _notice_expiry_date(meta: dict, expire_days: int) -> Optional[date]:
    """计算通知的"过期基准日"。

    规则：以 deadline 为准；无 deadline 的按 published_at + expire_days 兜底；
    两者都缺失视为无过期信息，返回 None（不判过期，避免误杀）。
    """
    deadline = _parse_date(meta.get("deadline") or "")
    if deadline is not None:
        return deadline
    published = _parse_date(meta.get("published_at") or "")
    if published is not None:
        return published + timedelta(days=expire_days)
    return None


def _days_expired(meta: dict, reference_date: date, expire_days: int) -> int:
    """通知相对参考日期已过期天数；未过期或无过期信息返回 0。"""
    expiry = _notice_expiry_date(meta, expire_days)
    if expiry is None:
        return 0
    overdue = (reference_date - expiry).days
    return overdue if overdue > 0 else 0


def _is_expired(meta: dict, reference_date: date, expire_days: int) -> bool:
    return _days_expired(meta, reference_date, expire_days) > 0


class VectorIndex:
    """通知向量索引。"""

    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        collection_name: str = COLLECTION_NAME,
    ):
        self.persist_dir = persist_dir or DEFAULT_PERSIST_DIR
        self.collection_name = collection_name
        self._embedding = get_embeddings()
        self._store: Optional[Chroma] = None

    def _get_store(self, force_rebuild: bool = False) -> Chroma:
        if self._store is not None and not force_rebuild:
            return self._store

        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._store = Chroma(
            persist_directory=str(self.persist_dir),
            embedding_function=self._embedding,
            collection_name=self.collection_name,
            collection_metadata={"hnsw:space": "cosine"},
        )
        return self._store

    def count(self) -> int:
        """返回当前 collection 中文档数。"""
        try:
            return self._get_store()._collection.count()
        except Exception as e:
            logger.warning(f"统计向量库失败: {e}")
            return 0

    def stats(self) -> dict:
        """返回向量库统计信息。"""
        count = self.count()
        try:
            collection = self._get_store()._collection
            # Chroma collection 的 metadata 不一定包含总通知数，这里仅返回文档数
            return {"chunks": count, "persist_dir": str(self.persist_dir)}
        except Exception as e:
            return {"chunks": count, "error": str(e)}

    def rebuild(self, notices: list[dict], dry_run: bool = False) -> dict:
        """全量重建索引：删除旧 collection，重新切分并索引所有通知。

        返回 {"notices": 通知数, "chunks": 分块数}
        """
        if dry_run:
            chunks = sum(len(_split_notice(n)) for n in notices)
            return {"notices": len(notices), "chunks": chunks, "dry_run": True}

        # 删除旧 collection（如果存在）
        self.delete_collection()
        store = self._get_store(force_rebuild=True)

        all_docs: list[Document] = []
        all_ids: list[str] = []
        for notice in notices:
            docs = _split_notice(notice)
            for idx, doc in enumerate(docs):
                all_docs.append(doc)
                all_ids.append(_doc_id(notice["id"], idx))

        if all_docs:
            store.add_documents(all_docs, ids=all_ids)
            logger.info(f"已索引 {len(notices)} 条通知，共 {len(all_docs)} 个 chunk")
        else:
            logger.info("没有可索引的通知")

        return {"notices": len(notices), "chunks": len(all_docs)}

    def add_notice(self, notice: dict) -> dict:
        """单条通知增量索引（先删除该通知旧 chunk，再添加新 chunk）。"""
        store = self._get_store()
        notice_id = notice["id"]
        self.remove_notice(notice_id)

        docs = _split_notice(notice)
        ids = [_doc_id(notice_id, idx) for idx in range(len(docs))]
        if docs:
            store.add_documents(docs, ids=ids)
        return {"notice_id": notice_id, "chunks": len(docs)}

    def remove_notice(self, notice_id: int) -> int:
        """删除某通知的所有 chunk，返回实际删除数量。

        先按 notice_id 元数据过滤拿到真实 chunk id，再按 id 删除并返回数量，
        避免"where delete 语义不确定导致删了但没删掉"（模块 2.5 幽灵向量根因之一）。
        """
        store = self._get_store()
        try:
            collection = store._collection
            ids = collection.get(where={"notice_id": notice_id}, include=["metadatas"])["ids"]
            if not ids:
                return 0
            collection.delete(ids=ids)
            logger.debug(f"已删除 notice_id={notice_id} 的 {len(ids)} 个 chunk")
            return len(ids)
        except Exception as e:
            logger.warning(f"删除 notice_id={notice_id} 失败: {e}")
            return 0

    def delete_collection(self) -> None:
        """删除整个 collection（用于重建）。"""
        try:
            client = self._get_store()._client
            client.delete_collection(name=self.collection_name)
            self._store = None
            logger.info(f"已删除旧 collection: {self.collection_name}")
        except Exception as e:
            logger.warning(f"删除 collection 失败（可能不存在）: {e}")

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
        """语义检索 Top-K 文档块。

        Args:
            query: 检索问题
            k: 返回的 chunk 数
            strategy: 过期策略
                - "none":  不过滤（基线，等价于原 similarity_search）
                - "decay":  过期降权——检索更大候选池，按过期天数时间衰减重排，取 top-k（不剔除）
                - "filter": 过期排除——检索前用 Chroma where($nin) 过滤过期通知
            reference_date: 过期判定的参考日期（默认今天；评测时传测试集 reference_date）
            expire_days: 无 deadline 通知的默认有效期（默认读配置，兜底 90）
            decay_strength: 降权系数，decay = 1 / (1 + strength * overdue_days)
            candidate_factor: "decay" 档候选池倍率（k * factor）
            min_score: 相似度下限（cosine 相似度，0~1）；低于阈值的 chunk 不返回，
                用于过滤低相关噪声。None 表示不过滤（基线行为）。
        """
        ref = reference_date or date.today()
        days = expire_days if expire_days is not None else _default_expire_days()
        store = self._get_store()

        if strategy == "filter":
            return self._search_filter_expired(query, k, ref, days, min_score)
        if strategy == "decay":
            return self._search_decay(
                query, k, ref, days, decay_strength, candidate_factor, min_score
            )
        return self._search_vector(query, k, min_score)

    def _search_vector(
        self, query: str, k: int, min_score: Optional[float] = None
    ) -> list[Document]:
        """纯向量检索；min_score 非 None 时用 with_score 过滤低相似度。"""
        if min_score is None:
            return self._get_store().similarity_search(query, k=k)
        scored = self._get_store().similarity_search_with_score(query, k=k)
        return [doc for doc, dist in scored if 1.0 - dist >= min_score]

    def _search_filter_expired(
        self,
        query: str,
        k: int,
        reference_date: date,
        expire_days: int,
        min_score: Optional[float] = None,
    ) -> list[Document]:
        """策略 C：检索前过滤过期通知（Chroma where $nin，真正的检索前过滤）。"""
        expired_ids = self.get_expired_notice_ids(reference_date, expire_days)
        if not expired_ids:
            return self._search_vector(query, k, min_score)
        if len(expired_ids) >= self.count():
            logger.warning("策略 filter：全部通知过期，检索结果为空")
            return []

        # langchain_chroma 的 similarity_search(where=...) 在本环境存在参数冲突 bug，
        # 这里直接走底层 collection.query 做检索前过滤
        collection = self._get_store()._collection
        query_embeddings = self._embedding.embed_query(query)
        try:
            res = collection.query(
                query_embeddings=[query_embeddings],
                n_results=k,
                where={"notice_id": {"$nin": sorted(expired_ids)}},
                include=["metadatas", "documents", "distances"],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("策略 filter 检索失败，回退普通检索: %s", e)
            return self._search_vector(query, k, min_score)

        docs = []
        metas = res.get("metadatas") or [[]]
        texts = res.get("documents") or [[]]
        dists = res.get("distances") or [[]]
        for meta, text, dist in zip(metas[0], texts[0], dists[0]):
            if min_score is not None and 1.0 - dist < min_score:
                continue
            docs.append(Document(page_content=text, metadata=meta or {}))
        return docs

    def _search_decay(
        self,
        query: str,
        k: int,
        reference_date: date,
        expire_days: int,
        decay_strength: float,
        candidate_factor: int,
        min_score: Optional[float] = None,
    ) -> list[Document]:
        """策略 B：过期降权重排——不剔除过期通知，只是按过期天数时间衰减后重排取 top-k。"""
        candidates = max(k * candidate_factor, k)
        scored = self._get_store().similarity_search_with_score(query, k=candidates)

        ranked = []
        for doc, dist in scored:
            sim = 1.0 - dist
            if min_score is not None and sim < min_score:
                continue
            overdue = _days_expired(doc.metadata, reference_date, expire_days)
            decay = 1.0 / (1.0 + decay_strength * overdue) if overdue > 0 else 1.0
            ranked.append((sim * decay, doc))

        ranked.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in ranked[:k]]

    def get_expired_notice_ids(
        self,
        reference_date: Optional[date] = None,
        expire_days: Optional[int] = None,
    ) -> set[int]:
        """返回当前索引中"已过期"的 notice_id 集合（用于策略 filter）。

        从 Chroma 全量 metadata 计算，与检索 universe 保持一致。
        """
        ref = reference_date or date.today()
        days = expire_days if expire_days is not None else _default_expire_days()
        try:
            collection = self._get_store()._collection
            all_meta = collection.get(include=["metadatas"])["metadatas"]
        except Exception as e:  # noqa: BLE001
            logger.warning("拉取索引 metadata 失败: %s", e)
            return set()
        return {
            m["notice_id"]
            for m in all_meta
            if m.get("notice_id") is not None and _is_expired(m, ref, days)
        }


# ---------- 便捷函数 ----------


def get_vector_index(persist_dir: Optional[Path] = None) -> VectorIndex:
    """获取默认 VectorIndex 实例。"""
    return VectorIndex(persist_dir=persist_dir)


def check_consistency(
    persist_dir: Optional[Path] = None,
    fix_ghosts: bool = False,
) -> dict:
    """对比 Chroma 与 SQLite 的通知 ID 集合，检测/清理残留（幽灵）向量（模块 2.5）。

    幽灵向量定义：notice_id 在 Chroma 中存在，但已不存在于 SQLite（通知被删除）。
    判定基准取 SQLite 全量通知 ID 而非"可索引"子集——通知处于 raw/failed 等状态时
    其向量仍属有效内容，不应被当作残留误删。

    fix_ghosts=True 时自动删除幽灵向量并重新读取确认；missing（已提取但未索引）
    只报告不处理，由提取链路的增量索引补齐。

    返回:
        consistent:    当前是否无残留（fix 后按修复结果重算）
        sqlite_notices / chroma_notices: 两侧通知 ID 数
        ghosts_found:  本次发现的幽灵 ID 列表（含已清理的）
        ghosts:        当前仍残留的幽灵 ID 列表（fix 后应为空）
        ghosts_removed: 本次实际清理的 chunk 数
        missing:       已提取但缺失向量的通知 ID 列表
    """
    from storage.db import get_all_notice_ids, get_connection, get_indexable_notice_ids

    conn = get_connection()
    try:
        sqlite_all_ids = set(get_all_notice_ids(conn))
        sqlite_indexable_ids = set(get_indexable_notice_ids(conn))
    finally:
        conn.close()

    def _load_chroma_ids(index: "VectorIndex") -> set[int]:
        data = index._get_store()._collection.get(include=["metadatas"])
        ids: set[int] = set()
        for meta in data.get("metadatas") or []:
            nid = (meta or {}).get("notice_id")
            if nid is not None:
                ids.add(int(nid))
        return ids

    index = get_vector_index(persist_dir=persist_dir)
    try:
        chroma_ids = _load_chroma_ids(index)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取向量库失败: %s", e)
        return {"consistent": False, "error": str(e)}

    ghosts_found = sorted(chroma_ids - sqlite_all_ids)
    missing = sorted(sqlite_indexable_ids - chroma_ids)

    removed = 0
    if ghosts_found and fix_ghosts:
        for nid in ghosts_found:
            try:
                removed += index.remove_notice(nid)
            except Exception as e:  # noqa: BLE001
                logger.warning("清理幽灵向量失败 notice_id=%s: %s", nid, e)
        logger.warning(
            "向量一致性: 清理 %d 个幽灵向量（对应通知已不存在于 SQLite）: %s",
            len(ghosts_found),
            ghosts_found,
        )
        # 修复后重新读取，确认无残留
        try:
            chroma_ids = _load_chroma_ids(index)
        except Exception as e:  # noqa: BLE001
            logger.warning("修复后复查向量库失败: %s", e)
            return {"consistent": False, "error": str(e)}

    ghosts = sorted(chroma_ids - sqlite_all_ids)

    return {
        "consistent": not ghosts,
        "sqlite_notices": len(sqlite_all_ids),
        "chroma_notices": len(chroma_ids),
        "ghosts_found": ghosts_found,
        "ghosts": ghosts,
        "ghosts_removed": removed,
        "missing": missing,
    }
