"""爬虫基类：列表页自动发现 + 翻页 + 详情页提取。

通用化设计思路：
1. 给定一个列表页 URL，自动发现通知链接（URL 模式聚类）
2. 自动发现翻页链接（页码 / 下一页 / 尾页）
3. 用 newspaper4k 提取详情页标题和正文
4. 配置文件可覆盖自动发现结果（手动指定 url_pattern / pagination）
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# 支持两种导入方式：包内相对导入 / 脚本直接运行
try:
    from ..storage.models import NoticeItem
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from storage.models import NoticeItem

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_PAGES = 20  # 最多翻多少页，防止失控


@dataclass
class ListPageConfig:
    """单个列表页的配置（可从 YAML 加载，也可自动发现）。"""

    list_url: str
    source_name: str = ""
    # 可选：手动指定通知链接的 URL 正则（覆盖自动发现）
    url_pattern: Optional[str] = None
    # 可选：手动指定翻页链接的 CSS 选择器
    pagination_selector: Optional[str] = None
    # 可选：最大翻页数
    max_pages: int = DEFAULT_MAX_PAGES


@dataclass
class PaginationInfo:
    """翻页信息。"""

    page_urls: list[str] = field(default_factory=list)
    total_pages: int = 1


class ListPageParser:
    """列表页解析器：自动发现通知链接和翻页。

    通用化策略：
    - 通知链接：把所有链接的数字部分替换为 {N}，按模式聚类，取数量最多的模式
    - 翻页：找"下一页/下页/Next"文字链接，或数字页码链接
    """

    # 翻页关键词
    NEXT_PAGE_KEYWORDS = ["下一页", "下页", "next", "Next", "»", "›"]
    LAST_PAGE_KEYWORDS = ["尾页", "末页", "last", "Last", "»"]
    PAGE_KEYWORDS = ["首页", "上页", "上一页", "prev", "Prev"]

    def __init__(self, html: str, base_url: str):
        self.soup = BeautifulSoup(html, "html.parser")
        self.base_url = base_url

    def discover_notice_links(
        self, url_pattern: Optional[str] = None
    ) -> list[NoticeItem]:
        """发现通知链接。

        Args:
            url_pattern: 可选的 URL 正则，覆盖自动发现

        Returns:
            通知链接列表
        """
        all_links = self._extract_all_links()

        if url_pattern:
            # 用配置的正则过滤
            pattern = re.compile(url_pattern)
            filtered = [
                (text, url) for text, url in all_links if pattern.search(url)
            ]
        else:
            # 自动发现：URL 模式聚类
            filtered = self._auto_discover_links(all_links)

        # 从 <dt> 元素提取日期，构建 url -> date 映射
        date_map = self._extract_dates_from_list()

        return [
            NoticeItem(
                url=url,
                title=text,
                list_source=self.base_url,
                published_at=date_map.get(url),
            )
            for text, url in filtered
            if text  # 过滤掉空标题
        ]

    def discover_pagination(self) -> PaginationInfo:
        """发现翻页信息。

        策略：
        1. 找数字页码链接（直接显示的）
        2. 找"下一页"链接
        3. 找"尾页"链接，推算总页数，生成缺失的页码 URL
        """
        page_urls: list[str] = []
        seen: set[str] = set()
        last_page_url: Optional[str] = None
        next_page_url: Optional[str] = None

        for a in self.soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"]
            full_url = urljoin(self.base_url, href)

            # 数字页码
            if text.isdigit():
                if full_url not in seen and full_url != self.base_url:
                    page_urls.append(full_url)
                    seen.add(full_url)

            # "下一页"
            if any(kw in text for kw in self.NEXT_PAGE_KEYWORDS):
                if full_url not in seen and full_url != self.base_url:
                    page_urls.append(full_url)
                    seen.add(full_url)
                    next_page_url = full_url

            # "尾页" → 推算总页数
            if any(kw in text for kw in self.LAST_PAGE_KEYWORDS):
                last_page_url = full_url

        # 策略3：从尾页 URL 推算总页数，生成缺失的页码
        if last_page_url:
            inferred = self._infer_missing_pages(last_page_url, seen)
            page_urls.extend(inferred)

        total_pages = len(page_urls) + 1
        return PaginationInfo(page_urls=page_urls, total_pages=total_pages)

    def _infer_missing_pages(
        self, last_page_url: str, seen: set[str]
    ) -> list[str]:
        """从尾页 URL 推算页码规律，生成缺失的页码 URL。

        常见模式：
        - jstz/1.htm 是尾页 → jstz/2.htm, jstz/3.htm, ...
        - tzgg/1.htm 是尾页 → tzgg/2.htm, tzgg/3.htm, ...
        页码数字在 URL 路径中，尾页通常是 /1.htm，前面的页码递增。
        """
        import re as re_module

        # 从尾页 URL 提取数字部分
        match = re_module.search(r"/(\d+)\.htm", last_page_url)
        if not match:
            return []

        last_num = int(match.group(1))
        # 尾页是 1.htm，说明页码是倒序的：第一页无后缀，第二页是 58.htm，... 尾页是 1.htm
        # 总页数 = last_num + 1（第一页是 list_url 本身，无数字后缀）
        # 但也可能第一页是 59.htm，最后一页是 1.htm
        # 我们需要找规律：已知的页码 URL 里数字的范围

        # 收集已知页码的数字
        known_nums: list[int] = []
        for url in seen:
            m = re_module.search(r"/(\d+)\.htm", url)
            if m:
                known_nums.append(int(m.group(1)))

        if not known_nums:
            return []

        max_known = max(known_nums)
        # 页码范围：从 1 到 max_known（倒序排列）
        # 生成所有缺失的页码 URL
        prefix = last_page_url.rsplit("/", 1)[0] + "/"
        suffix = ".htm"

        missing: list[str] = []
        for num in range(1, max_known + 1):
            url = f"{prefix}{num}{suffix}"
            if url not in seen and url != self.base_url:
                missing.append(url)

        return missing

    def _extract_all_links(self) -> list[tuple[str, str]]:
        """提取页面所有链接，返回 (text, full_url) 列表。"""
        links = []
        for a in self.soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            # 跳过锚点、javascript、mailto
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            full_url = urljoin(self.base_url, href)
            links.append((text, full_url))
        return links

    def _auto_discover_links(
        self, all_links: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """自动发现通知链接：URL 模式聚类。

        把 URL 中的数字部分替换为 {N}，按模式分组，
        取数量最多的模式作为通知链接模式。
        """
        # 生成 URL 模式（数字替换为 {N}）
        pattern_counter: Counter[str] = Counter()
        pattern_links: dict[str, list[tuple[str, str]]] = {}

        for text, url in all_links:
            # 只处理同域名或相对路径的链接
            parsed = urlparse(url)
            base_parsed = urlparse(self.base_url)
            if parsed.netloc and parsed.netloc != base_parsed.netloc:
                continue  # 跳过外站链接

            # 生成模式：把路径中的数字替换为 {N}
            path = parsed.path
            pattern = re.sub(r"\d+", "{N}", path)
            if "{N}" not in pattern:
                continue  # 没有数字的路径不太可能是通知详情页

            pattern_counter[pattern] += 1
            if pattern not in pattern_links:
                pattern_links[pattern] = []
            pattern_links[pattern].append((text, url))

        if not pattern_counter:
            return []

        # 取数量最多的模式
        best_pattern = pattern_counter.most_common(1)[0][0]
        return pattern_links[best_pattern]

    def _extract_dates_from_list(self) -> dict[str, str]:
        """从列表页提取日期，返回 {url: date} 映射。

        支持三种常见格式：
        1. <dt> 内 <span class="date">日期</span>（教务处格式）
        2. <tr> 内 <td class="postTime">日期</td>（创新创业学院主列表格式）
        3. <tr> 内 <div style="white-space:nowrap">日期</div>（侧边栏格式）
        """
        import re
        date_map: dict[str, str] = {}
        date_pattern = re.compile(r"\d{4}-\d{2}-\d{2}")

        # 格式1: <dt> 内的 <span class="date"> 或 <span class="time">
        for dt in self.soup.find_all("dt"):
            a = dt.find("a", href=True)
            if not a:
                continue
            full_url = urljoin(self.base_url, a["href"])
            date_span = dt.find("span", class_="date") or dt.find("span", class_="time")
            if date_span:
                date_text = date_span.get_text(strip=True)
                if date_text:
                    date_map[full_url] = date_text

        # 格式2+3: <tr> 内的日期（postTime 类 或 white-space:nowrap div）
        for tr in self.soup.find_all("tr"):
            a = tr.find("a", href=True)
            if not a:
                continue
            full_url = urljoin(self.base_url, a["href"])
            if full_url in date_map:
                continue  # 已有日期，跳过

            # 查找 postTime 类的 td
            for td in tr.find_all("td"):
                classes = td.get("class", [])
                if "postTime" in classes or "date" in classes:
                    date_text = td.get_text(strip=True)
                    if date_text:
                        date_map[full_url] = date_text
                        break

            # 查找 white-space:nowrap 的 div（侧边栏格式）
            if full_url not in date_map:
                for div in tr.find_all("div"):
                    style = div.get("style", "")
                    if "white-space" in style or "nowrap" in style:
                        date_text = div.get_text(strip=True)
                        if date_pattern.search(date_text):
                            date_map[full_url] = date_text
                            break

        return date_map


class PageFetcher:
    """HTTP 页面抓取器。"""

    def __init__(self, headers: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT):
        self.headers = {**DEFAULT_HEADERS, **(headers or {})}
        self.timeout = timeout

    def fetch(self, url: str) -> str:
        """抓取页面，返回 HTML 文本。"""
        resp = requests.get(url, headers=self.headers, timeout=self.timeout)
        resp.encoding = resp.apparent_encoding  # 自动检测编码（中文网站关键）
        return resp.text