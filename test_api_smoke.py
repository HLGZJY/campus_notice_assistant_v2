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


def _seed_config(config_dir: Path) -> None:
    """写入最小合法配置（app.yaml + schools/scuec.yaml），供配置 API 离线测试。

    与 test_api_smoke.py 的「不碰真实数据」原则一致：配置写操作全部落在临时目录。
    """
    schools = config_dir / "schools"
    schools.mkdir(parents=True, exist_ok=True)
    (config_dir / "app.yaml").write_text(
        """active_school: scuec
models:
  extraction:
    provider: opencode-zen
    model: model-a
  qa:
    provider: opencode-zen
    model: model-a
  todo:
    provider: opencode-zen
    model: model-a
  embedding:
    provider: local
    model: emb-model
providers:
  opencode-zen:
    name: opencode-zen
    base_url: https://example.com/v1
    api_key_env: OPENCODE_API_KEY
  local:
    name: local
    base_url: ""
    api_key_env: ""
crawl:
  interval_minutes: 60
""",
        encoding="utf-8",
    )
    (schools / "scuec.yaml").write_text(
        """name: 中南民族大学
code: scuec
sources:
- name: 教务处-通知公告
  type: web
  list_url: http://example.com/tzgg.htm
  max_pages: 3
""",
        encoding="utf-8",
    )


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

    # 临时配置隔离：ConfigStore 单例指向临时目录（含最小 app.yaml + 学校数据源），
    # 后续所有配置读写走临时目录，不碰真实 config/app.yaml。
    from config.store import ConfigStore

    config_dir = Path(tmpdir.name) / "config"
    _seed_config(config_dir)
    ConfigStore.reset_instance()
    ConfigStore.get_instance(config_dir)

    # 真实配置快照：section 10 结束后断言未被修改
    real_config_path = ROOT / "config" / "app.yaml"
    real_config_snapshot = real_config_path.read_text(encoding="utf-8") if real_config_path.exists() else None

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

    print("== 7. 待办模块 ==")
    from unittest.mock import patch

    from core.models import TodoItem as CoreTodoItem
    from core.todo import TodoOutcome

    class FakeTodoGenerator:
        """替代 LLM：返回确定性待办项；generate_todos_for_notice 随后真实落库。"""

        async def generate_one(self, notice):
            return [
                CoreTodoItem(
                    action="在 2026-05-20 前完成竞赛报名",
                    due_at="2026-05-20",
                    priority="high",
                )
            ]

    with patch("core.todo.TodoGenerator", FakeTodoGenerator):
        r = client.post("/api/v1/notices/1/todos")
    check("生成待办 200", r.status_code == 200, f"status={r.status_code}")
    gen = r.json()
    check("生成 success=true", gen["success"] is True, f"{gen}")
    check("生成 items 带主键", len(gen["items"]) == 1 and "id" in gen["items"][0], f"{gen}")

    r = client.get("/api/v1/todos")
    todos = r.json()
    check("todos 列表 1 条", len(todos) == 1, f"len={len(todos)}")
    todo0 = todos[0]
    check(
        "todo 关联通知字段",
        todo0["notice_id"] == 1 and todo0["notice_title"] == "2026 数学建模竞赛报名",
        f"{todo0}",
    )
    r = client.get("/api/v1/todos", params={"status": "pending"})
    check("todos status=pending 过滤 1 条", len(r.json()) == 1, f"len={len(r.json())}")
    r = client.get("/api/v1/todos/stats")
    check("todos stats pending=1", r.json()["pending"] == 1, f"{r.json()}")
    r = client.get("/api/v1/notices/1/todos")
    check("按通知查待办 1 条", len(r.json()) == 1)
    r = client.post(f"/api/v1/todos/{todo0['id']}/status", json={"status": "done"})
    check("标记 done 200", r.status_code == 200 and r.json()["ok"] is True, f"{r.json()}")
    r = client.get("/api/v1/todos", params={"status": "done"})
    check("todos status=done 1 条", len(r.json()) == 1)
    r = client.post(f"/api/v1/todos/{todo0['id']}/status", json={"status": "bogus"})
    check("非法状态 400", r.status_code == 400, f"status={r.status_code}")
    r = client.post("/api/v1/todos/999/status", json={"status": "done"})
    check("不存在待办 404", r.status_code == 404, f"status={r.status_code}")

    print("== 8. 提醒模块 ==")
    today = datetime.now().date().isoformat()
    conn = db_mod.get_connection()
    conn.execute(
        """INSERT INTO reminders (notice_id, todo_id, due_at, tier, remind_on, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
        (2, None, "2026-05-03", "1d", today, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    r = client.get("/api/v1/reminders")
    rems = r.json()
    check("reminders 列表 1 条", len(rems) == 1, f"len={len(rems)}")
    rem = rems[0]
    check(
        "reminder 增强字段 tier_label/is_today",
        rem["tier_label"] == "⏳ 距截止 1 天" and rem["is_today"] is True,
        f"{rem}",
    )
    check("reminder 带通知标题", rem["notice_title"] == "AI 讲座", f"{rem.get('notice_title')}")
    r = client.get("/api/v1/reminders", params={"status": "pending"})
    check("reminders status=pending 1 条", len(r.json()) == 1)
    r = client.get("/api/v1/reminders", params={"limit": 1})
    check("reminders limit=1", len(r.json()) == 1)
    r = client.get("/api/v1/reminders/stats")
    check("reminder stats pending=1", r.json()["pending"] == 1, f"{r.json()}")
    r = client.get("/api/v1/reminders/pending-count")
    check("pending-count=1", r.json() == 1, f"{r.json()}")
    r = client.post(f"/api/v1/reminders/{rem['id']}/status", json={"status": "read"})
    check("标记 read 200", r.status_code == 200 and r.json()["ok"] is True, f"{r.json()}")
    r = client.post(f"/api/v1/reminders/{rem['id']}/status", json={"status": "bogus"})
    check("非法状态 400", r.status_code == 400, f"status={r.status_code}")
    r = client.post("/api/v1/reminders/999/status", json={"status": "read"})
    check("不存在提醒 404", r.status_code == 404, f"status={r.status_code}")
    r = client.get("/api/v1/reminders/pending-count")
    check("pending-count=0（已读）", r.json() == 0, f"{r.json()}")

    print("== 9. 订阅两步式 ==")
    # 第一步：preview 只读（断言不写库）
    r = client.post("/api/v1/subscriptions/preview", json={"keyword": "数学建模", "sample_limit": 5})
    check("preview 200", r.status_code == 200, f"status={r.status_code}")
    prev = r.json()
    check("preview matched=1 total=3", prev["matched"] == 1 and prev["total"] == 3, f"{prev}")
    check("preview 样例含标题", "2026 数学建模竞赛报名" in prev["samples"], f"{prev['samples']}")
    conn = db_mod.get_connection()
    subs_count = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
    match_count = conn.execute("SELECT COUNT(*) FROM notice_subscription_matches").fetchone()[0]
    conn.close()
    check("preview 后无写库", subs_count == 0 and match_count == 0, f"subs={subs_count} matches={match_count}")

    # 第二步：确认后新增（含回填）
    r = client.post("/api/v1/subscriptions", json={"keyword": "数学建模"})
    check("add 200", r.status_code == 200, f"status={r.status_code}")
    added = r.json()
    check("add ok + 回填成功", added["ok"] is True and added["backfill"]["ok"] is True, f"{added}")
    sub_id = added["id"]
    conn = db_mod.get_connection()
    match_count = conn.execute("SELECT COUNT(*) FROM notice_subscription_matches").fetchone()[0]
    conn.close()
    check("add 后命中写库 1 条", match_count == 1, f"matches={match_count}")

    r = client.get("/api/v1/subscriptions")
    subs = r.json()
    check("subscriptions 列表 1 条", len(subs) == 1, f"len={len(subs)}")
    check(
        "list 含 match_count/type_label",
        subs[0]["match_count"] == 1 and subs[0]["type_label"] == "",
        f"{subs[0]}",
    )
    r = client.get("/api/v1/subscriptions/stats")
    st = r.json()
    check("stats total/enabled/matches=1", st["total"] == 1 and st["enabled"] == 1 and st["matches"] == 1, f"{st}")

    # 浏览页只读查询
    r = client.post("/api/v1/notices/match-map", json={"notice_ids": [1, 2]})
    mm = r.json()
    check("match-map 返回 {1:[数学建模]}", mm.get("1") == ["数学建模"] and "2" not in mm, f"{mm}")
    r = client.get("/api/v1/notices/matched-ids")
    check("matched-ids=[1]", r.json() == [1], f"{r.json()}")
    r = client.get("/api/v1/notices/count")
    check("notices count=3", r.json() == 3, f"{r.json()}")

    # 全库重匹配（长耗时，同步）
    r = client.post("/api/v1/subscriptions/match-all")
    check("match-all ok", r.status_code == 200 and r.json()["ok"] is True, f"{r.json()}")

    # 编辑：_UNSET 语义（缺失字段=不改；显式 null=清空类型）
    r = client.put(f"/api/v1/subscriptions/{sub_id}", json={"notice_type": "lecture"})
    check("PUT 限定类型 200", r.status_code == 200, f"status={r.status_code}")
    r = client.get("/api/v1/subscriptions")
    check("限定 lecture 后命中 0", r.json()[0]["match_count"] == 0, f"{r.json()}")
    r = client.put(f"/api/v1/subscriptions/{sub_id}", json={"notice_type": None})
    check("PUT 清空类型 200", r.status_code == 200, f"status={r.status_code}")
    r = client.get("/api/v1/subscriptions")
    check("清空类型后命中回 1", r.json()[0]["match_count"] == 1, f"{r.json()}")

    # 启停
    r = client.post(f"/api/v1/subscriptions/{sub_id}/toggle", json={"enabled": False})
    check("toggle 停用", r.status_code == 200 and r.json()["ok"] is True, f"{r.json()}")
    r = client.get("/api/v1/subscriptions")
    check("停用后命中 0", r.json()[0]["match_count"] == 0, f"{r.json()}")
    r = client.post(f"/api/v1/subscriptions/{sub_id}/toggle", json={"enabled": True})
    check("toggle 启用", r.status_code == 200 and r.json()["ok"] is True, f"{r.json()}")
    r = client.get("/api/v1/subscriptions")
    check("启用后命中 1", r.json()[0]["match_count"] == 1, f"{r.json()}")

    # 异常路径
    r = client.put("/api/v1/subscriptions/999", json={"keyword": "x"})
    check("PUT 不存在 404", r.status_code == 404, f"status={r.status_code}")
    r = client.post("/api/v1/subscriptions/999/toggle", json={"enabled": True})
    check("toggle 不存在 404", r.status_code == 404, f"status={r.status_code}")
    r = client.delete("/api/v1/subscriptions/999")
    check("DELETE 不存在 404", r.status_code == 404, f"status={r.status_code}")
    r = client.post("/api/v1/subscriptions", json={"keyword": "   "})
    check("add 空订阅词 400", r.status_code == 400, f"status={r.status_code}")

    # 删除
    r = client.delete(f"/api/v1/subscriptions/{sub_id}")
    check("DELETE ok", r.status_code == 200 and r.json()["ok"] is True, f"{r.json()}")
    r = client.get("/api/v1/subscriptions")
    check("delete 后列表空", len(r.json()) == 0, f"len={len(r.json())}")
    r = client.get("/api/v1/subscriptions/stats")
    check("delete 后 stats total=0", r.json()["total"] == 0, f"{r.json()}")
    r = client.get("/api/v1/notices/matched-ids")
    check("delete 后 matched-ids 空", r.json() == [], f"{r.json()}")

    print("== 10. 配置模块 ==")
    # 读取
    r = client.get("/api/v1/config")
    cfg = r.json()
    check("config 200 + active_school", r.status_code == 200 and cfg["active_school"] == "scuec", f"status={r.status_code}")
    check("config 含 models/providers/crawl", {"models", "providers", "crawl"} <= set(cfg), f"{list(cfg)}")
    r = client.get("/api/v1/config/models")
    check("models 含 4 任务", set(r.json()) == {"extraction", "qa", "todo", "embedding"}, f"{r.json()}")
    r = client.get("/api/v1/config/providers")
    prov = r.json()
    check("providers 含 local/opencode-zen", set(prov) == {"local", "opencode-zen"}, f"{list(prov)}")
    check("providers 含 api_key_status", "api_key_status" in prov["local"], f"{prov['local']}")
    r = client.get("/api/v1/config/sources")
    check("sources code=scuec 1 条", r.json()["code"] == "scuec" and len(r.json()["sources"]) == 1, f"{r.json()}")
    r = client.get("/api/v1/config/disk")
    check("disk exists=true", r.status_code == 200 and r.json()["exists"] is True, f"{r.json()}")

    # PUT models → GET 验证（验收点）
    r = client.put(
        "/api/v1/config/models",
        json={
            "extraction": {"provider": "opencode-zen", "model": "model-b"},
            "qa": {"provider": "opencode-zen", "model": "model-a"},
            "todo": {"provider": "opencode-zen", "model": "model-a"},
            "embedding": {"provider": "local", "model": "emb-model"},
        },
    )
    check(
        "PUT models ok+changed",
        r.status_code == 200 and r.json()["ok"] is True and r.json()["changed"] is True,
        f"{r.json()}",
    )
    r = client.get("/api/v1/config/models")
    check("PUT models 后 GET 反映", r.json()["extraction"]["model"] == "model-b", f"{r.json()}")
    # 引用不存在的 provider → ok=false（AppConfig 交叉校验兜底）
    r = client.put(
        "/api/v1/config/models",
        json={
            "extraction": {"provider": "ghost", "model": "x"},
            "qa": {"provider": "opencode-zen", "model": "model-a"},
            "todo": {"provider": "opencode-zen", "model": "model-a"},
            "embedding": {"provider": "local", "model": "emb-model"},
        },
    )
    check("PUT models 非法 provider ok=false", r.status_code == 200 and r.json()["ok"] is False, f"{r.json()}")

    # PUT providers → GET 验证
    r = client.put(
        "/api/v1/config/providers",
        json={
            "opencode-zen": {
                "name": "opencode-zen",
                "base_url": "https://new.example.com/v1",
                "api_key_env": "OPENCODE_API_KEY",
            },
            "local": {"name": "local", "base_url": "", "api_key_env": ""},
        },
    )
    check("PUT providers ok", r.status_code == 200 and r.json()["ok"] is True, f"{r.json()}")
    r = client.get("/api/v1/config/providers")
    check(
        "PUT providers 后 GET 反映",
        r.json()["opencode-zen"]["base_url"] == "https://new.example.com/v1",
        f"{r.json()}",
    )

    # PUT sources → GET 验证
    r = client.put(
        "/api/v1/config/sources",
        json=[
            {"name": "教务处-通知公告", "type": "web", "list_url": "http://example.com/tzgg.htm", "max_pages": 3},
            {"name": "教务处-办事指南", "type": "web", "list_url": "http://example.com/bszn.htm", "max_pages": 2},
        ],
    )
    check("PUT sources ok", r.status_code == 200 and r.json()["ok"] is True, f"{r.json()}")
    r = client.get("/api/v1/config/sources")
    check("PUT sources 后 GET 2 条", len(r.json()["sources"]) == 2, f"{r.json()}")

    # .bak 生成（验收点）
    check("app.yaml.bak 生成", (config_dir / "app.yaml.bak").exists(), f"bak={config_dir / 'app.yaml.bak'}")
    check("scuec.yaml.bak 生成", (config_dir / "schools" / "scuec.yaml.bak").exists())

    # reload（version：PUT models=1 + PUT providers=2 + reload=3）
    r = client.post("/api/v1/config/reload")
    check("reload ok + version=3", r.status_code == 200 and r.json()["ok"] is True and r.json()["version"] == 3, f"{r.json()}")

    # 离线失败路径
    r = client.post("/api/v1/config/test-source", json={"url": ""})
    check("test-source 空 URL ok=false", r.status_code == 200 and r.json()["ok"] is False, f"{r.json()}")
    r = client.post("/api/v1/config/test-model", json={"provider": "ghost", "model": "x"})
    check("test-model 未知 provider ok=false", r.status_code == 200 and r.json()["ok"] is False, f"{r.json()}")

    # 422：schema 校验失败（body 缺 qa/todo/embedding 任务）
    r = client.put("/api/v1/config/models", json={"extraction": {"provider": "opencode-zen", "model": "x"}})
    check("PUT models 缺任务 422", r.status_code == 422, f"status={r.status_code}")

    # 真实 config/app.yaml 未被修改
    now_snapshot = real_config_path.read_text(encoding="utf-8") if real_config_path.exists() else None
    check("真实 app.yaml 未修改", now_snapshot == real_config_snapshot)

    tmpdir.cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    main()
