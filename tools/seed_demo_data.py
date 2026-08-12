"""演示造数工具（W3 模块 3.3）：一键插入「未来截止 + 命中订阅词」的测试通知。

供全链路演示（发布 → 命中 → 提醒 → 待办 → 用户处理）使用。

设计要点：
  - 幂等：演示通知用确定性 URL（INSERT OR IGNORE，url UNIQUE 去重），演示订阅按
    keyword 查重；重复执行不产生重复数据，不污染真实数据。
  - 自动刷新：再次执行会把演示通知的截止时间刷新为「今天+3 / 今天+1 天」，
    保证无论哪一天运行，扫描都恰好命中提醒档位 3d / 1d。
  - 隔离：演示数据统一用 source='演示数据' 标记，clean_demo_data() 只清理该来源
    及其关联的待办/提醒/命中关系与演示订阅，不碰真实数据。
  - 无 LLM：结构化字段直接写入（绕过提取链路），全链路演示不依赖网络与模型；
    待办按确定性模板直插（与 core.todo.template_fallback 同款文案）。

用法：
    python tools/seed_demo_data.py --seed      # 插入演示通知 + 订阅 + 命中 + 待办（默认）
    python tools/seed_demo_data.py --seed --no-todo   # 不生成待办
    python tools/seed_demo_data.py --clean     # 清理全部演示数据
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from core.todo import compute_priority
from storage.db import (
    create_subscription,
    delete_subscription,
    delete_todos_for_notice,
    delete_notices_by_source,
    get_connection,
    insert_notice,
    insert_todo,
    list_subscriptions,
    update_extraction,
)
from storage.models import NoticeRecord
from services.subscription_service import match_notice

# 演示数据标记：source 统一前缀，供清理与 UI 过滤识别
DEMO_SOURCE = "演示数据"
# 演示订阅词：演示通知的标题/摘要都包含该词
DEMO_KEYWORD = "演示竞赛"
# 演示通知 URL 前缀（确定性，幂等键）
DEMO_URL_PREFIX = "https://demo.invalid/notice"
# 提醒档位映射：距截止天数 -> 档位
TIER_DAYS = {"3d": 3, "1d": 1}


def _now() -> str:
    return datetime.now().isoformat()


def _notice_specs(today: date) -> list[dict]:
    """两条演示通知：一条距截止 3 天（3d），一条距截止 1 天（1d）。"""
    specs = []
    for tier, days in TIER_DAYS.items():
        deadline = (today + timedelta(days=days)).isoformat()
        d = today + timedelta(days=days)
        specs.append(
            {
                "url": f"{DEMO_URL_PREFIX}-{tier}",
                "title": f"关于组织参加2026年{DEMO_KEYWORD}报名工作的通知" if tier == "3d"
                else f"关于举办2026年{DEMO_KEYWORD}校赛的通知",
                "deadline": deadline,
                "deadline_raw": f"截止至 {d.month}月{d.day}日 17:00",
                "summary": (
                    f"{DEMO_KEYWORD}报名/参赛安排已发布，请相关同学及时关注并在截止时间前完成报名，"
                    f"逾期不予补报。"
                ),
                "raw_content": (
                    f"{DEMO_KEYWORD}相关工作已启动。报名截止时间为 {d.month}月{d.day}日17:00，"
                    f"请有意向的同学在规定时间内通过报名链接完成报名。详情见附件。"
                ),
                "tier": tier,
            }
        )
    return specs


def ensure_demo_subscription() -> int:
    """幂等创建演示订阅，返回订阅 id（已存在则直接复用）。"""
    conn = get_connection()
    try:
        for s in list_subscriptions(conn):
            if s["keyword"] == DEMO_KEYWORD:
                return s["id"]
        return create_subscription(conn, DEMO_KEYWORD, None, True)
    finally:
        conn.close()


def _find_notice_id_by_url(conn, url: str) -> int:
    return conn.execute("SELECT id FROM notices WHERE url = ?", (url,)).fetchone()["id"]


def _upsert_demo_notice(conn, spec: dict) -> int:
    """插入（或幂等跳过）一条演示通知，随后刷新结构化字段与截止时间。返回通知 id。"""
    crawled_at = _now()
    record = NoticeRecord(
        url=spec["url"],
        source=DEMO_SOURCE,
        title=spec["title"],
        raw_content=spec["raw_content"],
        published_at=(date.today() - timedelta(days=2)).isoformat(),
        crawled_at=crawled_at,
        status="extracted",
    )
    inserted = insert_notice(conn, record)
    notice_id = _find_notice_id_by_url(conn, spec["url"])
    # 覆盖式写结构化字段（刷新 deadline 到「今天+3/+1」，保证重跑仍命中提醒档位）
    update_extraction(
        conn,
        notice_id,
        {
            "notice_type": "competition",
            "target_audience": "全体在校学生",
            "signup_method": "通过报名链接在线填报",
            "signup_url": "https://demo.invalid/signup",
            "location": "线上",
            "location_type": "online",
            "deadline": spec["deadline"],
            "deadline_raw": spec["deadline_raw"],
            "key_dates": [],
            "summary": spec["summary"],
        },
        "extracted",
    )
    match_notice(notice_id)  # 命中订阅回填（幂等：delete-then-insert）
    return notice_id


def _seed_todo(notice_id: int, title: str, deadline: str) -> int:
    """确定性直插一条待办（替换旧 pending），返回待办 id。"""
    conn = get_connection()
    try:
        delete_todos_for_notice(conn, notice_id, status="pending")
        return insert_todo(
            conn,
            notice_id=notice_id,
            action=f"在 {deadline} 前完成《{title}》报名/提交",
            due_at=deadline,
            priority=compute_priority(deadline),
        )
    finally:
        conn.close()


def seed_demo_data(create_todos: bool = True, refresh: bool = True) -> dict:
    """插入演示通知 + 订阅 + 命中关系 +（可选）待办。幂等可重复执行。

    Args:
        create_todos: 是否同时生成确定性待办（全链路演示需要）
        refresh: 是否把已存在演示通知的截止时间刷新为「今天+3/+1」（默认 True，
            保证任何一天运行都落在提醒档位）
    """
    today = date.today()
    sub_id = ensure_demo_subscription()
    specs = _notice_specs(today)

    conn = get_connection()
    try:
        results = []
        for spec in specs:
            notice_id = _upsert_demo_notice(conn, spec)
            todo_id = _seed_todo(notice_id, spec["title"], spec["deadline"]) if create_todos else None
            results.append(
                {
                    "notice_id": notice_id,
                    "tier": spec["tier"],
                    "title": spec["title"],
                    "deadline": spec["deadline"],
                    "url": spec["url"],
                    "todo_id": todo_id,
                    "subscription_id": sub_id,
                }
            )
    finally:
        conn.close()

    return {
        "subscription_id": sub_id,
        "subscription_keyword": DEMO_KEYWORD,
        "notices": results,
    }


def clean_demo_data() -> dict:
    """清理全部演示数据：演示通知（级联待办/提醒/命中）+ 演示订阅。返回删除统计。"""
    conn = get_connection()
    try:
        notice_ids, notices_deleted = delete_notices_by_source(conn, DEMO_SOURCE)
        subscription_deleted = 0
        for s in list_subscriptions(conn):
            if s["keyword"] == DEMO_KEYWORD:
                subscription_deleted += delete_subscription(conn, s["id"])
    finally:
        conn.close()
    return {
        "notices_deleted": notices_deleted,
        "notice_ids": notice_ids,
        "subscription_deleted": subscription_deleted,
    }


def main():
    parser = argparse.ArgumentParser(description="演示造数工具（W3 模块 3.3）")
    parser.add_argument("--seed", action="store_true", help="插入演示数据（默认行为）")
    parser.add_argument("--clean", action="store_true", help="清理全部演示数据")
    parser.add_argument("--no-todo", action="store_true", help="插入通知/订阅/命中但不生成待办")
    parser.add_argument("--db", type=str, default=None, help="指定 SQLite 路径（默认 data/notices.db）")
    args = parser.parse_args()

    if args.db:
        import storage.db as _db

        _db.DB_PATH = Path(args.db)

    if args.clean:
        r = clean_demo_data()
        print(
            f"已清理演示数据：通知 {r['notices_deleted']} 条"
            f"{'（id: ' + str(r['notice_ids']) + '）' if r['notice_ids'] else ''}，"
            f"演示订阅 {r['subscription_deleted']} 条。"
        )
        return

    seed = seed_demo_data(create_todos=not args.no_todo)
    print(f"已就绪演示订阅「{seed['subscription_keyword']}」(id={seed['subscription_id']})")
    for n in seed["notices"]:
        line = (
            f"  通知 #{n['notice_id']} [{n['tier']}] {n['title']} "
            f"截止 {n['deadline']} 命中订阅 ✓"
        )
        if n["todo_id"]:
            line += f" 待办 #{n['todo_id']} ✓"
        print(line)


if __name__ == "__main__":
    main()
