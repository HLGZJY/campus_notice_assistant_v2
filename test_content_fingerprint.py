"""模块 1.2 内容指纹变更检测的验收验证（离线、不依赖 LLM/网络）。

覆盖两个验收信号：
  1. 手工修改库中某条通知的正文并重新抓取 → 该条进入待提取状态(status=raw)，
     提取成功后的增量索引链路会更新对应 chunk。
  2. 正文未变的通知不触发任何提取/索引动作（统计 skipped、内容指纹不变）。

用法：python test_content_fingerprint.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import storage.db
from storage.db import compute_content_hash, get_connection, update_extraction
from storage.models import NoticeRecord
from crawler.base import ListPageConfig
from crawler.web_crawler import WebCrawler

TMP_DB = Path(__file__).parent / "data" / "test_fingerprint.db"
TMP_CHROMA = Path(__file__).parent / "data" / "test_fingerprint_chroma"

# 让爬虫的 get_connection() 使用临时库（函数体内引用 storage.db.DB_PATH 全局）
storage.db.DB_PATH = TMP_DB

URL = "https://example.com/notice/1001.htm"
TITLE = "示例通知"
CONTENT_V1 = (
    "通知正文第一版：关于大学生创新创业训练计划申报的通知。\n"
    "请相关同学按要求准备材料并按时提交。"
)
CONTENT_V2 = (
    "通知正文第二版：关于大学生创新创业训练计划申报的补充通知。\n"
    "申报截止时间已顺延，请相关同学留意。"
)

FAKE_LIST_HTML = f"""
<html><body>
<ul>
  <li><a href="{URL}">{TITLE}</a></li>
