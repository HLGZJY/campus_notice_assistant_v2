"""通知相关服务：封装 M1 爬取与 M2 提取。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from config.schema import SourceConfig
from config.store import ConfigStore
from core.extractor import NoticeExtractor
from core.models import ACTION_NOTICE_TYPES
from crawler import ListPageConfig, WebCrawler
from services.subscription_service import match_notice
from storage.db import (
    count_notices_by_status,
    get_connection,
    get_notice_by_id,
    get_notices_by_status,
    mark_failed,
    update_extraction,
)
logger = logging.getLogger(__name__)


def _get_vector_index():
    """延迟导入 VectorIndex，避免在只需要查询通知时触发向量库依赖。"""
    from storage.vectorstore import VectorIndex

    return VectorIndex()


def get_school_config():
    """获取当前活跃学校的数据源配置（来自 ConfigStore）。"""
    return ConfigStore.get_instance().get_school()


def crawl_source(source_cfg: SourceConfig) -> dict:
    """抓取单个数据源，返回结构化结果字典。"""
    cfg = ListPageConfig(
        list_url=source_cfg.list_url,
        source_name=source_cfg.name,
        url_pattern=source_cfg.url_pattern,
        max_pages=source_cfg.max_pages,
    )
    crawler = WebCrawler(config=cfg)
    result = crawler.crawl()
    return {
        "source": result.source,
        "discovered": result.total_discovered,
        "new": result.total_new,
        "skipped": result.total_skipped,
        "changed": result.total_changed,
        "failed": result.total_failed,
        "errors": result.errors,
    }


def crawl_all_sources() -> dict:
    """按配置文件抓取所有数据源。返回 {source_name: result_dict}。"""
    school_config = get_school_config()
    results = {}
    for source in school_config.sources:
        try:
            results[source.name] = crawl_source(source)
        except Exception as e:
            logger.exception("抓取失败: %s", source.name)
            results[source.name] = {
                "source": source.name,
                "discovered": 0,
                "new": 0,
                "skipped": 0,
                "changed": 0,
                "failed": 0,
                "errors": [f"{type(e).__name__}: {e}"],
            }
    return results


def get_status_counts() -> dict[str, int]:
    """按状态统计通知数量。"""
    conn = get_connection()
    try:
        return count_notices_by_status(conn)
    finally:
        conn.close()


def get_notices(
    status: Optional[str] = None,
    source: Optional[str] = None,
    notice_type: Optional[str] = None,
    keyword: Optional[str] = None,
    is_action: Optional[bool] = None,
    limit: int = 200,
) -> list[dict]:
    """多条件查询通知列表。

    Args:
        status: raw / extracted / partial / failed
        source: 数据源名称
        notice_type: 通知类型
        keyword: 标题关键词（模糊匹配）
        is_action: 是否只返回行动型通知
        limit: 最大返回条数
    """
    conn = get_connection()
    try:
        where: list[str] = []
        params: list = []
        if status:
            where.append("status = ?")
            params.append(status)
        if source:
            where.append("source = ?")
            params.append(source)
        if notice_type:
            where.append("notice_type = ?")
            params.append(notice_type)
        if keyword:
            where.append("title LIKE ?")
            params.append(f"%{keyword}%")
        if is_action is True:
            placeholders = ", ".join("?" * len(ACTION_NOTICE_TYPES))
            where.append(f"notice_type IN ({placeholders})")
            params.extend(ACTION_NOTICE_TYPES)
        if is_action is False:
            placeholders = ", ".join("?" * len(ACTION_NOTICE_TYPES))
            where.append(f"(notice_type IS NULL OR notice_type NOT IN ({placeholders}))")
            params.extend(ACTION_NOTICE_TYPES)

        sql = "SELECT * FROM notices"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY crawled_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_notice_detail(notice_id: int) -> Optional[dict]:
    """按 ID 查询通知详情。"""
    conn = get_connection()
    try:
        row = get_notice_by_id(conn, notice_id)
        if row is None:
            return None
        notice = dict(row)
        # 把 JSON 字符串的关键日期解析成列表，便于 UI 展示
        if notice.get("key_dates_json"):
            try:
                notice["key_dates"] = json.loads(notice["key_dates_json"])
            except Exception:
                notice["key_dates"] = []
        else:
            notice["key_dates"] = []
        return notice
    finally:
        conn.close()


def get_sources() -> list[str]:
    """返回数据库中所有来源名称。"""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT DISTINCT source FROM notices ORDER BY source").fetchall()
        return [r["source"] for r in rows if r["source"]]
    finally:
        conn.close()


def get_notice_types() -> list[str]:
    """返回数据库中所有通知类型。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT notice_type FROM notices WHERE notice_type IS NOT NULL ORDER BY notice_type"
        ).fetchall()
        return [r["notice_type"] for r in rows]
    finally:
        conn.close()


