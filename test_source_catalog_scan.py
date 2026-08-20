"""扫描数据源中心全部来源的列表页，自动分类"预览异常"四类情况。

分类（对应前端预览表现）：
  OK           正常：日期提取成功且标题无日期污染（独立日期元素 + 正确颜色）
  TITLE_DATED  标题含日期：日期与标题同一字体紧挨（链接文本自带日期）
  NO_DATE      只抓到标题无日期：列表页解析不出日期
  PAGINATION   抓到换页标签：自动发现选中分页链接（1 2 3 4 下页）

对"无日期"站点额外分析列表页 HTML 中日期元素的形态与格式，判断
是"网站真没日期"还是"解析器不认这个格式"，为扩展解析器提供依据。

用法：
    python test_source_catalog_scan.py               # 扫描全部来源
    python test_source_catalog_scan.py --source id   # 只扫指定来源
    python test_source_catalog_scan.py --save        # 结果同时存 JSON

只读逻辑，不写库。
"""
import argparse
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from crawler.base import (
    ListPageParser,
    PageFetcher,
    _extract_date_from_fragment,
    _strip_title_date,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

PAGINATION_KEYWORDS = {"首页", "上页", "上一页", "下页", "下一页", "尾页", "末页", "prev", "next", "last", "»", "›"}

# 页面文本中出现的日期格式（用于判断站点用什么格式展示日期）
DATE_FMT_PATTERNS = [
    ("YYYY-MM-DD", re.compile(r"(?:20\d{2})-(?:\d{1,2})-(?:\d{1,2})")),
    ("YYYY/MM/DD", re.compile(r"(?:20\d{2})/(?:\d{1,2})/(?:\d{1,2})")),
    ("YYYY.MM.DD", re.compile(r"(?:20\d{2})\.(?:\d{1,2})\.(?:\d{1,2})")),
    ("YYYY年M月D日", re.compile(r"(?:20\d{2})年(?:\d{1,2})月(?:\d{1,2})日")),
    ("YYYY-MM(仅年月)", re.compile(r"(?:20\d{2})-(?:\d{1,2})(?![-/.\d])")),
    ("MM-DD(无年)", re.compile(r"(?<!\d)(?:\d{1,2})-(?:\d{1,2})(?![-/.\d])")),
]


def load_sources() -> list[dict]:
    cat = yaml.safe_load(open("config/source_catalog.yaml", encoding="utf-8"))
    return cat.get("sources", [])


def analyze_html_dates(soup, html: str) -> dict:
    """分析列表页中日期元素的形态，返回 {格式: 计数} 与元素位置分布。"""
    fmt_count: Counter = Counter()
    for name, pat in DATE_FMT_PATTERNS:
        fmt_count[name] = len(pat.findall(html))

    # 日期所在元素形态
    elem_forms: Counter = Counter()
    dated_links = 0
    total_links = 0
    for a in soup.find_all("a", href=True):
        total_links += 1
        txt = a.get_text(" ", strip=True)
        if _extract_date_from_fragment(txt):
            dated_links += 1
            # 链接文本含日期 → 标题与日期同字体紧挨
            elem_forms["链接文本内(同字体)"] += 1
    # 独立日期元素（dt/tr/td/li 内非链接部分）
    for dt in soup.find_all("dt"):
        sp = dt.find("span", class_=lambda c: c and any("date" in x or "time" in x for x in (c if isinstance(c, list) else [c])))
        if sp and re.search(r"(?:20\d{2})", sp.get_text()):
            elem_forms["dt>span.date/time"] += 1
    for tr in soup.find_all("tr"):
        for td in tr.find_all("td"):
            cls = td.get("class") or []
            if any("date" in c or "time" in c for c in cls) and re.search(r"(?:20\d{2})", td.get_text()):
                elem_forms["tr>td.date/postTime"] += 1
                break
    return {
        "date_formats": dict(fmt_count),
        "element_forms": dict(elem_forms),
        "dated_links": dated_links,
        "total_links": total_links,
    }


def scan_source(source: dict, fetcher: PageFetcher) -> dict:
    sid = source["id"]
    name = source.get("name", "")
    url = source.get("list_url", "")
    out = {
        "id": sid, "name": name, "url": url,
        "category": None, "detail": "", "fetch_ok": False,
        "links": 0, "dated": 0, "title_dated": 0, "pagination_like": 0,
        "samples": [], "html_analysis": None,
    }
    try:
        html = fetcher.fetch(url)
    except Exception as e:
        out["category"] = "FETCH_FAIL"
        out["detail"] = f"{type(e).__name__}: {e}"
        return out
    out["fetch_ok"] = True

    soup_holder = None
    try:
        parser = ListPageParser(html, url)
        notices = parser.discover_notice_links(None)
        out["links"] = len(notices)
    except Exception as e:
        out["category"] = "PARSE_FAIL"
        out["detail"] = f"{type(e).__name__}: {e}"
        return out

    for n in notices:
        if n.published_at:
            out["dated"] += 1
        stripped = _strip_title_date(n.title)
        if stripped != n.title.strip():
            out["title_dated"] += 1
        if n.title.strip() in PAGINATION_KEYWORDS or re.fullmatch(r"\d{1,3}", n.title.strip()):
            out["pagination_like"] += 1
        if len(out["samples"]) < 5:
            out["samples"].append((stripped[:30], n.published_at or ""))

    # 分类判定
    n = out["links"]
    if n == 0:
        out["category"] = "NO_LINK"
        out["detail"] = "未发现任何链接（独立系统/动态加载）"
    elif out["pagination_like"] >= max(3, n * 0.5):
        out["category"] = "PAGINATION"
        out["detail"] = f"发现的链接多为翻页标签（{out['pagination_like']}/{n}）"
    elif out["dated"] == n and out["title_dated"] == 0:
        out["category"] = "OK"
        out["detail"] = "日期独立提取成功"
    elif out["dated"] == n and out["title_dated"] > 0:
        out["category"] = "TITLE_DATED"
        out["detail"] = f"日期在链接文本内，与标题同字体（{out['title_dated']}/{n} 条，已自动剥离）"
    elif out["dated"] > 0:
        out["category"] = "PARTIAL_DATE"
        out["detail"] = f"部分有日期 {out['dated']}/{n}，标题含日期 {out['title_dated']}"
    elif out["title_dated"] == n and n > 0:
        out["category"] = "TITLE_DATED"
        out["detail"] = f"全部为标题内日期（{n} 条，已自动剥离）"
    else:
        out["category"] = "NO_DATE"
        out["detail"] = "列表页解析不出日期"

    # 对异常类（无日期/部分/标题内/分页）做 HTML 形态分析
    if out["category"] in ("NO_DATE", "PARTIAL_DATE", "TITLE_DATED", "PAGINATION"):
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            out["html_analysis"] = analyze_html_dates(soup, html)
        except Exception:
            pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="扫描数据源中心全部来源的列表页解析情况")
    ap.add_argument("--source", help="只扫指定来源 id")
    ap.add_argument("--save", action="store_true", help="结果同时保存 JSON")
    args = ap.parse_args()

    sources = load_sources()
    if args.source:
        sources = [s for s in sources if s["id"] == args.source]

    fetcher = PageFetcher()
    results = []
    for i, src in enumerate(sources, 1):
        r = scan_source(src, fetcher)
        results.append(r)
        print(f"[{i}/{len(sources)}] {r['category']:<12} {r['id']} {r['name']} | {r['detail']}")
        for t, d in r["samples"]:
            print(f"       样例: {t} | {d}")

    # 汇总
    cat_counter = Counter(r["category"] for r in results)
    print("\n" + "=" * 60)
    print("分类汇总:")
    for cat, cnt in sorted(cat_counter.items(), key=lambda x: -x[1]):
        print(f"  {cat:<12} {cnt} 个")
    ok = cat_counter.get("OK", 0)
    print(f"  正常率: {ok}/{len(results)} = {ok/len(results):.0%}")

    # 每类详细清单
    print("\n" + "=" * 60)
    for cat in ["OK", "TITLE_DATED", "NO_DATE", "PARTIAL_DATE", "PAGINATION", "NO_LINK", "FETCH_FAIL", "PARSE_FAIL"]:
        items = [r for r in results if r["category"] == cat]
        if not items:
            continue
        print(f"\n--- {cat} ({len(items)}) ---")
        for r in items:
            print(f"  {r['id']} {r['name']} | {r['url']}")

    # 无日期类站的 HTML 形态分析（供扩展解析器）
    print("\n" + "=" * 60)
    print("无法正常提取日期站点的 HTML 日期形态分析:")
    for r in results:
        if r["category"] in ("NO_DATE", "PARTIAL_DATE") and r["html_analysis"]:
            ha = r["html_analysis"]
            fmts = {k: v for k, v in ha["date_formats"].items() if v}
            elems = {k: v for k, v in ha["element_forms"].items() if v}
            print(f"\n  [{r['id']}] {r['name']}")
            if fmts:
                print(f"    页面日期格式: {fmts}")
            else:
                print("    页面文本中未发现任何日期格式")
            if elems:
                print(f"    日期元素形态: {elems}")
            print(f"    链接总数 {ha['total_links']}, 链接文本含日期 {ha['dated_links']}")

    if args.save:
        Path("data/scan_source_catalog_result.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n结果已保存: data/scan_source_catalog_result.json")


if __name__ == "__main__":
    main()
