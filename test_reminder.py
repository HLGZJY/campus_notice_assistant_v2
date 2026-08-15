"""W3 模块 3.2 截止提醒的验收验证（离线，不依赖 LLM/网络/Streamlit）。

覆盖验收信号：
  1. 造一条截止在 3 天内的通知 → 扫描任务运行后产生提醒（档位 3d / 1d）；
  2. 同一天重复扫描不产生重复提醒（幂等：UNIQUE(notice_id, todo_id, tier, remind_on)）；
  3. 提醒生成不依赖 Streamlit（直接调用服务函数即可落库）；
  4. 过期 / 超出档位的通知不生成提醒；
  5. 有待办的通知提醒挂上 todo_id 且不重复；兜底待办（通知无 deadline）也覆盖；
  6. 待办完成/跳过 → 其待处理提醒自动已读；
  7. 删除通知 → 提醒级联删除；已读/忽略操作生效。

用法：python test_reminder.py
"""
import shutil
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
    count_reminders_by_status,
    delete_notice,
    delete_reminders_for_todo,
    get_connection,
    get_reminders,
    insert_reminder,
    set_todo_status,
)
from services.reminder_service import (
    count_pending_reminders as svc_count_pending,
    get_pending_reminders,
    get_reminder_stats,
    mark_reminder,
    scan_reminders,
)

TMP_DB = Path(__file__).parent / "data" / "test_reminder.db"

storage.db.DB_PATH = TMP_DB


def reset_db():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