async def _extract_one_async(notice: dict, extractor: NoticeExtractor | None = None) -> dict:
    """异步提取单条通知。extractor 可注入（测试用）。"""
    extractor = extractor or NoticeExtractor()
    outcome = await extractor.extract_one(
        title=notice["title"],
        content=notice["raw_content"],
        published_at=notice.get("published_at"),
        crawled_at=notice.get("crawled_at"),
        notice_id=notice.get("id"),
    )
    return {
        "status": outcome.status,
        "extraction": outcome.extraction.model_dump() if outcome.extraction else None,
        "error": outcome.error,
    }


def extract_notice(notice_id: int, auto_index: bool = True) -> dict:
    """对单条通知执行结构化提取，写库并自动增量索引。

    Args:
        notice_id: 通知 ID
        auto_index: 提取成功后是否自动加入向量索引
    """
    conn = get_connection()
    try:
        notice = get_notice_by_id(conn, notice_id)
        if not notice:
            return {"success": False, "error": "通知不存在"}
        if not notice.get("raw_content"):
            return {"success": False, "error": "通知无正文内容"}

        result = asyncio.run(_extract_one_async(dict(notice)))
        status = result["status"]
        extraction = result.get("extraction")

        if status == "failed" or extraction is None:
            mark_failed(conn, notice_id, result.get("error") or "提取失败")
            return {"success": False, "status": "failed", "error": result.get("error")}

        update_extraction(conn, notice_id, extraction, status)
        updated_notice = get_notice_by_id(conn, notice_id)

        try:
            match_notice(notice_id)
        except Exception as e:
            logger.warning("订阅匹配失败 notice_id=%s: %s", notice_id, e)

        index_info = {}
        if auto_index:
            try:
                index = _get_vector_index()
                index_info = index.add_notice(dict(updated_notice))
            except Exception as e:
                logger.warning("自动索引失败: %s", e)
                index_info = {"error": str(e)}

        return {
            "success": True,
            "status": status,
            "extraction": extraction,
            "index": index_info,
        }
    finally:
        conn.close()


def extract_batch(
    limit: int = 50, auto_index: bool = True, extractor: NoticeExtractor | None = None
) -> dict:
    """批量提取所有 status=raw 的通知（断点续跑的提取游标）。

    Args:
        limit: 最大处理条数
        auto_index: 每条提取成功后是否自动加入向量索引
        extractor: 可注入（测试用），默认真实 NoticeExtractor
    """
    conn = get_connection()
    try:
        notices = get_notices_by_status(conn, "raw", limit=limit)
    finally:
        conn.close()

    if not notices:
        return {"processed": 0, "summary": {}}

    extractor = extractor or NoticeExtractor()

    async def _run() -> dict:
        summary = {"extracted": 0, "partial": 0, "failed": 0, "details": []}
        for notice in notices:
            try:
                outcome = await extractor.extract_one(
                    title=notice["title"],
                    content=notice["raw_content"],
                    published_at=notice.get("published_at"),
                    crawled_at=notice.get("crawled_at"),
                    notice_id=notice["id"],
                )
                status = outcome.status
                extraction = outcome.extraction.model_dump() if outcome.extraction else None

                # 写库
                conn2 = get_connection()
                try:
                    if status == "failed" or extraction is None:
                        mark_failed(conn2, notice["id"], outcome.error or "提取失败")
                        summary["failed"] += 1
                    else:
                        update_extraction(conn2, notice["id"], extraction, status)
                        summary[status] += 1
                        try:
                            match_notice(notice["id"])
                        except Exception as e:
                            logger.warning("订阅匹配失败 notice_id=%s: %s", notice["id"], e)
                        if auto_index:
                            try:
                                updated = get_notice_by_id(conn2, notice["id"])
                                _get_vector_index().add_notice(dict(updated))
                            except Exception as e:
                                logger.warning("自动索引失败 notice_id=%s: %s", notice["id"], e)
                finally:
                    conn2.close()

                summary["details"].append(
                    {
                        "id": notice["id"],
                        "title": notice["title"],
                        "status": status,
                        "error": outcome.error,
                    }
                )
            except Exception as e:
                logger.exception("提取失败 notice_id=%s", notice["id"])
                summary["failed"] += 1
                summary["details"].append(
                    {
                        "id": notice["id"],
                        "title": notice["title"],
                        "status": "failed",
                        "error": f"{type(e).__name__}: {e}",
                    }
                )
        return summary

    summary = asyncio.run(_run())
    return {"processed": len(notices), "summary": summary}
