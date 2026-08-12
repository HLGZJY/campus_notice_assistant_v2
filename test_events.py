"""W4 模块 4.1 埋点的验收验证（离线，不依赖 LLM/网络）。

覆盖验收信号：
  1. events 事件表由 SCHEMA 自动创建，列齐全；
  2. track_event 正常写入（事件类型 / 关联对象 id / 备注 / 时间）；
  3. 埋点不阻塞主流程：写入失败时 track_event 不抛异常、返回 False；
  4. 查询接口：count_events_by_type / get_event_stats / get_recent_events；
  5. 五类事件（页面访问 / 问答 / 待办生成 / 待办完成 / 服务按钮点击）均可写入；
  6. 服务市场页（假按钮）AppTest 冒烟：页面可加载、点「下单」后事件表出现
     service_button_click（门控 #1 数据源）。

用法：python test_events.py
"""
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import storage.db
from storage.db import (
    count_events_by_type,
    get_connection,
    get_event_stats,
    get_recent_events,
    insert_event,
)
from services import tracking_service
from services.tracking_service import (
    EVENT_PAGE_VIEW,
    EVENT_QA_ASK,
    EVENT_SERVICE_CLICK,
    EVENT_TODO_DONE,
    EVENT_TODO_GENERATE,
    count_events,
    get_event_stats as svc_get_event_stats,
    get_recent_events as svc_get_recent_events,
    track_event,
)

TMP_DB = Path(__file__).parent / "data" / "test_events.db"

storage.db.DB_PATH = TMP_DB


def reset_db():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


def event_rows(conn, event_type=None):
    if event_type is None:
        return conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM events WHERE event_type = ? ORDER BY id", (event_type,)
    ).fetchall()