</ul>
</body></html>
"""

_LAST_INDEX = None  # 记录最近创建的 VectorIndex，供 cleanup 关闭


class FakeEmbeddings:
    """确定性 embedding 桩：相同文本 → 相同固定维度向量。"""

    _DIM = 8

    def embed_documents(self, texts):
        return [
            [float(abs(hash(t)) % 1000) / 1000.0] * self._DIM for t in texts
        ]

    def embed_query(self, text):
        return [float(abs(hash(text)) % 1000) / 1000.0] * self._DIM


class FingerprintCrawler(WebCrawler):
    """用可控正文替换真实详情抓取，其余流程保持原样。"""

    def __init__(self, config, content_provider):
        super().__init__(config)
        self._content_provider = content_provider
        self.fetcher.fetch = lambda url: FAKE_LIST_HTML  # 列表页用假 HTML

    def _fetch_detail(self, url, fallback_title, list_page_date=None):
        return NoticeRecord(
            url=url,
            source=self.config.source_name,
            title=fallback_title,
            raw_content=self._content_provider(),
            published_at="2026-01-01",
        )


def cleanup():
    global _LAST_INDEX
    import shutil

    # 先关闭 Chroma 持久化客户端，否则 Windows 下文件被占用无法删除
    if _LAST_INDEX is not None:
        try:
            if getattr(_LAST_INDEX, "_store", None) is not None:
                _LAST_INDEX._store._client.close()
        except Exception:
            pass
        _LAST_INDEX = None
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass  # 残留文件在下次运行时（连接打开前）清理
    try:
        if TMP_CHROMA.exists():
            shutil.rmtree(TMP_CHROMA)
    except OSError:
        pass


def run():
    cleanup()
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    print("== 0. 临时库初始化（迁移应补齐 content_hash / total_changed 列）==")
    conn = get_connection()
    notices_cols = {r[1] for r in conn.execute("PRAGMA table_info(notices)")}
    crawl_log_cols = {r[1] for r in conn.execute("PRAGMA table_info(crawl_log)")}
    check("notices.content_hash 列存在", "content_hash" in notices_cols)
    check("crawl_log.total_changed 列存在", "total_changed" in crawl_log_cols)
    conn.close()

    def make_crawler(version):
        return FingerprintCrawler(
            ListPageConfig(
                list_url="https://example.com/list.htm",
                source_name="测试来源",
                url_pattern=r"/notice/\d+\.htm",
                # 阶段 7：增量模式下重抓已入库详情页需显式开启深度检查
                deep_check=True,
            ),
            content_provider=lambda: version,
        )

    print("== 1. 首次抓取（新记录） ==")
    r1 = make_crawler(CONTENT_V1).crawl()
    check("total_new=1", r1.total_new == 1, f"new={r1.total_new}")
    check("total_changed=0", r1.total_changed == 0, f"changed={r1.total_changed}")
    conn = get_connection()
    row = conn.execute("SELECT * FROM notices WHERE url = ?", (URL,)).fetchone()
    check("status=raw", row["status"] == "raw", f"status={row['status']}")
    check(
        "content_hash 已写入且等于 compute_content_hash(V1)",
        row["content_hash"] == compute_content_hash(CONTENT_V1),
        f"hash={row['content_hash'][:12]}...",
    )
    first_crawled_at = row["crawled_at"]
    conn.close()

    print("== 2. 正文未变更 → 重新抓取应 skipped，不触发任何提取/索引动作 ==")
    r2 = make_crawler(CONTENT_V1).crawl()
    check("total_skipped=1", r2.total_skipped == 1, f"skipped={r2.total_skipped}")
    check("total_changed=0", r2.total_changed == 0, f"changed={r2.total_changed}")
    check("total_new=0", r2.total_new == 0, f"new={r2.total_new}")
    conn = get_connection()
    row = conn.execute("SELECT * FROM notices WHERE url = ?", (URL,)).fetchone()
    check("status 不变(仍 raw)", row["status"] == "raw", f"status={row['status']}")
    check(
        "content_hash 不变",
        row["content_hash"] == compute_content_hash(CONTENT_V1),
    )
    check("crawled_at 不变", row["crawled_at"] == first_crawled_at)
    conn.close()

    print("== 3. 手工修改库中该条正文（不动 content_hash）→ 重新抓取应检测变更 ==")
    conn = get_connection()
    conn.execute(
        "UPDATE notices SET raw_content = ? WHERE url = ?", (CONTENT_V2, URL)
    )
    conn.commit()
    conn.close()

    r3 = make_crawler(CONTENT_V2).crawl()
    check("total_changed=1", r3.total_changed == 1, f"changed={r3.total_changed}")
    check("total_skipped=0", r3.total_skipped == 0, f"skipped={r3.total_skipped}")
    conn = get_connection()
    row = conn.execute("SELECT * FROM notices WHERE url = ?", (URL,)).fetchone()
    check("status 已重置为 raw（进入待提取状态）", row["status"] == "raw", f"status={row['status']}")
    check("raw_content 已更新为 V2", row["raw_content"] == CONTENT_V2)
    check(
        "content_hash 已更新为 V2 指纹",
        row["content_hash"] == compute_content_hash(CONTENT_V2),
    )
    check("crawled_at 已刷新", row["crawled_at"] != first_crawled_at)
    notice_id = row["id"]
    conn.close()

    print("== 4. 提取成功后走既有增量索引链路 → 对应 chunk 被更新 ==")
    # 桩 embedding + 临时 Chroma，避免真实模型/网络依赖
    import storage.vectorstore as vs

    vs.get_embeddings = lambda: FakeEmbeddings()
    conn = get_connection()
    notice = dict(conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone())
    update_extraction(
        conn,
        notice_id,
        {
            "notice_type": "competition",
            "title": TITLE,
            "summary": "申报补充通知",
            "key_dates": [],
        },
        "extracted",
    )
    updated = dict(conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone())
    conn.close()

    index = vs.VectorIndex(persist_dir=TMP_CHROMA)
    global _LAST_INDEX
    _LAST_INDEX = index
    info = index.add_notice(updated)  # 即 extract_notice(..., auto_index=True) 的索引链路
    check("add_notice 写入 chunk", info["chunks"] > 0, f"chunks={info['chunks']}")

    data = index._get_store()._collection.get(where={"notice_id": notice_id}, include=["documents"])
    docs = data.get("documents") or []
    joined = " ".join(docs)
    check("chunk 内容已包含 V2 正文", "补充通知" in joined, f"chunks={len(docs)}")
    check("chunk 不含旧 V1 特有内容", "第一版" not in joined)

    # 再次模拟：内容再变 → remove+add 语义，旧 chunk 被替换（只余 V2 chunk）
    print("== 5. 再次变更时 add_notice 会先删旧 chunk 再写新 chunk ==")
    content_v3 = CONTENT_V2 + " 这是第三版内容。"
    conn = get_connection()
    conn.execute(
        "UPDATE notices SET raw_content = ?, content_hash = ?, status = 'extracted' WHERE id = ?",
        (content_v3, compute_content_hash(content_v3), notice_id),
    )
    conn.commit()
    updated3 = dict(conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone())
    conn.close()
    info3 = index.add_notice(updated3)
    data3 = index._get_store()._collection.get(where={"notice_id": notice_id}, include=["documents"])
    joined3 = " ".join(data3.get("documents") or [])
    check("chunk 已更新为 V3", "第三版内容" in joined3)
    check("chunk 数量无残留旧块", len(data3.get("documents") or []) == info3["chunks"])

    print("== 6. crawl_log 已记录 total_changed ==")
    conn = get_connection()
    log = conn.execute(
        "SELECT total_new, total_skipped, total_changed FROM crawl_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    check("最新一轮日志 changed=1", log["total_changed"] == 1, f"changed={log['total_changed']}")
    conn.close()

    print("== 7. 旧库迁移：无 content_hash 列 + 已有正文 → 自动回填指纹 ==")
    old_db = Path(__file__).parent / "data" / "test_old_schema.db"
    try:
        import sqlite3 as _sqlite3

        old_db.parent.mkdir(parents=True, exist_ok=True)
        if old_db.exists():
            old_db.unlink()
        c = _sqlite3.connect(str(old_db))
        c.execute(
            """CREATE TABLE notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                raw_content TEXT,
                published_at TEXT,
                crawled_at TEXT NOT NULL,
                status TEXT DEFAULT 'raw'
            )"""
        )
        c.execute(
            "INSERT INTO notices (url, source, title, raw_content, crawled_at, status) VALUES (?,?,?,?,?,?)",
            ("https://old/1.htm", "旧来源", "旧通知", CONTENT_V1, "2025-01-01T00:00:00", "extracted"),
        )
        c.commit()
        c.close()

        storage.db.DB_PATH = old_db
        conn2 = get_connection()  # 触发迁移
        row2 = conn2.execute("SELECT content_hash FROM notices WHERE url = ?", ("https://old/1.htm",)).fetchone()
        check(
            "content_hash 已回填为正文指纹",
            row2["content_hash"] == compute_content_hash(CONTENT_V1),
            f"hash={row2['content_hash'][:12]}...",
        )
        conn2.close()
    finally:
        try:
            old_db.unlink()
        except OSError:
            pass
        storage.db.DB_PATH = TMP_DB  # 恢复临时库路径

    cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()
