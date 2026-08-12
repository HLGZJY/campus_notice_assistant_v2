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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

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
scheduler:
  enabled: false # 测试不拉起调度器（离线、不碰网络/真实库）
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

    # 阶段 4：TestClient 上下文管理器触发 lifespan（异步任务管理器 worker 后台运行）。
    # 长耗时写操作改为「提交任务 → 轮询 GET /tasks/{id}」；主流程在 _smoke 中保持原缩进。
    with TestClient(create_app()) as client:
        _smoke(client, db_mod, config_dir, tmpdir, real_config_path, real_config_snapshot)

    tmpdir.cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


def poll_task(client, task_id: int, timeout: float = 30.0) -> dict | None:
    """轮询 GET /tasks/{id} 直到 success/failed，超时返回 None（阶段 4 提交→轮询链路）。"""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/v1/tasks/{task_id}")
        if r.status_code == 200:
            rec = r.json()
            if rec["status"] in ("success", "failed"):
                return rec
        time.sleep(0.05)
    return None


def _smoke(client, db_mod, config_dir, tmpdir, real_config_path, real_config_snapshot) -> None:
    """冒烟主流程：阶段 4 起在 TestClient 上下文内运行（worker 后台已就绪）。"""
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

    # 阶段 4：提交生成任务 → 轮询完成。patch 须覆盖 worker 线程执行期间，故轮询也在 with 内。
    with patch("core.todo.TodoGenerator", FakeTodoGenerator):
        r = client.post("/api/v1/notices/1/todos")
        check("提交生成待办任务 202", r.status_code == 202, f"status={r.status_code}")
        gen_task = r.json()
        check(
            "任务返回 queued + generate_todos",
            gen_task["status"] == "queued" and gen_task["type"] == "generate_todos",
            f"{gen_task}",
        )
        gen = poll_task(client, gen_task["task_id"])
        check("生成待办任务 success", gen is not None and gen["status"] == "success", f"{gen}")
        check(
            "生成 success=true",
            gen is not None and gen["result"]["success"] is True,
            f"{gen}",
        )
        check(
            "生成 items 带主键",
            gen is not None and len(gen["result"]["items"]) == 1 and "id" in gen["result"]["items"][0],
            f"{gen}",
        )

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

    # 第二步：确认后新增（异步任务 → 轮询完成 → 断言回填落库）
    r = client.post("/api/v1/subscriptions", json={"keyword": "数学建模"})
    check("add 202", r.status_code == 202, f"status={r.status_code}")
    add_task = r.json()
    check(
        "add 任务 queued + subscription_add",
        add_task["status"] == "queued" and add_task["type"] == "subscription_add",
        f"{add_task}",
    )
    added = poll_task(client, add_task["task_id"])
    check(
        "add 任务 success + 回填 ok",
        added is not None
        and added["status"] == "success"
        and added["result"]["ok"] is True
        and added["result"]["backfill"]["ok"] is True,
        f"{added}",
    )
    sub_id = added["result"]["id"]
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

    # 全库重匹配（异步任务 → 轮询完成）
    r = client.post("/api/v1/subscriptions/match-all")
    check("match-all 202", r.status_code == 202, f"status={r.status_code}")
    ma_task = r.json()
    check("match-all 任务 queued", ma_task["status"] == "queued" and ma_task["type"] == "match_all", f"{ma_task}")
    ma = poll_task(client, ma_task["task_id"])
    check("match-all 任务 success + ok", ma is not None and ma["status"] == "success" and ma["result"]["ok"] is True, f"{ma}")

    # 编辑：_UNSET 语义（缺失字段=不改；显式 null=清空类型），异步任务 + 轮询
    r = client.put(f"/api/v1/subscriptions/{sub_id}", json={"notice_type": "lecture"})
    check("PUT 限定类型 202", r.status_code == 202, f"status={r.status_code}")
    up = poll_task(client, r.json()["task_id"])
    check("PUT 限定类型任务 success", up is not None and up["status"] == "success", f"{up}")
    r = client.get("/api/v1/subscriptions")
    check("限定 lecture 后命中 0", r.json()[0]["match_count"] == 0, f"{r.json()}")
    r = client.put(f"/api/v1/subscriptions/{sub_id}", json={"notice_type": None})
    check("PUT 清空类型 202", r.status_code == 202, f"status={r.status_code}")
    up = poll_task(client, r.json()["task_id"])
    check("PUT 清空类型任务 success", up is not None and up["status"] == "success", f"{up}")
    r = client.get("/api/v1/subscriptions")
    check("清空类型后命中回 1", r.json()[0]["match_count"] == 1, f"{r.json()}")

    # 启停（异步任务 + 轮询）
    r = client.post(f"/api/v1/subscriptions/{sub_id}/toggle", json={"enabled": False})
    check("toggle 停用 202", r.status_code == 202, f"status={r.status_code}")
    tg = poll_task(client, r.json()["task_id"])
    check("toggle 停用任务 success", tg is not None and tg["status"] == "success", f"{tg}")
    r = client.get("/api/v1/subscriptions")
    check("停用后命中 0", r.json()[0]["match_count"] == 0, f"{r.json()}")
    r = client.post(f"/api/v1/subscriptions/{sub_id}/toggle", json={"enabled": True})
    check("toggle 启用 202", r.status_code == 202, f"status={r.status_code}")
    tg = poll_task(client, r.json()["task_id"])
    check("toggle 启用任务 success", tg is not None and tg["status"] == "success", f"{tg}")
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

    print("== 11. 通用异步任务链路 ==")
    # 提交 match_all（离线纯规则）→ 轮询 success → 断言 result 形状
    r = client.post("/api/v1/tasks", json={"type": "match_all"})
    check("POST /tasks 202 + queued", r.status_code == 202 and r.json()["status"] == "queued", f"{r.json()}")
    tid = r.json()["task_id"]
    rec = poll_task(client, tid)
    check("match_all 任务 success", rec is not None and rec["status"] == "success", f"{rec}")
    check(
        "match_all result 形状",
        rec is not None
        and rec["result"]["ok"] is True
        and {"notices", "matched_notices", "total_matches"} <= set(rec["result"]),
        f"{rec}",
    )
    # 任务列表 / 详情
    r = client.get("/api/v1/tasks")
    check("tasks 列表非空", r.status_code == 200 and any(t["id"] == tid for t in r.json()), f"{r.json()}")
    r = client.get(f"/api/v1/tasks/{tid}")
    check("tasks/{id} 详情 200", r.status_code == 200 and r.json()["id"] == tid, f"status={r.status_code}")
    # 未知 type → 400；不存在 → 404
    r = client.post("/api/v1/tasks", json={"type": "nonexistent"})
    check("未知 type 400", r.status_code == 400, f"status={r.status_code}")
    r = client.get("/api/v1/tasks/999999")
    check("tasks 不存在 404", r.status_code == 404, f"status={r.status_code}")

    print("== 12. 问答 SSE 流式（离线 mock，复用 test_demo.py 确定性语义） ==")
    import asyncio
    import json

    from core.qa import QAResult, QAAgent, SourceRef

    # ---- 12.1 主链路：delta → done（QAResult 路由层 as_source 序列化） ----
    async def _fake_stream(question):
        yield ("delta", "你好，")
        yield ("delta", "2026 数学建模竞赛报名截止 5 月 20 日。")
        yield (
            "done",
            QAResult(
                answer="你好，2026 数学建模竞赛报名截止 5 月 20 日。",
                sources=[
                    SourceRef(
                        notice_id=1,
                        title="2026 数学建模竞赛报名",
                        url="http://t1",
                        notice_type="competition",
                        deadline="2026-05-20",
                    )
                ],
                retrieved_chunks=2,
            ),
        )

    with patch("core.qa.QAAgent") as FakeAgent:
        FakeAgent.return_value.ask_stream = _fake_stream
        with client.stream(
            "GET", "/api/v1/qa/ask/stream", params={"question": "最近有哪些比赛？"}
        ) as r:
            check(
                "stream 200 + text/event-stream",
                r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"),
                f"status={r.status_code} ctype={r.headers.get('content-type')}",
            )
            events = [
                json.loads(line[6:])
                for line in r.iter_lines()
                if line.startswith("data: ")
            ]
    types = [e["type"] for e in events]
    check("事件序列 delta,delta,done", types == ["delta", "delta", "done"], f"{types}")
    check(
        "delta 顺序拼接即最终答案",
        events[0]["content"] + events[1]["content"] == "你好，2026 数学建模竞赛报名截止 5 月 20 日。",
        f"{events[0]} {events[1]}",
    )
    done = events[-1]
    check("done 携带完整 answer", done["answer"] == "你好，2026 数学建模竞赛报名截止 5 月 20 日。", f"{done}")
    check(
        "done.sources 为 as_source 纯 dict（§5.7 例外在路由层生效）",
        done["sources"]
        == [
            {
                "notice_id": 1,
                "title": "2026 数学建模竞赛报名",
                "url": "http://t1",
                "notice_type": "competition",
                "deadline": "2026-05-20",
            }
        ],
        f"{done['sources']}",
    )
    check("done.retrieved_chunks=2", done["retrieved_chunks"] == 2, f"{done['retrieved_chunks']}")

    # ---- 12.2 错误路径：流中途抛异常 → 末事件 error ----
    async def _fail_stream(question):
        yield ("delta", "部分输出")
        raise RuntimeError("模拟 LLM 故障")

    with patch("core.qa.QAAgent") as FakeAgent:
        FakeAgent.return_value.ask_stream = _fail_stream
        with client.stream(
            "GET", "/api/v1/qa/ask/stream", params={"question": "会失败的问题"}
        ) as r:
            err_events = [
                json.loads(line[6:])
                for line in r.iter_lines()
                if line.startswith("data: ")
            ]
    check(
        "错误路径末事件为 error 且含异常名",
        err_events[-1]["type"] == "error" and "RuntimeError" in err_events[-1]["message"],
        f"{err_events[-1]}",
    )

    # ---- 12.3 空 question → 422 ----
    r = client.get("/api/v1/qa/ask/stream", params={"question": ""})
    check("空 question 422", r.status_code == 422, f"status={r.status_code}")

    # ---- 12.4 core 空检索路径：真实 QAAgent + FakeIndex，离线不碰 LLM ----
    class FakeEmptyIndex:
        def search(self, query: str, k: int = 6, **kwargs):
            return []

    async def _collect(agen):
        return [item async for item in agen]

    items = asyncio.run(_collect(QAAgent(index=FakeEmptyIndex()).ask_stream("测试问题")))
    check("空索引仅产 done", len(items) == 1 and items[0][0] == "done", f"{items}")
    check(
        "空索引兜底回答正确",
        items[0][1].answer == "根据已抓取的通知，没有找到相关信息。"
        and items[0][1].retrieved_chunks == 0,
        f"{items[0][1]}",
    )

    # ---- 12.5 index-stats（临时库下 chunks=0 + error 信息，宽松断言） ----
    r = client.get("/api/v1/qa/index-stats")
    st = r.json()
    check("index-stats 200 + 字段", r.status_code == 200 and {"chunks", "persist_dir"} <= set(st), f"{st}")

    print("== 13. 调度器状态（test 模式不拉起，app.state.scheduler=None） ==")
    r = client.get("/api/v1/scheduler/status")
    check("scheduler/status 200", r.status_code == 200, f"status={r.status_code}")
    s = r.json()
    check("status enabled=false（测试不拉起调度器）", s["enabled"] is False, f"{s}")
    check("status running=false + jobs 空", s["running"] is False and s["jobs"] == [], f"{s}")
    check("status recent_runs 为 list", isinstance(s["recent_runs"], list), f"{s}")
    conn = db_mod.get_connection()
    sched_rows = conn.execute("SELECT COUNT(*) FROM scheduler_log").fetchone()[0]
    conn.close()
    check("测试全程 scheduler_log 无写入（调度器确未启动）", sched_rows == 0, f"rows={sched_rows}")

    # 真实 config/app.yaml 未被修改
    now_snapshot = real_config_path.read_text(encoding="utf-8") if real_config_path.exists() else None
    check("真实 app.yaml 未修改", now_snapshot == real_config_snapshot)


if __name__ == "__main__":
    main()
