"""W3 模块 3.3 演示工具 + 两步式预览的验收验证（离线，不依赖 LLM/网络/Streamlit）。

覆盖验收信号：
  1. 造数工具幂等：seed 连续执行两次，通知/订阅/命中/待办行数不变，不污染真实数据；
  2. 演示通知是「未来截止（今天+3/+1 天）+ 命中订阅词」，状态/类型正确；
  3. 全链路：scan_reminders 对演示通知生成 3d/1d 提醒，同日重复扫描幂等（created=0）；
  4. 用户处理：完成待办 → 其提醒自动收敛为已读；忽略提醒 → 状态收敛为 ignored；
  5. clean_demo_data 只清演示数据：真实通知/订阅/命中关系不受影响；
  6. preview_subscription_matches：命中计数正确且只读不写库。

用法：python test_demo.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import storage.db
from storage.db import (
    count_pending_reminders,
    create_subscription,
    get_connection,
    get_matches_for_notice,
    get_reminders,
    get_todos,
    insert_notice,
    list_subscriptions,
)
from storage.models import NoticeRecord
from services.reminder_service import mark_reminder, scan_reminders
from services.subscription_service import match_notice, preview_subscription_matches
from services.todo_service import mark_todo
from tools.seed_demo_data import (
    DEMO_KEYWORD,
    DEMO_SOURCE,
    clean_demo_data,
    seed_demo_data,
)

TMP_DB = Path(__file__).parent / "data" / "test_demo.db"

storage.db.DB_PATH = TMP_DB


def reset_db():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


def insert_real_notice(title: str, summary: str = "") -> int:
    """插入一条「真实」通知（非演示数据），返回 id。"""
    conn = get_connection()
    try:
        insert_notice(
            conn,
            NoticeRecord(
                url=f"https://real.example/{title}",
                source="真实来源",
                title=title,
                raw_content=f"{title} 正文",
                published_at="2026-01-01T00:00:00",
                status="extracted",
            ),
        )
        nid = conn.execute(
            "SELECT id FROM notices WHERE url = ?", (f"https://real.example/{title}",)
        ).fetchone()["id"]
        conn.execute(
            """UPDATE notices SET notice_type='scholarship', summary=?, deadline=? WHERE id = ?""",
            (summary, (date.today() + timedelta(days=30)).isoformat(), nid),
        )
        conn.commit()
    finally:
        conn.close()
    match_notice(nid)
    return nid


def count_rows(table: str) -> int:
    conn = get_connection()
    try:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    finally:
        conn.close()


def demo_reminder_rows(conn):
    ids = [
        r["id"]
        for r in conn.execute("SELECT id FROM notices WHERE source = ?", (DEMO_SOURCE,)).fetchall()
    ]
    return [r for r in get_reminders(conn) if r["notice_id"] in ids]


def run():
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    # ---------- Part A：preview_subscription_matches 只读且计数正确 ----------
    print("\n== A. 订阅两步式预览：preview_subscription_matches 只读不写库 ==")
    reset_db()
    conn = get_connection()
    insert_notice(
        conn,
        NoticeRecord(
            url="https://real.example/a1",
            source="真实来源",
            title="关于申请2026年示范奖学金的通知",
            raw_content="奖学金正文",
            status="extracted",
        ),
    )
    conn.execute(
        """UPDATE notices SET notice_type='scholarship', summary='含示范词奖学金' WHERE url = ?""",
        ("https://real.example/a1",),
    )
    conn.commit()
    conn.close()

    before_subs = count_rows("subscriptions")
    preview = preview_subscription_matches("示范", None, True)
    after_subs = count_rows("subscriptions")
    check("预览命中计数 >= 1", preview["matched"] >= 1, f"preview={preview}")
    check("预览不写订阅表", after_subs == before_subs, f"before={before_subs} after={after_subs}")
    check("预览返回样例标题", len(preview.get("samples", [])) >= 1, f"samples={preview.get('samples')}")

    # ---------- Part B：seed 幂等（跑两次行数不变） ----------
    print("\n== B. 造数工具幂等：seed 连续执行两次 ==")
    reset_db()
    # 先放一条真实通知 + 真实订阅，验证隔离
    real_nid = insert_real_notice("国家奖学金申请通知", summary="国家奖学金评定办法")
    real_sub_id = create_subscription(get_connection(), "国家奖学金", None, True)
    get_connection().close()
    match_notice(real_nid)

    r1 = seed_demo_data(create_todos=True)
    n1 = {
        "notices": count_rows("notices"),
        "subs": count_rows("subscriptions"),
        "matches": count_rows("notice_subscription_matches"),
        "todos": count_rows("todos"),
        "reminders": count_rows("reminders"),
    }
    r2 = seed_demo_data(create_todos=True)
    n2 = {
        "notices": count_rows("notices"),
        "subs": count_rows("subscriptions"),
        "matches": count_rows("notice_subscription_matches"),
        "todos": count_rows("todos"),
        "reminders": count_rows("reminders"),
    }
    check("第二次 seed 通知数不变", n1["notices"] == n2["notices"], f"{n1['notices']} -> {n2['notices']}")
    check("第二次 seed 订阅数不变", n1["subs"] == n2["subs"], f"{n1['subs']} -> {n2['subs']}")
    check("第二次 seed 命中关系数不变", n1["matches"] == n2["matches"], f"{n1['matches']} -> {n2['matches']}")
    check("第二次 seed 待办数不变", n1["todos"] == n2["todos"], f"{n1['todos']} -> {n2['todos']}")
    check("真实通知仍在（seed 不污染）", count_rows("notices") == 2 + 1, "演示2 + 真实1")
    check(
        "演示订阅存在",
        any(s["keyword"] == DEMO_KEYWORD for s in list_subscriptions(get_connection())),
    )
    get_connection().close()

    # ---------- Part C：演示通知属性 + 命中 ----------
    print("\n== C. 演示通知：未来截止 + 命中订阅词 ==")
    today = date.today()
    conn = get_connection()
    demo_ids = [
        r["id"]
        for r in conn.execute("SELECT id FROM notices WHERE source = ?", (DEMO_SOURCE,)).fetchall()
    ]
    check("演示通知 2 条", len(demo_ids) == 2, f"ids={demo_ids}")
    for nid in demo_ids:
        row = conn.execute("SELECT * FROM notices WHERE id = ?", (nid,)).fetchone()
        deadline = row["deadline"]
        days = (date.fromisoformat(deadline[:10]) - today).days
        check(
            f"通知 #{nid} 截止距今天在 {3}/{1} 天档位",
            days in (3, 1),
            f"deadline={deadline} days={days}",
        )
        check(
            f"通知 #{nid} 类型/状态正确",
            row["notice_type"] == "competition" and row["status"] == "extracted",
            f"type={row['notice_type']} status={row['status']}",
        )
        keywords = [m["keyword"] for m in get_matches_for_notice(conn, nid)]
        check(f"通知 #{nid} 命中演示订阅词", DEMO_KEYWORD in keywords, f"keywords={keywords}")
    conn.close()

    # ---------- Part D：扫描生成提醒 + 幂等 ----------
    print("\n== D. 提醒扫描：生成 3d/1d，同日重复扫描幂等 ==")
    result1 = scan_reminders()
    check("首次扫描生成 2 条提醒", result1["created"] == 2, f"result={result1}")
    conn = get_connection()
    rows = demo_reminder_rows(conn)
    check("演示提醒共 2 条", len(rows) == 2, f"n={len(rows)}")
    check("提醒档位 3d/1d 各一", sorted(r["tier"] for r in rows) == ["1d", "3d"], f"tiers={[r['tier'] for r in rows]}")
    check("提醒全部 pending", all(r["status"] == "pending" for r in rows), f"statuses={[r['status'] for r in rows]}")
    conn.close()

    result2 = scan_reminders()
    check("重复扫描 created=0（幂等）", result2["created"] == 0, f"result={result2}")
    conn = get_connection()
    check("重复扫描后演示提醒仍 2 条", len(demo_reminder_rows(conn)) == 2)
    conn.close()

    # ---------- Part E：用户处理（完成→自动已读；忽略→收敛） ----------
    print("\n== E. 用户处理：完成待办 → 提醒自动已读；忽略提醒 → 收敛 ==")
    conn = get_connection()
    rows = demo_reminder_rows(conn)
    demo_notice_ids = {r["notice_id"] for r in rows}
    by_tier = {r["tier"]: r for r in rows}
    d3 = by_tier["3d"]
    d1 = by_tier["1d"]
    todo3 = [
        t for t in get_todos(conn) if t["notice_id"] == d3["notice_id"] and t["status"] == "pending"
    ][0]
    conn.close()
    mark_todo(todo3["id"], "done")
    conn = get_connection()
    d3_after = [r for r in get_reminders(conn) if r["id"] == d3["id"]][0]
    check("完成 3d 待办 → 其提醒自动已读", d3_after["status"] == "read", f"status={d3_after['status']}")
    conn.close()

    mark_reminder(d1["id"], "ignored")
    conn = get_connection()
    d1_after = [r for r in get_reminders(conn) if r["id"] == d1["id"]][0]
    check("忽略 1d 提醒 → 状态 ignored", d1_after["status"] == "ignored", f"status={d1_after['status']}")
    check("剩余待处理提醒数正确", count_pending_reminders(conn) == 0, f"pending={count_pending_reminders(conn)}")
    conn.close()

    # ---------- Part F：clean 只清演示数据，真实数据不动 ----------
    print("\n== F. 清理：clean_demo_data 只清演示数据 ==")
    cleaned = clean_demo_data()
    check("清理删除演示通知 2 条", cleaned["notices_deleted"] == 2, f"cleaned={cleaned}")
    conn = get_connection()
    demo_left = conn.execute(
        "SELECT COUNT(*) AS n FROM notices WHERE source = ?", (DEMO_SOURCE,)
    ).fetchone()["n"]
    check("无演示通知残留", demo_left == 0)
    subs_left = [s for s in list_subscriptions(conn) if s["keyword"] == DEMO_KEYWORD]
    check("演示订阅已删除", len(subs_left) == 0)
    real_left = conn.execute("SELECT COUNT(*) AS n FROM notices WHERE source = ?", ("真实来源",)).fetchone()["n"]
    check("真实通知仍在", real_left == 1, f"n={real_left}")
    real_subs = [s for s in list_subscriptions(conn) if s["keyword"] == "国家奖学金"]
    check("真实订阅仍在", len(real_subs) == 1)
    real_matches = conn.execute(
        "SELECT COUNT(*) AS n FROM notice_subscription_matches WHERE notice_id = ?", (real_nid,)
    ).fetchone()["n"]
    check("真实命中关系仍在", real_matches >= 1, f"n={real_matches}")
    check("全部提醒已级联清理", count_rows("reminders") == 0)
    check("全部待办已级联清理", count_rows("todos") == 0)
    conn.close()

    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()
