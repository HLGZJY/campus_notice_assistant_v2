"""阶段 7 增量抓取 + 提取预筛的验收验证（离线，不依赖真实 LLM/网络）。

覆盖验收信号：
  1. 增量早停：翻页遇到"整页均已入库"立即停止，不再抓后续页；
  2. max_age_days 时效过滤：列表页过期通知不收录；
  3. deep_check=False 时已入库通知不重抓详情页（详情抓取次数受控）；
  4. 提取预筛：正文过短 → 不调 LLM、落 extract_skipped_reason、raw 游标排除；
  5. prefilter=False 绕过预筛（手动/测试路径）。

用法：python test_incremental_crawl.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import storage.db
from storage.db import get_connection, get_notices_by_status, insert_notice
from storage.models import NoticeRecord
from crawler.base import ListPageConfig
from crawler.web_crawler import WebCrawler

TMP_DB = Path(__file__).parent / "data" / "test_incremental.db"
storage.db.DB_PATH = TMP_DB


def cleanup():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


class FakeCrawler(WebCrawler):
    """可控详情正文 + 统计列表页/详情页抓取次数。"""

    def __init__(self, config, pages: dict[str, str]):
        super().__init__(config)
        self._pages = pages
        self.list_fetches: list[str] = []
        self.detail_fetches: list[str] = []
        self.fetcher.fetch = self._fake_fetch

    def _fake_fetch(self, url: str) -> str:
        self.list_fetches.append(url)
        return self._pages.get(url, "<html><body></body></html>")

    def _fetch_detail(self, url, fallback_title, list_page_date=None):
        self.detail_fetches.append(url)
        return NoticeRecord(
            url=url,
            source=self.config.source_name,
            title=fallback_title,
            raw_content=f"通知正文：{fallback_title}。这是足够长的正文内容，用于通过最短长度检查。",
            published_at=list_page_date,
        )


def make_page(links: list[tuple[str, str]]) -> str:
    """links: [(url, title)]"""
    lis = "".join(f'<li><a href="{u}">{t}</a></li>' for u, t in links)
    return f"<html><body><ul>{lis}</ul></body></html>"


def run():
    cleanup()
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    print("== 1. 增量早停：第 2 页整页已入库 → 不再抓第 3 页 ==")
    p1 = make_page([(f"https://x.example/n/{i}.htm", f"通知{i}") for i in range(1, 3)])
    p2 = make_page([(f"https://x.example/n/{i}.htm", f"通知{i}") for i in range(1, 3)])
    p3 = make_page([(f"https://x.example/n/{i}.htm", f"通知{i}") for i in range(3, 5)])
    crawler = FakeCrawler(
        ListPageConfig(
            list_url="https://x.example/list.htm",
            source_name="测试来源",
            url_pattern=r"/n/\d+\.htm",
            max_pages=5,
        ),
        pages={
            "https://x.example/list.htm": p1,
            "https://x.example/list.htm?page=2": p2,
            "https://x.example/list.htm?page=3": p3,
        },
    )
    r = crawler.crawl()
    check("首轮新增 2 条", r.total_new == 2, f"new={r.total_new}")
    check("详情页抓取 2 次（仅新增）", len(crawler.detail_fetches) == 2, f"fetches={crawler.detail_fetches}")

    r2 = crawler.crawl()
    check("第二轮零新增", r2.total_new == 0, f"new={r2.total_new}")
    check("第二轮早停：只抓了 2 个列表页（第 3 页未访问）", len(crawler.list_fetches) == 2, f"fetches={crawler.list_fetches}")
    check("第二轮零详情抓取（增量不重抓）", len(crawler.detail_fetches) == 2, f"fetches={crawler.detail_fetches}")

    print("== 2. max_age_days 时效过滤 ==")
    conn = get_connection()
    today = datetime.now().strftime("%Y-%m-%d")
    old_day = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    conn.close()

    # 用 <dt> + <span class="date">（教务处列表格式），解析器可识别日期
    lis = (
        f'<dt><span class="date">{old_day}</span><a href="https://y.example/n/old.htm">旧通知</a></dt>'
        f'<dt><span class="date">{today}</span><a href="https://y.example/n/new.htm">新通知</a></dt>'
    )
    p_dated = f"<html><body><dl>{lis}</dl></body></html>"
    crawler2 = FakeCrawler(
        ListPageConfig(
            list_url="https://y.example/list.htm",
            source_name="测试来源",
            url_pattern=r"/n/",
            max_pages=2,
            max_age_days=30,
        ),
        pages={"https://y.example/list.htm": p_dated},
    )
    r3 = crawler2.crawl()
    check("30 天窗口内仅收录 1 条", r3.total_new == 1, f"new={r3.total_new}, discovered={r3.total_discovered}")

    print("== 3. deep_check=False：已入库详情不重抓（指纹测试已覆盖 deep_check=True 变更检测） ==")
    crawler3 = FakeCrawler(
        ListPageConfig(
            list_url="https://z.example/list.htm",
            source_name="测试来源",
            url_pattern=r"/n/",
            max_pages=2,
        ),
        pages={"https://z.example/list.htm": p1},
    )
    crawler3.crawl()
    crawler3.detail_fetches.clear()
    crawler3.crawl()
    check("再抓一轮详情抓取仍为 0", len(crawler3.detail_fetches) == 0, f"fetches={crawler3.detail_fetches}")

    print("== 4. 预筛时效按发布时间（P0-1：max_age_days 语义修复） ==")
    from config.schema import ExtractConfig
    from services.notice_service import prefilter_notice

    cfg17 = ExtractConfig(max_age_days=17, min_content_length=1)
    old_pub = {
        "title": "旧通知",
        "raw_content": "长正文" * 20,
        "published_at": (datetime.now() - timedelta(days=21)).isoformat(),
        "crawled_at": datetime.now().isoformat(),
    }
    new_pub = {
        "title": "新通知",
        "raw_content": "长正文" * 20,
        "published_at": (datetime.now() - timedelta(days=10)).isoformat(),
        "crawled_at": datetime.now().isoformat(),
    }
    ok_old, reason_old = prefilter_notice(old_pub, cfg17)
    ok_new, reason_new = prefilter_notice(new_pub, cfg17)
    check("21 天前发布被拦截（即使当天抓取）", not ok_old and "发布时间" in reason_old, f"reason={reason_old}")
    check("10 天前发布通过", ok_new, f"reason={reason_new}")
    no_pub = {
        "title": "无日期",
        "raw_content": "长正文" * 20,
        "published_at": None,
        "crawled_at": (datetime.now() - timedelta(days=30)).isoformat(),
    }
    ok_np, reason_np = prefilter_notice(no_pub, cfg17)
    check("无发布时间回退抓取时间并拦截", not ok_np and "抓取时间" in reason_np, f"reason={reason_np}")

    print("== 5. 提取预筛：正文过短 → 跳过并落 extract_skipped_reason ==")
    from config.store import ConfigStore
    from services.notice_service import extract_batch, prefilter_notice

    conn = get_connection()
    conn.execute("DELETE FROM notices")
    short = NoticeRecord(
        url="https://s.example/1.htm", source="测试来源", title="空页面", raw_content="标题 正文内容"
    )
    long = NoticeRecord(
        url="https://s.example/2.htm",
        source="测试来源",
        title="长通知",
        raw_content="这是一段足够长的通知正文。" * 30,
    )
    insert_notice(conn, short)
    insert_notice(conn, long)
    conn.close()

    cfg = ExtractConfig()  # 默认宽松配置（不依赖用户持久化的真实配置）
    ok_s, reason_s = prefilter_notice({"raw_content": short.raw_content, "title": short.title, "published_at": None}, cfg)
    ok_l, reason_l = prefilter_notice({"raw_content": long.raw_content, "title": long.title, "published_at": None}, cfg)
    check("短正文被预筛拦截", not ok_s and "正文过短" in reason_s, f"reason={reason_s}")
    check("长正文通过预筛", ok_l and reason_l is None, f"reason={reason_l}")

    class FakeExtractor:
        def __init__(self):
            from core.models import NoticeExtraction

            self.extraction = NoticeExtraction(
                notice_type="competition",
                title="提取标题",
                summary="提取摘要",
                key_dates=[],
            )

        async def extract_one(self, title, content, published_at=None, crawled_at=None, notice_id=None):
            from core.extractor import ExtractionOutcome

            return ExtractionOutcome(status="extracted", extraction=self.extraction)

    res = extract_batch(
        limit=50,
        auto_index=False,
        extractor=FakeExtractor(),
        extract_cfg=ExtractConfig(min_content_length=100),
    )
    check("预筛后只处理 1 条", res["processed"] == 1, f"processed={res['processed']}")
    check("prefiltered 计数=1", res["prefiltered"] == 1, f"prefiltered={res['prefiltered']}")

    conn = get_connection()
    row = conn.execute("SELECT * FROM notices WHERE url = ?", ("https://s.example/1.htm",)).fetchone()
    check("跳过项落 extract_skipped_reason", row["extract_skipped_reason"] is not None, f"reason={row['extract_skipped_reason']}")
    check("跳过项状态仍为 raw", row["status"] == "raw", f"status={row['status']}")
    raw_all = get_notices_by_status(conn, "raw", limit=50)
    check("raw 游标仍可见跳过项（默认不过滤）", len(raw_all) == 1, f"n={len(raw_all)}")
    raw_excl = get_notices_by_status(conn, "raw", limit=50, exclude_prefiltered=True)
    check("exclude_prefiltered=True 排除跳过项", raw_excl == [], f"n={len(raw_excl)}")
    row2 = conn.execute("SELECT * FROM notices WHERE url = ?", ("https://s.example/2.htm",)).fetchone()
    check("通过项已提取", row2["status"] == "extracted", f"status={row2['status']}")
    conn.close()

    print("== 6. prefilter=False 绕过预筛 ==")
    conn = get_connection()
    conn.execute("DELETE FROM notices")
    insert_notice(conn, short)
    conn.close()
    res2 = extract_batch(limit=50, auto_index=False, extractor=FakeExtractor(), prefilter=False)
    check("关闭预筛后处理 1 条", res2["processed"] == 1, f"processed={res2['processed']}")
    conn = get_connection()
    row3 = conn.execute("SELECT * FROM notices WHERE url = ?", ("https://s.example/1.htm",)).fetchone()
    check("绕过预筛后正常提取", row3["status"] == "extracted", f"status={row3['status']}")
    conn.close()

    print("== 7. 分页/列表页 URL 排除（P0-2：分页页不再被当通知） ==")
    pg_list = (
        "<html><body><ul>"
        '<li><a href="https://p.example/n/1.htm">通知一</a></li>'
        '<li><a href="https://p.example/n/2.htm">通知二</a></li>'
        '<li><a href="https://p.example/n/99.htm">2</a></li>'
        '<li><a href="https://p.example/n/98.htm">下一页</a></li>'
        '<li><a href="https://p.example/list.htm">首页</a></li>'
        "</ul></body></html>"
    )
    crawler_p = FakeCrawler(
        ListPageConfig(
            list_url="https://p.example/list.htm",
            source_name="测试来源",
            url_pattern=r"/n/\d+\.htm",
            max_pages=2,
        ),
        pages={"https://p.example/list.htm": pg_list},
    )
    rp = crawler_p.crawl()
    check("分页页码 99.htm/98.htm 未被收录", rp.total_new == 2, f"new={rp.total_new}")
    conn = get_connection()
    row_99 = conn.execute("SELECT id FROM notices WHERE url = ?", ("https://p.example/n/99.htm",)).fetchone()
    check("分页 URL 未入库", row_99 is None, f"row={row_99}")
    conn.close()

    print("== 8. skip_llm：不调 LLM，仅索引 + partial ==")
    conn = get_connection()
    conn.execute("DELETE FROM notices")
    insert_notice(conn, long)
    conn.close()
    calls = {"n": 0}

    class CountingExtractor(FakeExtractor):
        async def extract_one(self, **kwargs):
            calls["n"] += 1
            return await super().extract_one(**kwargs)

    res_skip = extract_batch(
        limit=50,
        auto_index=False,
        extractor=CountingExtractor(),
        extract_cfg=ExtractConfig(min_content_length=100, skip_llm=True),
    )
    check("skip_llm 不调 LLM", calls["n"] == 0, f"calls={calls['n']}")
    conn = get_connection()
    row_skip = conn.execute("SELECT * FROM notices WHERE url = ?", ("https://s.example/2.htm",)).fetchone()
    check("skip_llm 状态置 partial", row_skip["status"] == "partial", f"status={row_skip['status']}")
    check("skip_llm 未写结构化字段", row_skip["notice_type"] is None, f"type={row_skip['notice_type']}")
    conn.close()

    print("== 9. crawl_all_sources 来源过滤（P0-2 修复：勾选只抓选中来源） ==")
    from config.schema import SchoolConfig, SourceConfig
    import services.notice_service as ns

    fake_cfg = SchoolConfig(
        name="测试校",
        code="test",
        sources=[
            SourceConfig(name="源A", list_url="https://a.example/list.htm"),
            SourceConfig(name="源B", list_url="https://b.example/list.htm"),
            SourceConfig(name="源C", list_url="https://c.example/list.htm", enabled=False),
        ],
    )
    called: list[str] = []
    real_get = ns.get_school_config
    real_crawl = ns.crawl_source

    def fake_get_school_config():
        return fake_cfg

    def fake_crawl_source(source, **kwargs):
        called.append(source.name)
        return {"source": source.name, "new": 1}

    ns.get_school_config = fake_get_school_config
    ns.crawl_source = fake_crawl_source
    try:
        ns.crawl_all_sources()
        check("不选来源 = 全部启用来源（源A+源B）", called == ["源A", "源B"], f"called={called}")
        called.clear()
        ns.crawl_all_sources(sources=["源A"])
        check("勾选源A 只抓源A", called == ["源A"], f"called={called}")
        called.clear()
        ns.crawl_all_sources(sources=["源A", "源C"])
        check("勾选含停用源C 时仍只抓源A", called == ["源A"], f"called={called}")
    finally:
        ns.get_school_config = real_get
        ns.crawl_source = real_crawl

    cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()