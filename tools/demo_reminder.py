"""全链路演示脚本（W3 模块 3.3）：发布 → 命中 → 提醒 → 待办 → 用户处理。

一条命令跑完整闭环并自验证（幂等 + 提醒级联收敛），数据默认保留供 Streamlit UI
展示，--clean 可一键清场。全程不依赖手工改库、不依赖 LLM/网络。

用法：
    python tools/demo_reminder.py --demo              # 完整闭环演示（默认：先重置再跑）
    python tools/demo_reminder.py --demo --no-reset   # 不重置，基于现有演示数据跑
    python tools/demo_reminder.py --clean             # 清理全部演示数据

演示后打开 Streamlit 可查看完整效果：
    streamlit run app.py   # 首页红点 / 订阅管理命中 / 待办页提醒区（忽略为两步式）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from storage.db import get_connection, get_matches_for_notice, get_reminders, get_todos
from services.reminder_service import mark_reminder, scan_reminders
from services.todo_service import mark_todo
from tools.seed_demo_data import DEMO_KEYWORD, clean_demo_data, seed_demo_data


class Reporter:
    """逐步骤输出 + 断言计数，最终汇总 PASS/FAIL 并以退出码反映结果。"""

    def __init__(self):
        self.failures: list[str] = []

    def step(self, title: str) -> None:
        print(f"\n== {title} ==")

    def ok(self, name: str, detail: str = "") -> None:
        print(f"  [OK]   {name}" + (f"  ({detail})" if detail else ""))

    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        if cond:
            self.ok(name, detail)
            return True
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))
        self.failures.append(name)
        return False


def _notices_by_tier(seed: dict) -> dict:
    return {n["tier"]: n for n in seed["notices"]}


def _reminders_for_notice(conn, notice_id: int) -> list[dict]:
    return [r for r in get_reminders(conn) if r["notice_id"] == notice_id]


def _todos_for_notice(conn, notice_id: int) -> list[dict]:
    return [t for t in get_todos(conn) if t["notice_id"] == notice_id]


def run_demo(rep: Reporter, reset: bool) -> None:
    if reset:
        cleaned = clean_demo_data()
        if cleaned["notices_deleted"] or cleaned["subscription_deleted"]:
            rep.ok("已重置旧演示数据", f"通知 {cleaned['notices_deleted']} 条 / 订阅 {cleaned['subscription_deleted']} 条")
        else:
            rep.ok("无旧演示数据，无需重置")

    # ---- 1. 发布 ----
    rep.step("[1/5] 发布：插入「未来截止」演示通知")
    seed = seed_demo_data(create_todos=True)
    by_tier = _notices_by_tier(seed)
    for n in seed["notices"]:
        rep.ok(f"通知 #{n['notice_id']} [{n['tier']}] {n['title']}", f"截止 {n['deadline']}")

    # ---- 2. 命中 ----
    rep.step("[2/5] 命中：订阅规则自动标记")
    conn = get_connection()
    try:
        for n in seed["notices"]:
            keywords = [m["keyword"] for m in get_matches_for_notice(conn, n["notice_id"])]
            rep.check(
                f"通知 #{n['notice_id']} 命中订阅词 {DEMO_KEYWORD}",
                DEMO_KEYWORD in keywords,
                f"keywords={keywords}",
            )
    finally:
        conn.close()

    # ---- 3. 提醒 ----
    rep.step("[3/5] 提醒：扫描生成截止前 3 天 / 1 天提醒")
    before = _count_demo_reminders(by_tier)
    scan1 = scan_reminders()
    conn = get_connection()
    try:
        for tier, n in by_tier.items():
            rows = _reminders_for_notice(conn, n["notice_id"])
            rep.check(
                f"[{tier}] 通知 #{n['notice_id']} 生成 1 条 {tier} 提醒",
                len(rows) == 1 and rows[0]["tier"] == tier,
                f"rows={[(r['tier'], r['status']) for r in rows]}",
            )
            if rows:
                rep.check(
                    f"[{tier}] 提醒挂上待办 id（待办兜底链路正常）",
                    rows[0]["todo_id"] == n["todo_id"],
                    f"todo_id={rows[0]['todo_id']}",
                )
    finally:
        conn.close()
    rep.ok(f"扫描创建提醒 {scan1.get('created', 0)} 条", f"通知路 {scan1.get('notice_created', 0)}")

    # 幂等验证：同一天重复扫描不产生重复提醒
    scan2 = scan_reminders()
    after = _count_demo_reminders(by_tier)
    rep.check(
        "幂等：重复扫描 created=0",
        scan2.get("created", 0) == 0,
        f"scan2.created={scan2.get('created', 0)}",
    )
    rep.check(
        "幂等：重复扫描后演示提醒总数不变",
        after == before + 2,
        f"before={before} after={after}",
    )

    # ---- 4. 待办 ----
    rep.step("[4/5] 待办：演示通知关联待办（确定性直插）")
    conn = get_connection()
    try:
        for tier, n in by_tier.items():
            todos = _todos_for_notice(conn, n["notice_id"])
            rep.check(
                f"[{tier}] 通知 #{n['notice_id']} 有 1 条 pending 待办",
                len(todos) == 1 and todos[0]["status"] == "pending",
                f"todos={[(t['action'], t['status']) for t in todos]}",
            )
    finally:
        conn.close()

    # ---- 5. 用户处理 ----
    rep.step("[5/5] 用户处理：完成待办 → 提醒自动已读；忽略提醒 → 状态收敛")
    d3 = by_tier["3d"]
    d1 = by_tier["1d"]
    conn = get_connection()
    try:
        d3_rem = _reminders_for_notice(conn, d3["notice_id"])[0]
        mark_todo(d3["todo_id"], "done")
        after_read = _reminders_for_notice(conn, d3["notice_id"])
        rep.check(
            "完成 3d 待办 → 其提醒自动收敛为已读",
            after_read and after_read[0]["status"] == "read",
            f"status={after_read[0]['status'] if after_read else None}",
        )

        d1_rem = _reminders_for_notice(conn, d1["notice_id"])[0]
        mark_reminder(d1_rem["id"], "ignored")
        after_ignored = _reminders_for_notice(conn, d1["notice_id"])
        rep.check(
            "忽略 1d 提醒 → 状态为 ignored",
            after_ignored and after_ignored[0]["status"] == "ignored",
            f"status={after_ignored[0]['status'] if after_ignored else None}",
        )
    finally:
        conn.close()

    print("\n== 演示完成：数据已保留，可在 Streamlit 中查看 ==")
    print("    streamlit run app.py")


def _count_demo_reminders(by_tier: dict) -> int:
    conn = get_connection()
    try:
        ids = [n["notice_id"] for n in by_tier.values()]
        rows = get_reminders(conn)
        return sum(1 for r in rows if r["notice_id"] in ids)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="全链路演示：发布 → 命中 → 提醒 → 待办 → 用户处理")
    parser.add_argument("--demo", action="store_true", help="完整闭环演示（默认）")
    parser.add_argument("--no-reset", action="store_true", help="演示前不清理旧演示数据")
    parser.add_argument("--clean", action="store_true", help="仅清理全部演示数据")
    parser.add_argument("--db", type=str, default=None, help="指定 SQLite 路径（默认 data/notices.db）")
    args = parser.parse_args()

    if args.db:
        import storage.db as _db

        _db.DB_PATH = Path(args.db)

    if args.clean:
        r = clean_demo_data()
        print(
            f"已清理演示数据：通知 {r['notices_deleted']} 条，"
            f"演示订阅 {r['subscription_deleted']} 条。"
        )
        return

    rep = Reporter()
    print("=" * 64)
    print("全链路演示：发布 → 命中 → 提醒 → 待办 → 用户处理")
    print("=" * 64)
    run_demo(rep, reset=not args.no_reset)

    print("\n" + "=" * 64)
    if rep.failures:
        print(f"结果：{len(rep.failures)} 项失败 -> {rep.failures}")
        sys.exit(1)
    print("结果：全部通过（幂等 ✓ / 级联收敛 ✓ / 演示数据可一键清理 ✓）")


if __name__ == "__main__":
    main()
