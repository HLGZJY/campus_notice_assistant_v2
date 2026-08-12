"""API 冒烟测试（离线，TestClient 临时库隔离）。

覆盖：/health、/notices 列表/详情/统计/来源/类型、404、鉴权占位。
使用临时数据库 + 种子数据，不碰 data/notices.db。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# 项目根（与引擎脚本保持一致）
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

os.environ["APP_ENV"] = "test"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def _seed_notice(
    conn,
    url: str,
    title: str,
    *,
    source: str = "教务处",
    raw_content: str = "",
    published_at: str | None = None,
    status: str = "extracted",
    notice_type: str | None = None,
    deadline: str | None = None,
    summary: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO notices
           (url, source, title, raw_content, published_at, crawled_at, status, notice_type, deadline, summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            url,
            source,
            title,
            raw_content,
            published_at,
            datetime.now().isoformat(),
            status,
            notice_type,
            deadline,
            summary,
        ),
    )
    conn.commit()


def main() -> None:
    from fastapi.testclient import TestClient

    from api.main import create_app

    # 临时库隔离：覆盖 storage.db.DB_PATH（get_connection() 内部读模块级 DB_PATH，services 全链路由此生效）
    tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db_path = Path(tmpdir.name) / "test_api.db"

    from storage import db as db_mod

    db_mod.DB_PATH = db_path

    # 种子：2 条通知（1 竞赛 / 1 讲座），1 条 raw
    conn = db_mod.get_connection()
    _seed_notice(
        conn,
        url="http://t1",
        source="教务处",
        title="2026 数学建模竞赛报名",
        raw_content="报名截止 5 月 20 日",
        published_at="2026-05-01",
        status="extracted",
        notice_type="competition",
        deadline="2026-05-20",
        summary="校级竞赛",
    )
    _seed_notice(
        conn,
        url="http://t2",
        source="创新创业学院",
        title="AI 讲座",
        raw_content="周五下午",
        published_at="2026-05-02",
        status="extracted",
        notice_type="lecture",
    )
    _seed_notice(conn, url="http://t3", source="教务处", title="待提取公告", raw_content="...", status="raw")
    conn.close()

    client = TestClient(create_app())

    print("== 1. 健康检查 ==")
    r = client.get("/api/v1/health")
    check("health 200", r.status_code == 200, f"status={r.status_code}")
    body = r.json()
    check("health 字段齐全", body["status"] == "ok" and body["db"] == "ok")
    check("health notices=3", body["notices"] == 3, f"notices={body['notices']}")

    print("== 2. 通知列表 ==")
    r = client.get("/api/v1/notices")
    check("notices 200", r.status_code == 200, f"status={r.status_code}")
    items = r.json()
    check("notices 返回 3 条", len(items) == 3, f"len={len(items)}")
    check("列表项为摘要模型（无 raw_content）", all("raw_content" not in it for it in items))
    check(
        "列表含 competition 类型",
        any(it["notice_type"] == "competition" for it in items),
        f"{[it.get('notice_type') for it in items]}",
    )

    print("== 3. 过滤 ==")
    r = client.get("/api/v1/notices", params={"source": "教务处"})
    check("按 source 过滤 2 条", len(r.json()) == 2, f"len={len(r.json())}")
    r = client.get("/api/v1/notices", params={"status": "raw"})
    check("按 status=raw 过滤 1 条", len(r.json()) == 1)
    r = client.get("/api/v1/notices", params={"notice_type": "competition"})
    check("按 notice_type 过滤 1 条", len(r.json()) == 1)
    r = client.get("/api/v1/notices", params={"keyword": "数学建模"})
    check("按 keyword 过滤 1 条", len(r.json()) == 1)
    r = client.get("/api/v1/notices", params={"is_action": True})
    check("is_action=true 含行动型", all(it["notice_type"] in ("competition", "lecture") for it in r.json()))
    r = client.get("/api/v1/notices", params={"limit": 1})
    check("limit=1 返回 1 条", len(r.json()) == 1)

    print("== 4. 统计 / 来源 / 类型 ==")
    r = client.get("/api/v1/notices/status-counts")
    counts = r.json()
    check("status-counts extracted=2", counts["extracted"] == 2, f"{counts}")
    check("status-counts raw=1", counts["raw"] == 1)
    r = client.get("/api/v1/notices/sources")
    check("sources 含 2 个", set(r.json()) == {"教务处", "创新创业学院"}, f"{r.json()}")
    r = client.get("/api/v1/notices/types")
    check("types 含 competition/lecture", "competition" in r.json() and "lecture" in r.json(), f"{r.json()}")

    print("== 5. 详情 / 404 ==")
    r = client.get("/api/v1/notices/1")
    check("detail 200", r.status_code == 200)
    d = r.json()
    check("detail 含 key_dates", isinstance(d.get("key_dates"), list))
    check("detail 含 raw_content", "raw_content" in d)
    r = client.get("/api/v1/notices/999")
    check("detail 999 → 404", r.status_code == 404, f"status={r.status_code}")

    print("== 6. 未知路由 → 404 ==")
    r = client.get("/api/v1/nonexistent")
    check("未知路由 404", r.status_code == 404)

    tmpdir.cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    main()