def run():
    reset_db()
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    print("== 0. 表结构由 SCHEMA 自动创建 ==")
    conn = get_connection()
    e_cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    check(
        "events 列齐全",
        {"id", "event_type", "ref_id", "note", "event_at"} <= e_cols,
        f"cols={sorted(e_cols)}",
    )
    conn.close()

    # ---------- Part A：track_event 正常写入 ----------
    print("\n== A. track_event 正常写入 ==")
    reset_db()
    check(
        "track_event 返回 True",
        track_event(EVENT_PAGE_VIEW, note="app.py:首页"),
    )
    track_event(EVENT_TODO_GENERATE, ref_id=42, note="某通知标题")
    conn = get_connection()
    rows = event_rows(conn)
    check("共写入 2 条", len(rows) == 2, f"n={len(rows)}")
    pv = rows[0]
    check("事件类型正确", pv["event_type"] == EVENT_PAGE_VIEW, f"type={pv['event_type']}")
    check("备注正确", pv["note"] == "app.py:首页", f"note={pv['note']}")
    check("event_at 已写入", bool(pv["event_at"]), f"event_at={pv['event_at']}")
    todo_evt = rows[1]
    check("ref_id 正确", todo_evt["ref_id"] == 42, f"ref_id={todo_evt['ref_id']}")
    check("空 ref_id 允许（None）", rows[0]["ref_id"] is None)
    conn.close()

    # ---------- Part B：五类事件均可写入 ----------
    print("\n== B. 五类事件常量齐全且可写入 ==")
    reset_db()
    ok = True
    for evt_type in tracking_service.ALL_EVENT_TYPES:
        ok = ok and track_event(evt_type)
    check("五类事件各写 1 条成功", ok)
    conn = get_connection()
    check("事件表共 5 条", len(event_rows(conn)) == 5)
    conn.close()

    # ---------- Part C：不阻塞主流程（写入失败不抛异常） ----------
    print("\n== C. 埋点失败不阻塞主流程 ==")
    orig = tracking_service.get_connection

    def boom(*args, **kwargs):
        raise RuntimeError("模拟数据库不可用")

    tracking_service.get_connection = boom
    try:
        result = track_event(EVENT_QA_ASK, note="即使失败也不上抛")
        check("track_event 失败时返回 False 且不抛异常", result is False)
    finally:
        tracking_service.get_connection = orig
    check("主流程可继续调用", track_event(EVENT_QA_ASK, note="恢复正常") is True)

    # ---------- Part D：查询接口 ----------
    print("\n== D. 查询接口 ==")
    reset_db()
    track_event(EVENT_PAGE_VIEW, note="p1")
    track_event(EVENT_PAGE_VIEW, note="p2")
    track_event(EVENT_SERVICE_CLICK, ref_id="print", note="北苑打印店")
    conn = get_connection()
    check("count_events_by_type(page_view)=2", count_events_by_type(conn, EVENT_PAGE_VIEW) == 2)
    check("count_events_by_type(service)=1", count_events_by_type(conn, EVENT_SERVICE_CLICK) == 1)
    stats = get_event_stats(conn)
    check(
        "get_event_stats 含 total",
        stats[EVENT_PAGE_VIEW] == 2 and stats[EVENT_SERVICE_CLICK] == 1 and stats["total"] == 3,
        f"stats={stats}",
    )
    recent = get_recent_events(conn, limit=2)
    check("get_recent_events limit 生效", len(recent) == 2, f"n={len(recent)}")
    recent_type = get_recent_events(conn, limit=10, event_type=EVENT_PAGE_VIEW)
    check("get_recent_events 按类型过滤", len(recent_type) == 2, f"n={len(recent_type)}")
    conn.close()

    # ---------- Part E：服务层查询封装 ----------
    print("\n== E. 服务层查询封装（门控 #1 数据可查） ==")
    check("count_events(service_button_click)=1", count_events(EVENT_SERVICE_CLICK) == 1)
    svc_stats = svc_get_event_stats()
    check("get_event_stats 服务层 total=3", svc_stats["total"] == 3, f"{svc_stats}")
    check("get_recent_events 服务层可查", len(svc_get_recent_events()) == 3)

    # ---------- Part F：服务市场页 AppTest 冒烟 ----------
    # 前后端分离后 pages/ 归属前端（Vue 重写），该冒烟由前端 E2E 覆盖；pages 不存在则跳过。
    market_page = Path(__file__).parent / "pages" / "6_服务市场.py"
    print("\n== F. 服务市场页假按钮（门控 #1 数据源） ==")
    if not market_page.exists():
        print("  [SKIP] pages/ 归属前端（分离后 Vue 重写），服务市场页冒烟由前端 E2E 覆盖")
    else:
        try:
            from streamlit.testing.v1 import AppTest

            reset_db()
            at = AppTest.from_file(str(market_page), default_timeout=15)
            at.run()
            check("服务市场页可加载", not at.exception, f"exception={at.exception}")
            check(
                "页面访问已埋点",
                count_events(EVENT_PAGE_VIEW) >= 1,
                f"page_view={count_events(EVENT_PAGE_VIEW)}",
            )

            # 点「下单」按钮（order_print）→ 记录 service_button_click
            clicked = False
            for b in at.button:
                if b.key == "order_print":
                    b.click()
                    clicked = True
                    break
            check("找到下单按钮 order_print", clicked)
            at.run()
            check(
                "点击后 service_button_click 落库",
                count_events(EVENT_SERVICE_CLICK) >= 1,
                f"service={count_events(EVENT_SERVICE_CLICK)}",
            )
            conn = get_connection()
            svc_row = event_rows(conn, EVENT_SERVICE_CLICK)[0]
            check("服务点击 ref_id/note 正确", svc_row["ref_id"] == "print" and svc_row["note"] == "北苑打印店", f"{dict(svc_row)}")
            conn.close()
        except ImportError as e:
            check(f"AppTest 不可用（跳过）：{e}", False)
        finally:
            reset_db()

    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()
