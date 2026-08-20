"""测试：现有抓取函数对"抓取最近 N 天"（max_age_days）过滤是否真正生效。

背景：
    用户为数据源 #7「创新创业教育实践基地-创新创业活动」配置 max_age_days=30，
    但抓取仍收录了非 30 天内的文章，怀疑列表页发布时间解析失败。

本脚本验证整条链路（与 WebCrawler 内部使用完全相同的类/函数）：
    1. 列表页链接发现          → ListPageParser.discover_notice_links()
    2. 列表页日期提取          → published_at 是否非空（_extract_dates_from_list）
    3. 时效过滤                → 模拟 WebCrawler._age_ok()，max_age_days=30
    4. 完整回放                → 临时库跑 WebCrawler.crawl()，检查入库文章的发布时间分布

用法（系统 venv，含 requests/bs4/newspaper 依赖）：
    python test_crawl_age_filter.py [--site id] [--list-url URL] [--age 30]

只读逻辑不碰正式库；crawl 回放使用 tempfile.mkdtemp() 专属临时库。
"""
import argparse
import logging
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from crawler import ListPageConfig, WebCrawler
from crawler.base import ListPageParser, PageFetcher
from crawler.web_crawler import _parse_published_date

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


def _make_age_checker(age_days: int):
    """构造一个与真实抓取完全一致的 _age_ok 判定器（复用 WebCrawler 实例方法）。"""
    crawler = WebCrawler(ListPageConfig(list_url="x", max_age_days=age_days))
    return crawler._age_ok

# 代表性站点：覆盖不同列表页 HTML 结构（dt / tr / 嵌套 li / jsp / 独立系统）
SITES = [
    ("cxcyjd-hd", "#7 创新创业教育实践基地-创新创业活动",
     "https://www.scuec.edu.cn/cxcysj/cxcyhuo_do.htm"),
    ("school-tzgg", "学校主页-通知公告",
     "https://www.scuec.edu.cn/sylm/tzgg.htm"),
    ("cxcy-jstz", "创新创业学院-竞赛通知",
     "https://www.scuec.edu.cn/cxcy/scss/jstz.htm"),
    ("cxcy-tzgg", "创新创业学院-通知公告",
     "https://www.scuec.edu.cn/cxcy/tzgg.htm"),
    ("jwc-tzgg", "教务处-通知公告",
     "https://www.scuec.edu.cn/jwc/tzgg.htm"),
    ("xb-tzgg", "党政办公室-通知公告",
     "https://www.scuec.edu.cn/xb/index/tzgg.htm"),
    ("yjsy-tzgg", "研究生院-通知公告",
     "https://www.scuec.edu.cn/yjsy/yjspy/tzgg.htm"),
    ("syxy-tzgg", "生物医学工程学院-通知公告",
     "https://www.scuec.edu.cn/syxy/lmy-tzgg.jsp?urltype=tree.TreeTempUrl&wbtreeid=1296"),
    ("lib-gzdt", "图书馆-工作动态（独立系统）",
     "https://lib.scmu.edu.cn/news/web_newsList?cid=C87DB29C-DB36-9666-35D5-8FEADD07D2C9"),
]

DEFAULT_AGE_DAYS = 30


def probe_site(site_id: str, name: str, list_url: str, age_days: int) -> dict:
    """对单个站点跑 列表页解析 + 日期提取 + 时效过滤 探针，不写库。"""
    out = {
        "id": site_id, "name": name, "url": list_url,
        "fetched": False, "links": 0, "with_date": 0,
        "date_ratio": 0.0, "after_filter": 0, "dropped_by_age": 0,
        "sample": [], "parse_fail_sample": [], "error": None,
    }
    fetcher = PageFetcher()
    try:
        html = fetcher.fetch(list_url)
        out["fetched"] = True
    except Exception as e:
        out["error"] = f"列表页抓取失败: {type(e).__name__}: {e}"
        return out

    parser = ListPageParser(html, list_url)
    try:
        notices = parser.discover_notice_links(url_pattern=None)
    except Exception as e:
        out["error"] = f"链接发现失败: {type(e).__name__}: {e}"
        return out

    out["links"] = len(notices)
    age_ok = _make_age_checker(age_days)
    for n in notices:
        has_date = bool(n.published_at)
        if has_date:
            out["with_date"] += 1
            parsed = _parse_published_date(n.published_at)
            if parsed is None:
                out["parse_fail_sample"].append((n.title[:24], n.published_at))
        else:
            out["parse_fail_sample"].append((n.title[:24], None))
        if age_ok(n):
            out["after_filter"] += 1
        else:
            out["dropped_by_age"] += 1
        if len(out["sample"]) < 6:
            out["sample"].append((n.title[:28], n.published_at or "(无日期)"))

    out["date_ratio"] = (out["with_date"] / out["links"]) if out["links"] else 0.0
    return out