def insert_notice_sql(conn, url, title, deadline=None, notice_type="competition", status="extracted"):
    conn.execute(
        """INSERT INTO notices
           (url, source, title, raw_content, published_at, crawled_at, status, notice_type, deadline)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            url,
            "测试来源",
            title,
            f"{title} 正文",
            "2026-01-01T00:00:00",
            "2026-01-02T00:00:00",
            status,
            notice_type,
            deadline,
        ),
    )
    conn.commit()
    return conn.execute("SELECT id FROM notices WHERE url = ?", (url,)).fetchone()["id"]


def insert_todo_sql(conn, notice_id, action, due_at, status="pending"):
    conn.execute(
        """INSERT INTO todos (notice_id, action, due_at, priority, status, created_at)
           VALUES (?, ?, ?, 'normal', ?, ?)""",
        (notice_id, action, due_at, status, "2026-01-03T00:00:00"),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM todos WHERE notice_id = ? AND action = ?", (notice_id, action)
    ).fetchone()["id"]


def reminder_rows(conn, notice_id=None):
    if notice_id is None:
        return conn.execute("SELECT * FROM reminders ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM reminders WHERE notice_id = ? ORDER BY id", (notice_id,)
    ).fetchall()


def run():
    reset_db()
    failures = []
    today = date.today()

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    print("== 0. 表结构由 SCHEMA 自动创建 ==")
    conn = get_connection()
    r_cols = {r[1] for r in conn.execute("PRAGMA table_info(reminders)")}
    check(
        "reminders 列齐全",
        {"notice_id", "todo_id", "due_at", "tier", "remind_on", "status", "created_at", "read_at"} <= r_cols,
        f"cols={sorted(r_cols)}",
    )
    conn.close()

    # ---------- Part A：档位扫描（截止前 3 天 / 1 天） ----------
    print("\n== A. 扫描生成提醒（3d / 1d），过期与超出档位不生成 ==")
    reset_db()
    conn = get_connection()
    n_d3 = insert_notice_sql(conn, "https://r/a1", "三天后截止通知", deadline=(today + timedelta(days=3)).isoformat())
    n_d1 = insert_notice_sql(conn, "https://r/a2", "一天后截止通知", deadline=(today + timedelta(days=1)).isoformat())
    insert_notice_sql(conn, "https://r/a3", "五天前通知", deadline=(today + timedelta(days=5)).isoformat())
    insert_notice_sql(conn, "https://r/a4", "已过期通知", deadline=(today - timedelta(days=1)).isoformat())
    insert_notice_sql(conn, "https://r/a5", "无截止通知", deadline=None)
    conn.close()

    result = scan_reminders()
    check("扫描返回生成 2 条", result["created"] == 2, f"result={result}")

    conn = get_connection()
    rows = reminder_rows(conn)
    check("库中共 2 条提醒", len(rows) == 2, f"n={len(rows)}")
    d3_rows = [r for r in rows if r["notice_id"] == n_d3]
    d1_rows = [r for r in rows if r["notice_id"] == n_d1]
    check("3 天前截止 → 档位 3d", len(d3_rows) == 1 and d3_rows[0]["tier"] == "3d", f"{d3_rows}")
    check("1 天前截止 → 档位 1d", len(d1_rows) == 1 and d1_rows[0]["tier"] == "1d", f"{d1_rows}")
    check("remind_on = 今天", all(r["remind_on"] == today.isoformat() for r in rows), f"remind_on={[r['remind_on'] for r in rows]}")
    conn.close()

    # ---------- Part B：同日重复扫描幂等 ----------
    print("\n== B. 同一天重复扫描不产生重复提醒 ==")
    result2 = scan_reminders()
    check("第二次扫描 created=0（全部幂等跳过）", result2["created"] == 0, f"result={result2}")
    conn = get_connection()
    check("重复扫描后总行数仍为 2", len(reminder_rows(conn)) == 2)
    conn.close()

    # ---------- Part C：remind_on 参与幂等键（不同天生成新提醒） ----------
    print("\n== C. remind_on 参与幂等：不同日期是不同的提醒 ==")
    conn = get_connection()
    inserted = insert_reminder(
        conn, notice_id=n_d3, todo_id=None, due_at=(today + timedelta(days=3)).isoformat(),
        tier="3d", remind_on=(today - timedelta(days=1)).isoformat(),
    )
    check("手工插入昨日提醒成功", inserted, "INSERT OR IGNORE 返回 False")
    conn.close()
    scan_reminders()
    conn = get_connection()
    d3_after = reminder_rows(conn, notice_id=n_d3)
    check("昨日提醒 + 今日扫描 = 同一对象两条（不同 remind_on）", len(d3_after) == 2, f"n={len(d3_after)}")
    conn.close()

    # ---------- Part D：有待办的通知挂 todo_id，兜底待办也覆盖 ----------
    print("\n== D. 通知有待办挂 todo_id；兜底待办（通知无 deadline）也生成 ==")
    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://r/d1", "竞赛报名", deadline=(today + timedelta(days=3)).isoformat())
    t1 = insert_todo_sql(conn, n1, "在截止前完成竞赛报名", (today + timedelta(days=3)).isoformat())
    n2 = insert_notice_sql(conn, "https://r/d2", "无截止字段的通知", deadline=None)
    t2 = insert_todo_sql(conn, n2, "兜底待办事项", (today + timedelta(days=1)).isoformat())
    conn.close()

    result = scan_reminders()
    check("扫描创建 2 条（通知路 1 + 兜底路 1）", result["created"] == 2, f"result={result}")
    conn = get_connection()
    rows = reminder_rows(conn)
    by_notice = {r["notice_id"]: r for r in rows}
    check("通知路提醒挂上 todo_id", by_notice[n1]["todo_id"] == t1, f"todo_id={by_notice[n1]['todo_id']}")
    check("同一截止不重复：通知 n1 仅 1 条", len(reminder_rows(conn, n1)) == 1)
    check("兜底待办提醒挂 todo_id 且关联通知", by_notice[n2]["todo_id"] == t2, f"todo_id={by_notice[n2]['todo_id']}")
    conn.close()

    # ---------- Part D2：待办延期后，扫描优先采用待办 due_at ----------
    print("\n== D2. 待办延期：扫描优先采用待办 due_at ==")
    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://r/d2x", "延期的通知", deadline=(today + timedelta(days=3)).isoformat())
    t1 = insert_todo_sql(conn, n1, "延后到 1 天后的待办", (today + timedelta(days=1)).isoformat())
    conn.close()

    result = scan_reminders()
    check("扫描创建 1 条提醒", result["created"] == 1, f"result={result}")
    conn = get_connection()
    rows = reminder_rows(conn, notice_id=n1)
    check("生成 1 条提醒", len(rows) == 1, f"rows={rows}")
    check("提醒 due_at = 待办 due_at（1 天后）", rows[0]["due_at"] == (today + timedelta(days=1)).isoformat(), f"due_at={rows[0]['due_at']}")
    check("提醒档位按待办 due 计算 = 1d", rows[0]["tier"] == "1d", f"tier={rows[0]['tier']}")
    check("提醒挂上 todo_id", rows[0]["todo_id"] == t1)
    conn.close()

    # ---------- Part E：待办完成 → 提醒自动已读 ----------
    print("\n== E. 待办完成/跳过后待处理提醒自动收敛为已读 ==")
    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://r/e1", "报名通知", deadline=(today + timedelta(days=3)).isoformat())
    t1 = insert_todo_sql(conn, n1, "完成报名", (today + timedelta(days=3)).isoformat())
    conn.close()
    scan_reminders()
    conn = get_connection()
    check("待办完成前提醒 pending", reminder_rows(conn)[0]["status"] == "pending")
    set_todo_status(conn, t1, "done")
    check("待办 done → 提醒自动 read", reminder_rows(conn)[0]["status"] == "read", f"row={reminder_rows(conn)[0]}")
    check("待处理提醒数归零", count_pending_reminders(conn) == 0)
    conn.close()

    # ---------- Part F：已读 / 忽略操作 ----------
    print("\n== F. 已读 / 忽略操作 ==")
    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://r/f1", "通知", deadline=(today + timedelta(days=3)).isoformat())
    conn.close()
    scan_reminders()
    conn = get_connection()
    rid = reminder_rows(conn)[0]["id"]
    conn.close()
    mark_reminder(rid, "read")
    conn = get_connection()
    check("标记已读生效", conn.execute("SELECT status FROM reminders WHERE id = ?", (rid,)).fetchone()["status"] == "read")
    conn.close()
    mark_reminder(rid, "ignored")
    conn = get_connection()
    check("标记忽略生效", conn.execute("SELECT status FROM reminders WHERE id = ?", (rid,)).fetchone()["status"] == "ignored")
    check("已读+忽略后 pending=0", svc_count_pending() == 0)
    conn.close()

    # ---------- Part G：删除通知 / 删除待办级联清理 ----------
    print("\n== G. 删除通知/待办级联清理提醒 ==")
    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://r/g1", "通知", deadline=(today + timedelta(days=3)).isoformat())
    t1 = insert_todo_sql(conn, n1, "待办", (today + timedelta(days=3)).isoformat())
    conn.close()
    scan_reminders()
    conn = get_connection()
    check("删除前有 1 条提醒", len(reminder_rows(conn)) == 1)
    delete_notice(conn, n1)
    check("删除通知后提醒级联删除", len(reminder_rows(conn)) == 0)
    conn.close()

    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://r/g2", "通知", deadline=None)
    t1 = insert_todo_sql(conn, n1, "兜底待办", (today + timedelta(days=1)).isoformat())
    conn.close()
    scan_reminders()
    conn = get_connection()
    check("删除前有 1 条兜底提醒", len(reminder_rows(conn)) == 1)
    delete_reminders_for_todo(conn, t1)
    check("删除待办后其提醒级联删除", len(reminder_rows(conn)) == 0)
    conn.close()

    # ---------- Part H：查询接口（UI 只读） ----------
    print("\n== H. UI 查询接口 ==")
    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://r/h1", "三天后截止", deadline=(today + timedelta(days=3)).isoformat())
    insert_todo_sql(conn, n1, "完成报名", (today + timedelta(days=3)).isoformat())
    conn.close()
    scan_reminders()

    stats = get_reminder_stats()
    check("状态统计 pending=1", stats["pending"] == 1 and stats["total"] == 1, f"{stats}")
    check("count_pending_reminders = 1", svc_count_pending() == 1)

    pend = get_pending_reminders()
    check("pending 列表带待办动作文案", len(pend) == 1 and pend[0]["todo_action"] == "完成报名", f"{pend}")
    check("pending 列表带档位标签与 is_today", pend[0]["tier_label"] == "⏳ 距截止 3 天" and pend[0]["is_today"], f"{pend[0]}")
    check("get_reminders(status=read) 为空", get_reminders(get_connection(), status="read") == [])
    check("count_reminders_by_status pending=1", count_reminders_by_status(get_connection())["pending"] == 1)
    get_connection().close()

    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()
