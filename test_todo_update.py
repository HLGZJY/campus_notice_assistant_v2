"""待办编辑 / 延期 / 备注（PATCH 服务层）的验收验证（离线，不依赖 HTTP/LLM/网络）。

覆盖验收信号：
  1. 更新 action / notes 生效，缺省字段不被覆盖；
  2. 显式 null 清空 notes / due_at；
  3. 延期（改 due_at）后，该待办旧的待处理提醒收敛为已读；
  4. 无效 due_at / 空 action 抛 ValueError（路由层转 400）；
  5. 待办不存在返回 None（路由层转 404）；
  6. 提醒扫描优先采用待办 due_at（通知 deadline 与其不同时以待办为准）。

用法：python test_todo_update.py
"""
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import storage.db
from storage.db import get_connection
from services.reminder_service import scan_reminders
from services.todo_service import update_todo

TMP_DIR = tempfile.mkdtemp(prefix="wb_test_todo_update_")
TMP_DB = Path(TMP_DIR) / "test_todo_update.db"

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


def insert_todo_sql(conn, notice_id, action, due_at, status="pending", notes=None):
    conn.execute(
        """INSERT INTO todos (notice_id, action, due_at, priority, status, created_at, notes)
           VALUES (?, ?, ?, 'normal', ?, ?, ?)""",
        (notice_id, action, due_at, status, "2026-01-03T00:00:00", notes),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM todos WHERE notice_id = ? AND action = ?", (notice_id, action)
    ).fetchone()["id"]


def run():
    reset_db()
    failures = []
    today = date.today()

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    # ---------- Part A：更新 action / notes，缺省字段不被覆盖 ----------
    print("\n== A. 更新 action / notes ==")
    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://t/a1", "报名通知", deadline="2026-09-01T00:00:00")
    t1 = insert_todo_sql(conn, n1, "原待办内容", "2026-09-01T00:00:00")
    conn.close()

    row = update_todo(t1, action="在 09-01 前完成报名", notes="已联系负责人")
    check("action 更新生效", row and row["action"] == "在 09-01 前完成报名", f"{row and row['action']}")
    check("notes 更新生效", row and row["notes"] == "已联系负责人", f"{row and row['notes']}")
    check("notice_title 保留（关联通知）", row and row["notice_title"] == "报名通知", f"{row and row['notice_title']}")
    check("status 不变", row and row["status"] == "pending")
    check("created_at 不变", row and row["created_at"] == "2026-01-03T00:00:00")

    # ---------- Part B：缺省字段不覆盖 + 显式 null 清空 ----------
    print("\n== B. 缺省不修改；显式 null 清空 ==")
    row = update_todo(t1, notes="新备注")
    check("缺省 action 不被覆盖", row and row["action"] == "在 09-01 前完成报名", f"{row and row['action']}")
    check("缺省 due_at 不被覆盖", row and row["due_at"] == "2026-09-01T00:00:00")

    row = update_todo(t1, notes=None)
    check("notes 显式 null 清空", row and row["notes"] is None, f"notes={row and row['notes']!r}")

    row = update_todo(t1, due_at=None)
    check("due_at 显式 null 清空", row and row["due_at"] is None, f"due_at={row and row['due_at']!r}")

    # ---------- Part C：非法输入 ----------
    print("\n== C. 非法输入 / 不存在 ==")
    invalid = False
    try:
        update_todo(t1, due_at="不是时间")
    except ValueError:
        invalid = True
    check("无效 due_at 抛 ValueError", invalid)

    empty = False
    try:
        update_todo(t1, action="   ")
    except ValueError:
        empty = True
    check("空 action 抛 ValueError", empty)

    row = update_todo(999999, notes="x")
    check("不存在的待办返回 None", row is None)

    # ---------- Part D：延期 → 旧待处理提醒收敛为已读 ----------
    print("\n== D. 延期后旧提醒自动收敛 ==")
    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://t/d1", "三天后截止", deadline=(today + timedelta(days=3)).isoformat())
    t1 = insert_todo_sql(conn, n1, "报名待办", (today + timedelta(days=3)).isoformat())
    conn.close()
    scan_reminders()
    conn = get_connection()
    rows = conn.execute("SELECT status FROM reminders WHERE todo_id = ?", (t1,)).fetchall()
    check("延期前提醒为 pending", len(rows) == 1 and rows[0]["status"] == "pending", f"{rows}")
    conn.close()

    update_todo(t1, due_at=(today + timedelta(days=1)).isoformat())
    conn = get_connection()
    rows = conn.execute("SELECT status, due_at FROM reminders WHERE todo_id = ?", (t1,)).fetchall()
    check("延期后旧提醒收敛为已读", len(rows) == 1 and rows[0]["status"] == "read", f"{rows}")
    conn.close()

    # ---------- Part E：提醒扫描优先采用待办 due_at ----------
    print("\n== E. 扫描优先采用待办 due_at（用户可延期） ==")
    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://t/e1", "延期的通知", deadline=(today + timedelta(days=3)).isoformat())
    t1 = insert_todo_sql(conn, n1, "延后到 1 天后的待办", (today + timedelta(days=1)).isoformat())
    conn.close()

    result = scan_reminders()
    check("扫描创建 1 条提醒", result["created"] == 1, f"result={result}")
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE notice_id = ?", (n1,)
    ).fetchall()
    check("生成 1 条提醒", len(rows) == 1, f"rows={rows}")
    check("提醒 due_at = 待办 due_at（1 天后）", rows[0]["due_at"] == (today + timedelta(days=1)).isoformat(), f"due_at={rows[0]['due_at']}")
    check("提醒档位按待办 due 计算 = 1d", rows[0]["tier"] == "1d", f"tier={rows[0]['tier']}")
    check("提醒挂上 todo_id", rows[0]["todo_id"] == t1)
    conn.close()

    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()