def replay_crawl(
    list_url: str, name: str, age_days: int, max_pages: int,
    fetch_detail: bool = False,
) -> dict:
    """用临时库完整回放 WebCrawler.crawl()，检查入库文章发布时间分布。

    fetch_detail=False（list_only）聚焦"列表页时效过滤"本身；
    fetch_detail=True 走完整详情页流程（含 newspaper4k + 详情页日期兜底）。
    入库记录均走 insert_notice 全流程。
    """
    import storage.db as db

    tmpdir = tempfile.mkdtemp(prefix="crawl_age_test_")
    tmp_db = Path(tmpdir) / "test.db"
    old_path, db.DB_PATH = db.DB_PATH, tmp_db  # 临时库隔离，不污染 data/

    from crawler import ListPageConfig, WebCrawler

    cfg = ListPageConfig(
        list_url=list_url,
        source_name=name,
        max_pages=max_pages,
        crawl_mode="incremental",
        max_age_days=age_days,
        fetch_detail=fetch_detail,
        stop_when_caught_up=True,
    )
    try:
        result = WebCrawler(cfg).crawl()
        conn = db.get_connection()
        try:
            rows = conn.execute(
                "SELECT title, published_at FROM notices ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        dates = [r["published_at"] for r in rows]
        cutoff = datetime.now() - timedelta(days=age_days)
        over = 0
        for d in dates:
            p = _parse_published_date(d) if d else None
            if p is None:
                over += 1  # 无日期也算"无法证明在窗口内"
            elif p < cutoff:
                over += 1
        return {
            "discovered": result.total_discovered,
            "new": result.total_new,
            "skipped": result.total_skipped,
            "failed": result.total_failed,
            "errors": result.errors[:5],
            "in_db": len(rows),
            "in_db_with_date": sum(1 for d in dates if d),
            "in_db_over_age_or_nodate": over,
            "samples": [(r["title"][:30], r["published_at"] or "(无日期)") for r in rows[:12]],
        }
    finally:
        db.DB_PATH = old_path


def print_probe(probe: dict, age_days: int) -> None:
    print(f"\n[{probe['id']}] {probe['name']}")
    print(f"  URL: {probe['url']}")
    if probe["error"]:
        print(f"  !! {probe['error']}")
        return
    print(f"  发现链接: {probe['links']} | 带日期: {probe['with_date']} "
          f"({probe['date_ratio']:.0%}) | {age_days}天过滤后: {probe['after_filter']} "
          f"(被拦截: {probe['dropped_by_age']})")
    for t, d in probe["sample"]:
        flag = "✔" if d else "✘无日期"
        print(f"    {flag} {t} | {d}")
    if probe["parse_fail_sample"]:
        print(f"  ※ 有日期但 _parse_published_date 解析失败: "
              f"{probe['parse_fail_sample'][:3]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="验证 max_age_days 时效过滤是否生效")
    ap.add_argument("--site", help="只测指定站点 id（见脚本 SITES 列表）")
    ap.add_argument("--list-url", help="自定义列表页 URL")
    ap.add_argument("--age", type=int, default=DEFAULT_AGE_DAYS, help="时效天数")
    ap.add_argument("--replay", action="store_true", help="对 #7 跑完整 crawl 回放")
    ap.add_argument(
        "--offline", metavar="HTML文件",
        help="用本地保存的列表页 HTML 做可复现的离线解析验证（排除网站版本轮换干扰）",
    )
    args = ap.parse_args()

    if args.offline:
        print(f"=== 离线解析验证: {args.offline}（{args.age}天过滤）===")
        html = Path(args.offline).read_text(encoding="utf-8")
        url = "https://www.scuec.edu.cn/cxcysj/cxcyhuo_do.htm"
        parser = ListPageParser(html, url)
        notices = parser.discover_notice_links(None)
        age_ok = _make_age_checker(args.age)
        kept = [n for n in notices if age_ok(n)]
        dated = sum(1 for n in notices if n.published_at)
        print(f"  发现 {len(notices)} | 带日期 {dated} | {args.age}天过滤后保留 {len(kept)}")
        print("  保留条目:")
        for n in kept:
            print(f"    {n.published_at or '(无日期)'} | {n.title[:40]}")
        print("  被拦截（超窗口）:")
        for n in notices:
            if not age_ok(n):
                print(f"    {n.published_at} | {n.title[:40]}")
        return

    print(f"=== 列表页解析 + 日期提取 + {args.age}天时效过滤 探针（{datetime.now():%Y-%m-%d %H:%M}）===")

    sites = SITES
    if args.list_url:
        sites = [("custom", "自定义站点", args.list_url)]
    elif args.site:
        sites = [s for s in SITES if s[0] == args.site]

    for site_id, name, url in sites:
        probe = probe_site(site_id, name, url, args.age)
        print_probe(probe, args.age)

    if args.replay:
        print("\n\n=== #7 完整 crawl 回放（临时库，list_only，验证列表页过滤层）===")
        r = replay_crawl(
            "https://www.scuec.edu.cn/cxcysj/cxcyhuo_do.htm",
            "创新创业教育实践基地-创新创业活动",
            args.age,
            max_pages=3,
            fetch_detail=False,
        )
        print(f"  发现 {r['discovered']} | 新增入库 {r['new']} | 跳过 {r['skipped']} | 失败 {r['failed']}")
        print(f"  入库 {r['in_db']} 条，其中带日期 {r['in_db_with_date']} 条；"
              f"超 {args.age} 天或无日期的 {r['in_db_over_age_or_nodate']} 条 ← 应等于 0")
        for t, d in r["samples"]:
            print(f"    {t} | {d}")
        if r["errors"]:
            print("  错误:", r["errors"])

        print("\n=== #7 完整 crawl 回放（临时库，fetch_detail=True，含详情页日期兜底）===")
        r2 = replay_crawl(
            "https://www.scuec.edu.cn/cxcysj/cxcyhuo_do.htm",
            "创新创业教育实践基地-创新创业活动",
            args.age,
            max_pages=1,
            fetch_detail=True,
        )
        print(f"  发现 {r2['discovered']} | 新增入库 {r2['new']} | 失败 {r2['failed']}")
        print(f"  入库 {r2['in_db']} 条，其中带日期 {r2['in_db_with_date']} 条；"
              f"超 {args.age} 天或无日期的 {r2['in_db_over_age_or_nodate']} 条 ← 应等于 0")
        for t, d in r2["samples"]:
            print(f"    {t} | {d}")
        if r2["errors"]:
            print("  错误:", r2["errors"])


if __name__ == "__main__":
    main()
