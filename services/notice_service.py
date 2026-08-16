"""通知相关服务：封装 M1 爬取与 M2 提取。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from config.schema import ExtractConfig, SourceConfig
from config.store import ConfigStore
from core.extractor import NoticeExtractor
from core.models import ACTION_NOTICE_TYPES
from crawler import ListPageConfig, WebCrawler
from services.subscription_service import match_notice
from storage.db import (
    build_notice_where,
    clear_prefiltered,
    count_notices_by_status,
    get_connection,
    get_notice_by_id,
    get_notices_by_status,
    mark_failed,
    mark_prefiltered,
    update_extraction,
)
logger = logging.getLogger(__name__)

# 时间线索词：规则预检（require_time_hint）用，零 LLM 成本
_TIME_HINT_PATTERN = re.compile(
    r"\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2}|"
    r"截止|报名|开始|结束|时间|日期|期限|期间|月\d{1,2}日|周[一二三四五六日天]|"
    r"上午|下午|点|时|:00|：00",
    re.IGNORECASE,
)


def _get_vector_index():
    """延迟导入 VectorIndex，避免在只需要查询通知时触发向量库依赖。"""
    from storage.vectorstore import VectorIndex

    return VectorIndex()


def get_school_config():
    """获取当前活跃学校的数据源配置（来自 ConfigStore）。"""
    return ConfigStore.get_instance().get_school()


def _build_list_page_config(
    source_cfg: SourceConfig,
    mode: Optional[str] = None,
    max_pages: Optional[int] = None,
    deep_check: Optional[bool] = None,
) -> ListPageConfig:
    """把 SourceConfig + 全局抓取参数组装为爬虫配置（阶段 7：全量透传新字段）。"""
    crawl = ConfigStore.get_instance().get_crawl()
    return ListPageConfig(
        list_url=source_cfg.list_url,
        source_name=source_cfg.name,
        url_pattern=source_cfg.url_pattern,
        max_pages=max_pages or source_cfg.max_pages,
        crawl_mode=mode or source_cfg.crawl_mode,
        max_age_days=source_cfg.max_age_days,
        fetch_detail=source_cfg.fetch_detail,
        deep_check=deep_check if deep_check is not None else source_cfg.deep_check,
        stop_when_caught_up=crawl.stop_when_caught_up,
        request_timeout=crawl.request_timeout,
        retry_times=crawl.retry_times,
        concurrency=crawl.concurrency,
    )


def crawl_source(
    source_cfg: SourceConfig,
    mode: Optional[str] = None,
    max_pages: Optional[int] = None,
    deep_check: Optional[bool] = None,
) -> dict:
    """抓取单个数据源，返回结构化结果字典。

    Args:
        mode: 覆盖抓取模式（incremental/full/list_only）
        max_pages: 覆盖翻页上限
        deep_check: 覆盖深度变更检测开关（full 模式隐含开启）
    """
    cfg = _build_list_page_config(source_cfg, mode=mode, max_pages=max_pages, deep_check=deep_check)
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
        "mode": cfg.crawl_mode,
    }


def crawl_all_sources(
    progress_cb=None,
    deep_check: bool = False,
    mode: Optional[str] = None,
    max_pages: Optional[int] = None,
    sources: Optional[list[str]] = None,
) -> dict:
    """按配置文件抓取所有数据源。返回 {source_name: result_dict}。

    Args:
        progress_cb: 可选进度回调 (done:int, total:int) -> None，供任务管理器上报进度。
        deep_check: 全局深度变更检测（调度器定期深检 / 手动"深度抓取"用）。
        mode / max_pages: 覆盖所有来源的抓取模式 / 翻页上限（手动批量抓取对话框用）。
        sources: 只抓指定名称的来源（手动抓取对话框多选）；None = 全部启用来源。
    """
    school_config = get_school_config()
    results = {}
    target_names = set(sources) if sources else None
    enabled_sources = [s for s in school_config.sources if s.enabled]
    if target_names:
        enabled_sources = [s for s in enabled_sources if s.name in target_names]
        skipped = len(school_config.sources) - len(enabled_sources)
        if skipped:
            logger.info("跳过 %d 个未选中/已停用来源", skipped)
    elif len(enabled_sources) != len(school_config.sources):
        logger.info("跳过 %d 个已停用来源", len(school_config.sources) - len(enabled_sources))
    total = len(enabled_sources)
    for i, source in enumerate(enabled_sources, start=1):
        try:
            results[source.name] = crawl_source(
                source,
                mode=mode,
                max_pages=max_pages,
                deep_check=deep_check if deep_check else None,
            )
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
        if progress_cb is not None:
            progress_cb(i, total)
    return results


def crawl_source_by_name(
    source_name: str,
    progress_cb=None,
    mode: Optional[str] = None,
    max_pages: Optional[int] = None,
    deep_check: Optional[bool] = None,
) -> dict:
    """按来源名抓取单个数据源（供异步任务使用）。

    Args:
        source_name: 数据源名称（对应 config/schools/*.yaml 的 sources[].name）
        progress_cb: 可选进度回调 (done:int, total:int) -> None
        mode / max_pages / deep_check: 抓取参数覆盖（见 crawl_source）

    Returns:
        成功返回 crawl_source 的结构化结果；来源不存在/已停用返回 {"ok": False, "error": ...}。
    """
    school_config = get_school_config()
    for source in school_config.sources:
        if source.name == source_name:
            if not source.enabled:
                return {"ok": False, "error": f"数据源已停用: {source_name}"}
            if progress_cb is not None:
                progress_cb(1, 1)
            return crawl_source(
                source,
                mode=mode,
                max_pages=max_pages,
                deep_check=deep_check if deep_check is not None else source.deep_check,
            )
    return {"ok": False, "error": f"数据源不存在: {source_name}"}


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
    published_from: Optional[str] = None,
    published_to: Optional[str] = None,
    published_before: Optional[str] = None,
    crawled_from: Optional[str] = None,
    crawled_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """多条件分页查询通知列表。

    Args:
        status: raw / extracted / partial / failed
        source: 数据源名称
        notice_type: 通知类型
        keyword: 标题关键词（模糊匹配）
        is_action: 是否只返回行动型通知
        published_from/to / crawled_from/to: 时间范围筛选（含边界）
        published_before: 发布时间严格早于该日期（清理预设）
        page: 页码（从 1 起）
        page_size: 每页条数

    Returns:
        {"items": [...], "total": int, "page": int, "page_size": int}
    """
    conn = get_connection()
    try:
        where, params = build_notice_where(
            {
                "status": status,
                "source": source,
                "notice_type": notice_type,
                "published_from": published_from,
                "published_to": published_to,
                "published_before": published_before,
                "crawled_from": crawled_from,
                "crawled_to": crawled_to,
            }
        )
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

        w = (" WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) AS n FROM notices{w}", params).fetchone()["n"]

        offset = max(0, (page - 1) * page_size)
        rows = conn.execute(
            f"SELECT * FROM notices{w} ORDER BY crawled_at DESC, id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    finally:
        conn.close()


def get_notice_meta() -> dict:
    """通知元信息：状态/类型的中文标签映射（翻译单一事实源在 core/models.py）。"""
    from core.models import ACTION_NOTICE_TYPES, NOTICE_TYPE_LABELS, STATUS_LABELS

    return {
        "statuses": [{"value": k, "label": v} for k, v in STATUS_LABELS.items()],
        "notice_types": [
            {"value": k, "label": v} for k, v in NOTICE_TYPE_LABELS.items()
        ],
        "action_notice_types": sorted(ACTION_NOTICE_TYPES),
    }


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


def prefilter_notice(notice: dict, cfg: ExtractConfig) -> tuple[bool, Optional[str]]:
    """提取前置规则预检（零 LLM 成本）。返回 (是否通过, 跳过原因)。

    判定顺序：时效 → 正文长度 → 关键词白名单 → 标题黑名单 → 时间线索 → 仅订阅命中。
    所有开关可关（默认宽松：只开时效与长度），关闭后行为接近现状。
    """
    if cfg.max_age_days:
        # 优先按发布时间过滤（用户预期），发布时间缺失时回退抓取时间
        age_src = notice.get("published_at") or notice.get("crawled_at")
        if age_src:
            try:
                age_dt = datetime.fromisoformat(age_src)
                if age_dt < datetime.now() - timedelta(days=cfg.max_age_days):
                    label = "发布时间" if notice.get("published_at") else "抓取时间"
                    return False, f"{label}超过 {cfg.max_age_days} 天"
            except ValueError:
                pass

    content = notice.get("raw_content") or ""
    if len(content) < cfg.min_content_length:
        return False, f"正文过短（{len(content)} 字符 < {cfg.min_content_length}）"

    title = notice.get("title") or ""
    text = f"{title}\n{content}"

    if cfg.keyword_filter:
        kws = [k.strip() for k in cfg.keyword_filter.split(",") if k.strip()]
        if kws and not any(k in text for k in kws):
            return False, "不包含任一关注关键词"

    if cfg.skip_keywords:
        kws = [k.strip() for k in cfg.skip_keywords.split(",") if k.strip()]
        if kws:
            hit = next((k for k in kws if k in title), None)
            if hit:
                return False, f"标题包含排除词「{hit}」"

    if cfg.require_time_hint and not _TIME_HINT_PATTERN.search(text):
        return False, "无时间线索（报名/截止/日期等）"

    if cfg.match_subscription_only:
        from storage.db import get_matches_for_notice

        conn = get_connection()
        try:
            matches = get_matches_for_notice(conn, notice["id"])
        finally:
            conn.close()
        if not matches:
            return False, "未命中任何订阅"

    return True, None


def _gather_extract_candidates(
    cfg: ExtractConfig,
    limit: int = 50,
    prefilter: bool = True,
    notice_ids: Optional[list[int]] = None,
) -> tuple[list[dict], list[dict]]:
    """收集提取候选并跑预筛判定（不落库）。返回 (待提取列表, 跳过列表)。

    候选 = raw（排除已预筛）+ failed（retry_failed 开启时，供重试）。
    notice_ids 非空时：显式勾选，仅取指定 id，且跳过预筛（用户已确认）。
    """
    effective_limit = limit if limit and limit > 0 else cfg.batch_limit

    conn = get_connection()
    try:
        raw_notices = get_notices_by_status(
            conn, "raw", limit=effective_limit * 3, exclude_prefiltered=True
        )
        failed_notices = (
            get_notices_by_status(conn, "failed", limit=effective_limit * 3)
            if cfg.retry_failed
            else []
        )
    finally:
        conn.close()

    seen: set[int] = set()
    candidates: list[dict] = []
    for n in raw_notices + failed_notices:
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        candidates.append(n)

    if notice_ids:
        id_set = set(notice_ids)
        candidates = [n for n in candidates if n["id"] in id_set]
        prefilter = False

    skipped: list[dict] = []
    notices: list[dict] = []
    if prefilter:
        for n in candidates:
            ok, reason = prefilter_notice(n, cfg)
            if ok:
                notices.append(n)
            else:
                skipped.append({"id": n["id"], "title": n["title"], "reason": reason})
            if len(notices) >= effective_limit:
                break
    else:
        notices = candidates[:effective_limit]
    return notices, skipped


def extract_preview(limit: int = 0) -> dict:
    """提取前预览（dry-run）：对待提取候选跑预筛判定，不落库不改状态。

    返回 {"passed": [...], "skipped": [...]}，每条含 id/title/url/source/
    published_at/status，skipped 额外带 reason，供前端勾选后提交 notice_ids。
    """
    cfg = ConfigStore.get_instance().get_extract()
    notices, skipped = _gather_extract_candidates(cfg, limit=limit, prefilter=True)

    def _item(n: dict, reason: Optional[str] = None) -> dict:
        return {
            "id": n["id"],
            "title": n.get("title") or "",
            "url": n.get("url") or "",
            "source": n.get("source") or "",
            "published_at": n.get("published_at"),
            "status": n.get("status") or "",
            "reason": reason,
        }

    return {
        "passed": [_item(n) for n in notices],
        "skipped": [_item(s, s.get("reason")) for s in skipped],
    }


def extract_batch(
    limit: int = 50,
    auto_index: bool = True,
    extractor: NoticeExtractor | None = None,
    progress_cb=None,
    prefilter: bool = True,
    extract_cfg: ExtractConfig | None = None,
    notice_ids: Optional[list[int]] = None,
) -> dict:
    """批量提取所有 status=raw 的通知（断点续跑的提取游标 + 阶段 7 前置过滤）。

    Args:
        limit: 最大处理条数（预筛通过后才计入；<=0 时取 config.extract.batch_limit）
        auto_index: 每条提取成功后是否自动加入向量索引
        extractor: 可注入（测试用），默认真实 NoticeExtractor
        progress_cb: 可选进度回调 (done:int, total:int) -> None，供任务管理器上报进度
        prefilter: 是否启用规则预筛（读取 config.extract；跳过项写 extract_skipped_reason，
                   状态保持 raw，下轮不再重复判定）
        extract_cfg: 可注入提取配置（测试用），默认读 ConfigStore
        notice_ids: 显式指定要提取的通知 id（提取前预览勾选提交；命中即跳过预筛）
    """
    cfg = extract_cfg or ConfigStore.get_instance().get_extract()
    notices, skipped = _gather_extract_candidates(
        cfg, limit=limit, prefilter=prefilter, notice_ids=notice_ids
    )

    if skipped:
        conn = get_connection()
        try:
            for s in skipped:
                mark_prefiltered(conn, s["id"], s["reason"])
        finally:
            conn.close()

    if not notices:
        return {"processed": 0, "prefiltered": len(skipped), "summary": {}}

    extractor = extractor or NoticeExtractor()
    total = len(notices)

    async def _run() -> dict:
        summary = {"extracted": 0, "partial": 0, "failed": 0, "details": []}
        for i, notice in enumerate(notices, start=1):
            if progress_cb is not None:
                progress_cb(i, total)
            try:
                if cfg.skip_llm:
                    # 省 token 模式：不调 LLM，仅订阅匹配 + 建索引，状态置 partial（仅索引未结构化）
                    status = "partial"
                    conn2 = get_connection()
                    try:
                        update_extraction(conn2, notice["id"], {}, "partial")
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
                    summary["partial"] += 1
                    summary["details"].append(
                        {
                            "id": notice["id"],
                            "title": notice["title"],
                            "status": status,
                            "error": None,
                            "skipped_llm": True,
                        }
                    )
                    continue

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
    return {
        "processed": len(notices),
        "prefiltered": len(skipped),
        "summary": summary,
    }
