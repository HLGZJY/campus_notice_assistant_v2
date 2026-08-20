"""网页爬虫：列表页遍历 + 详情页提取（阶段 7：增量早停 + 抓取模式）。

工作流程：
1. 抓取列表页第一页，自动发现通知链接 + 翻页链接
2. 按 crawl_mode 遍历翻页：
   - incremental（默认）：遇到"整页通知均已入库"立即停止翻页；
   - full：翻满 max_pages 并对已入库通知重抓详情页做变更检测；
   - list_only：只收录列表页标题/日期，不抓详情页
3. 仅对新增 URL 抓详情页（incremental 默认不重抓已入库详情页，除非 deep_check）
4. 存入 SQLite（URL 去重）
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

import newspaper

try:
    from ..storage.db import (
        compute_content_hash,
        get_connection,
        get_notice_by_url,
        insert_notice,
        log_crawl,
        update_notice_content,
        update_notice_date,
    )
    from ..storage.models import CrawlResult, NoticeItem, NoticeRecord
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from storage.db import (
        compute_content_hash,
        get_connection,
        get_notice_by_url,
        insert_notice,
        log_crawl,
        update_notice_content,
        update_notice_date,
    )
    from storage.models import CrawlResult, NoticeItem, NoticeRecord

from .base import (
    ListPageConfig,
    ListPageParser,
    PageFetcher,
    _extract_date_from_fragment,
)

logger = logging.getLogger(__name__)


def _is_error_page(html: Optional[str]) -> bool:
    """详情页是否为 404/错误提示页（此类页面不应入库，也不应提取日期）。"""
    if not html:
        return True
    import re

    head = html[:4000]
    return bool(
        re.search(
            r"<title>\s*404|404\s*错误|页面不存在|无法找到该页|错误提示|您访问的页面",
            head,
            re.I,
        )
    )


def _extract_date_from_detail_html(html: Optional[str]) -> Optional[str]:
    """从详情页 HTML 提取发布时间（newspaper4k 无法解析时兜底）。

    学校网站常见形态：<span>发布时间：2026/07/16</span>、2026-07-16、2026年07月16日。
    返回规范化 'YYYY-MM-DD'；无则 None。
    若页面为 404/错误提示页则直接返回 None（避免误取错误页模板里的日期）。
    """
    if not html:
        return None
    import re

    # 404 / 错误页防护：避免把错误页模板里的日期当成文章发布时间
    if _is_error_page(html):
        return None

    # 优先找"发布时间/日期"标签附近的日期
    m = re.search(
        r"发布\s*时间?[:：]?\s*((?:20\d{2})[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
        html,
    )
    if not m:
        m = re.search(r"((?:20\d{2})[-/.年]\d{1,2}[-/.月]\d{1,2}日?)", html)
    if not m:
        return None
    return _extract_date_from_fragment(m.group(1))


def _match_notice_by_url(url: str) -> None:
    """抓取/内容变更后对通知执行订阅匹配（模块 3.1，失败不影响主流程）。"""
    try:
        from services.subscription_service import match_notice

        conn = get_connection()
        try:
            row = get_notice_by_url(conn, url)
        finally:
            conn.close()
        if row:
            match_notice(row["id"])
    except Exception as e:
        logger.warning("订阅匹配失败 url=%s: %s", url, e)


def _parse_published_date(value: Optional[str]) -> Optional[datetime]:
    """宽容解析列表页日期（date-only / ISO / 中文年月日 / 斜杠 / 点号）。"""
    if not value:
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    import re

    # 通用分隔格式：YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # 中文格式：YYYY年M月D日
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


class WebCrawler:
    """网页爬虫：抓取列表页所有通知，存入 SQLite。

    工作流程：
    1. 抓取列表页第一页
    2. 自动发现通知链接 + 翻页链接
    3. 按模式遍历翻页（增量早停 / 全量 / 仅列表）
    4. 对需要的新通知链接，用 newspaper4k 提取详情
    5. 存入 SQLite（URL 去重）
    """

    def __init__(self, config: ListPageConfig):
        self.config = config
        self.fetcher = PageFetcher(timeout=config.request_timeout)

    def crawl(self) -> CrawlResult:
        """执行完整抓取流程。"""
        result = CrawlResult(source=self.config.source_name or self.config.list_url)
        conn = get_connection()
        try:
            # ---------- 1. 抓取第一页，发现通知链接和翻页 ----------
            try:
                html = self.fetcher.fetch(self.config.list_url)
            except Exception as e:
                result.errors.append(f"列表页抓取失败: {type(e).__name__}: {e}")
                log_crawl(
                    conn,
                    result.source,
                    0,
                    0,
                    0,
                    0,
                    result.errors,
                    0,
                )
                return result

            known_urls = self._load_known_urls(conn)
            all_notices: dict[str, NoticeItem] = {}  # url -> NoticeItem（去重）
            # 分页页码 / 列表页自身 URL 排除集：防止分页页被当成通知收录（P0-2）
            exclude_urls: set[str] = {self.config.list_url}

            parser = ListPageParser(html, self.config.list_url)
            pagination = parser.discover_pagination()
            exclude_urls.update(pagination.page_urls)
            added, discovered = self._collect_notices(
                parser, all_notices, known_urls, exclude_urls
            )
            result.total_discovered = len(all_notices)

            # ---------- 2. 遍历翻页（增量早停 / 全量） ----------
            pages_to_crawl = pagination.page_urls[: self.config.max_pages - 1]
            pages_fetched = 1
            for page_url in pages_to_crawl:
                if self._should_stop_pagination(added, discovered):
                    break
                try:
                    page_html = self.fetcher.fetch(page_url)
                except Exception as e:
                    result.errors.append(f"翻页抓取失败 {page_url}: {e}")
                    continue
                page_parser = ListPageParser(page_html, page_url)
                page_pagination = page_parser.discover_pagination()
                exclude_urls.update(page_pagination.page_urls)
                added, discovered = self._collect_notices(
                    page_parser, all_notices, known_urls, exclude_urls
                )
                pages_fetched += 1

            result.total_discovered = len(all_notices)
            logger.info(
                f"[{result.source}] 共发现 {result.total_discovered} 条通知，"
                f"来自 {pages_fetched} 页（模式={self.config.crawl_mode}"
                + (f"，最近 {self.config.max_age_days} 天" if self.config.max_age_days else "")
                + "）"
            )

            # ---------- 3. 处理详情页：仅新增抓详情；已入库按 deep_check 决定 ----------
            new_items: list[tuple[str, NoticeItem]] = []
            for url, item in all_notices.items():
                existing = known_urls.get(url)
                if existing:
                    if self.config.deep_check or self.config.crawl_mode == "full":
                        self._deep_check_existing(conn, url, item, existing, result)
                    else:
                        # 已入库且正文未重抓：仅补日期，不触发任何提取/索引动作
                        if not existing.get("published_at") and item.published_at:
                            update_notice_date(conn, url, item.published_at)
                            result.total_updated += 1
                        else:
                            result.total_skipped += 1
                            logger.info(f"[{result.source}] skipped(已入库): {url}")
                else:
                    new_items.append((url, item))

            # 详情页抓取（并发可选；抓取只读网络，写库回到主线程，规避 SQLite 线程约束）
            records: list[tuple[str, NoticeItem, Optional[NoticeRecord]]] = []
            if new_items:
                if self.config.fetch_detail:
                    if self.config.concurrency > 1:
                        with ThreadPoolExecutor(
                            max_workers=self.config.concurrency,
                            thread_name_prefix="crawl-detail",
                        ) as pool:
                            records = list(
                                pool.map(
                                    lambda it: (
                                        it[0],
                                        it[1],
                                        self._fetch_detail_with_retry(
                                            it[0], it[1].title, it[1].published_at
                                        ),
                                    ),
                                    new_items,
                                )
                            )
                    else:
                        records = [
                            (
                                url,
                                item,
                                self._fetch_detail_with_retry(
                                    url, item.title, item.published_at
                                ),
                            )
                            for url, item in new_items
                        ]
                else:
                    # list_only：仅收录列表页标题/日期，不抓详情页
                    records = [
                        (url, item, None) for url, item in new_items
                    ]

            for url, item, record in records:
                if record is None and self.config.fetch_detail:
                    result.total_failed += 1
                    result.errors.append(f"详情页失败 {url}: 无法提取正文")
                    continue
                if record is None:
                    record = NoticeRecord(
                        url=url,
                        source=self.config.source_name,
                        title=item.title,
                        raw_content="",
                        published_at=item.published_at,
                    )
                # 时效兜底：列表页无日期时，用详情页提取的日期做 max_age_days 过滤
                # （列表页无日期的站点在 _age_ok 阶段全部放行，这里补最后一层拦截）
                if (
                    self.config.max_age_days
                    and not item.published_at
                    and record.published_at
                ):
                    published = _parse_published_date(record.published_at)
                    if published is not None:
                        cutoff = datetime.now() - timedelta(
                            days=self.config.max_age_days
                        )
                        if published < cutoff:
                            result.total_skipped += 1
                            logger.info(
                                f"[{result.source}] 详情页日期超期，跳过入库: "
                                f"{url} ({record.published_at})"
                            )
                            continue
                record.content_hash = compute_content_hash(record.raw_content)
                insert_notice(conn, record)
                result.total_new += 1
                _match_notice_by_url(url)

            log_crawl(
                conn,
                result.source,
                result.total_discovered,
                result.total_new,
                result.total_skipped,
                result.total_failed,
                result.errors,
                result.total_changed,
            )
            return result
        finally:
            conn.close()

    # ---------- 内部：列表页 ----------

    def _load_known_urls(self, conn) -> dict[str, dict]:
        """加载库中已有 URL → {url: {published_at, content_hash}}，供早停/去重/变更判断。"""
        rows = conn.execute(
            "SELECT url, published_at, content_hash FROM notices"
        ).fetchall()
        return {r["url"]: dict(r) for r in rows}

    def _collect_notices(
        self,
        parser: ListPageParser,
        all_notices: dict[str, NoticeItem],
        known_urls: dict[str, dict],
        exclude_urls: Optional[set[str]] = None,
    ) -> tuple[int, int]:
        """收集一页的通知链接（含时效过滤），返回 (本页新增数, 本页发现数)。

        exclude_urls: 分页页码/列表页自身 URL 集合，命中则不入通知池（P0-2）。
        """
        notices = parser.discover_notice_links(self.config.url_pattern)
        if self.config.max_age_days:
            notices = [n for n in notices if self._age_ok(n)]
        added = 0
        for n in notices:
            if exclude_urls and n.url in exclude_urls:
                continue
            if n.url not in all_notices and n.url not in known_urls:
                added += 1
            all_notices[n.url] = n
        return added, len(notices)

    def _age_ok(self, item: NoticeItem) -> bool:
        """时效过滤：列表页日期早于窗口则跳过（无日期不拦截）。"""
        if not self.config.max_age_days or not item.published_at:
            return True
        published = _parse_published_date(item.published_at)
        if published is None:
            return True
        cutoff = datetime.now() - timedelta(days=self.config.max_age_days)
        return published >= cutoff

    def _should_stop_pagination(self, added: int, discovered: int) -> bool:
        """增量早停：整页通知均已入库（或全被时效过滤）时停止翻页。"""
        if self.config.crawl_mode != "incremental":
            return False
        if not self.config.stop_when_caught_up:
            return False
        return discovered > 0 and added == 0

    # ---------- 内部：详情页 ----------

    def _deep_check_existing(
        self,
        conn,
        url: str,
        item: NoticeItem,
        existing: dict,
        result: CrawlResult,
    ) -> None:
        """深度变更检测：重抓已入库详情页，指纹不一致则重置为待提取。"""
        try:
            record = self._fetch_detail_with_retry(
                url, item.title, item.published_at
            )
            if not record:
                result.total_failed += 1
                result.errors.append(f"详情页失败 {url}: 无法提取正文")
                return
            record.content_hash = compute_content_hash(record.raw_content)
            if existing.get("content_hash") != record.content_hash:
                update_notice_content(
                    conn,
                    url,
                    record.title,
                    record.raw_content,
                    record.content_hash,
                )
                result.total_changed += 1
                logger.info(f"[{result.source}] 内容变更，重置为待提取: {url}")
                _match_notice_by_url(url)
            else:
                if not existing.get("published_at") and item.published_at:
                    update_notice_date(conn, url, item.published_at)
                    result.total_updated += 1
                else:
                    result.total_skipped += 1
                    logger.info(f"[{result.source}] skipped(正文未变更): {url}")
        except Exception as e:
            result.total_failed += 1
            result.errors.append(f"详情页失败 {url}: {type(e).__name__}: {e}")
            logger.warning(f"详情页失败 {url}: {e}")

    def _fetch_detail_with_retry(
        self, url: str, fallback_title: str, list_page_date: Optional[str] = None
    ) -> Optional[NoticeRecord]:
        """抓取详情页，失败按 retry_times 重试（指数退避），最终失败返回 None。"""
        for attempt in range(self.config.retry_times + 1):
            record = self._fetch_detail(url, fallback_title, list_page_date)
            if record is not None:
                return record
            if attempt < self.config.retry_times:
                time.sleep(0.5 * (attempt + 1))
        return None

    def _fetch_detail(
        self, url: str, fallback_title: str, list_page_date: Optional[str] = None
    ) -> Optional[NoticeRecord]:
        """用 newspaper4k 抓取详情页，返回 NoticeRecord。

        Args:
            url: 详情页 URL
            fallback_title: 列表页提取的标题（newspaper4k 失败时使用）
            list_page_date: 列表页提取的日期（newspaper4k 失败时使用）
        """
        try:
            article = newspaper.article(url, language="zh")
            title = article.title or fallback_title
            content = article.text or ""

            if not content:
                # newspaper4k 提取失败，用 fallback
                content = self._fallback_extract(url)

            # newspaper4k 日期优先，列表页日期作为 fallback；
            # 两者都无时从详情页 HTML 兜底提取"发布时间"（如 发布时间：2026/07/16）
            published_at = None
            if article.publish_date:
                published_at = article.publish_date.isoformat()
            elif list_page_date:
                published_at = list_page_date
            else:
                try:
                    published_at = _extract_date_from_detail_html(
                        self.fetcher.fetch(url)
                    )
                except Exception:
                    published_at = None

            return NoticeRecord(
                url=url,
                source=self.config.source_name,
                title=title,
                raw_content=content,
                published_at=published_at,
            )
        except Exception as e:
            logger.warning(f"newspaper4k 提取失败 {url}: {e}")
            # fallback：用 BeautifulSoup 提取
            try:
                html = self.fetcher.fetch(url)
                # 404/错误页不入库（如公示类文章过期被删除）
                if _is_error_page(html):
                    logger.info(f"详情页为错误页，不入库: {url}")
                    return None
                content = self._fallback_extract_from_html(html)
                return NoticeRecord(
                    url=url,
                    source=self.config.source_name,
                    title=fallback_title,
                    raw_content=content,
                    published_at=list_page_date
                    or _extract_date_from_detail_html(html),
                )
            except Exception:
                return None

    def _fallback_extract(self, url: str) -> str:
        """newspaper4k 失败时的 fallback 提取。"""
        html = self.fetcher.fetch(url)
        return self._fallback_extract_from_html(html)

    def _fallback_extract_from_html(self, html: str) -> str:
        """从 HTML 提取纯文本（fallback）。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        # 移除不需要的标签
        for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)