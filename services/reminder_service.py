"""截止提醒服务（W3 模块 3.2）。

职责：
  1. 每日扫描（scan_reminders）：对截止前 3 天 / 1 天的通知（及兜底待办）各生成
     一条提醒，幂等（UNIQUE(notice_id, tier, remind_on)，同一天重复扫描不产生重复
     提醒；SQLite 将 UNIQUE 列中的 NULL 视为互异，故唯一键不含可空的 todo_id）。
  2. 查询：pending 列表、状态统计、首页红点计数。
  3. 操作：已读 / 忽略。

设计要点：
  - 以通知为对象粒度：每条有截止时间的通知只生成一条提醒；若存在待处理待办则
    同时挂上 todo_id（UI 可展示待办动作文案），且截止时间优先采用待办 due_at
    （用户可延期，提醒跟随）。因待办生成时 due_at 被强制等于通知 deadline，
    同一截止时间不会重复提醒。
  - 提醒链路不依赖 Streamlit：扫描由调度器独立进程（scheduler.py 的 reminder
    job）触发，UI 只读表。
  - 纯规则，不消耗 LLM。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from storage.db import (
    count_pending_reminders as _count_pending,
    count_reminders_by_status as _count_by_status,
    get_connection,
    get_reminders as _get_reminders,
    insert_reminder,
    set_reminder_status as _set_reminder_status,
)

logger = logging.getLogger(__name__)

# 提醒档位：距截止的天数 -> 档位名
REMINDER_TIERS: dict[int, str] = {3: "3d", 1: "1d"}
# 档位展示标签
TIER_LABELS: dict[str, str] = {"3d": "⏳ 距截止 3 天", "1d": "⏳ 距截止 1 天"}
REMINDER_STATUS = {"pending", "read", "ignored"}


def _days_until(due_at: Optional[str], today: date) -> Optional[int]:
    """截止日期距今天的天数（按日期差，忽略时刻）。无法解析返回 None。"""
    if not due_at:
        return None
    try:
        due = datetime.fromisoformat(due_at).date()
    except ValueError:
        return None
    return (due - today).days


def _scan_notices(conn, today: date) -> tuple[int, int, int]:
    """通知路：有截止时间的通知，命中 3/1 天时生成提醒。

    有待办的通知优先采用待办 due_at（用户可延期，提醒跟随），
    无待办才回退通知 deadline；提醒行挂上 todo_id 便于 UI 显示动作。
    """
    rows = conn.execute(
        """SELECT id, title, deadline FROM notices
           WHERE deadline IS NOT NULL AND deadline != ''"""
    ).fetchall()
    created = 0
    skipped = 0
    for r in rows:
        # 找该通知的待处理待办（限 1 条）
        todo = conn.execute(
            """SELECT id, due_at FROM todos
               WHERE notice_id = ? AND status = 'pending'
               ORDER BY id ASC LIMIT 1""",
            (r["id"],),
        ).fetchone()
        todo_id = todo["id"] if todo else None
        due_at = todo["due_at"] if (todo and todo["due_at"]) else r["deadline"]
        days = _days_until(due_at, today)
        if days is None or days not in REMINDER_TIERS:
            continue
        tier = REMINDER_TIERS[days]
        ok = insert_reminder(
            conn,
            notice_id=r["id"],
            todo_id=todo_id,
            due_at=due_at,
            tier=tier,
            remind_on=today.isoformat(),
        )
        if ok:
            created += 1
        else:
            skipped += 1
    return len(rows), created, skipped


def _scan_todos_fallback(conn, today: date) -> tuple[int, int, int]:
    """待办兜底路：因数据异常而「有截止但通知无 deadline」的待办，防止漏提醒。

    与通知路按构造不重叠：只处理通知无 deadline（或通知不存在）的 pending 待办。
    """
    rows = conn.execute(
        """SELECT t.id, t.notice_id, t.action, t.due_at
           FROM todos t
           LEFT JOIN notices n ON n.id = t.notice_id
           WHERE t.due_at IS NOT NULL AND t.due_at != ''
             AND t.status = 'pending'
             AND (n.id IS NULL OR n.deadline IS NULL OR n.deadline = '')"""
    ).fetchall()
    created = 0
    skipped = 0
    for r in rows:
        days = _days_until(r["due_at"], today)
        if days is None or days not in REMINDER_TIERS:
            continue
        tier = REMINDER_TIERS[days]
        ok = insert_reminder(
            conn,
            notice_id=r["notice_id"],
            todo_id=r["id"],
            due_at=r["due_at"],
            tier=tier,
            remind_on=today.isoformat(),
        )
        if ok:
            created += 1
        else:
            skipped += 1
    return len(rows), created, skipped


def scan_reminders() -> dict:
    """每日扫描：为截止前 3 天 / 1 天的通知与兜底待办生成提醒（幂等）。

    返回统计供 scheduler_log / 日志使用。
    """
    today = date.today()
    conn = get_connection()
    try:
        notices_scanned, notice_created, notice_skipped = _scan_notices(conn, today)
        todos_scanned, todo_created, todo_skipped = _scan_todos_fallback(conn, today)
    except Exception:
        logger.exception("截止提醒扫描失败")
        raise
    finally:
        conn.close()

    created = notice_created + todo_created
    skipped = notice_skipped + todo_skipped
    logger.info(
        "截止提醒扫描: 通知=%d(生成%d/跳过%d) 兜底待办=%d(生成%d/跳过%d) 合计生成=%d",
        notices_scanned,
        notice_created,
        notice_skipped,
        todos_scanned,
        todo_created,
        todo_skipped,
        created,
    )
    return {
        "notices_scanned": notices_scanned,
        "notice_created": notice_created,
        "todos_scanned": todos_scanned,
        "todo_created": todo_created,
        "created": created,
        "skipped": skipped,
    }


# ---------- 查询（UI 只读） ----------


def get_reminders(status: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """查询提醒列表，带通知标题 / 待办动作文案，按截止时间升序。"""
    conn = get_connection()
    try:
        rows = _get_reminders(conn, status=status, limit=limit)
        for r in rows:
            r["tier_label"] = TIER_LABELS.get(r["tier"], r["tier"])
            r["is_today"] = r["remind_on"] == date.today().isoformat()
        return rows
    finally:
        conn.close()


def get_pending_reminders(limit: Optional[int] = None) -> list[dict]:
    """待处理提醒列表（首页红点 / 待办中心提醒区）。"""
    return get_reminders(status="pending", limit=limit)


def get_reminder_stats() -> dict:
    """按状态统计提醒数量。"""
    conn = get_connection()
    try:
        return _count_by_status(conn)
    finally:
        conn.close()


def count_pending_reminders() -> int:
    """待处理提醒数（首页红点）。"""
    conn = get_connection()
    try:
        return _count_pending(conn)
    finally:
        conn.close()


def mark_reminder(reminder_id: int, status: str) -> bool:
    """更新提醒状态：pending / read / ignored。"""
    if status not in REMINDER_STATUS:
        raise ValueError(f"无效状态: {status}")
    conn = get_connection()
    try:
        return _set_reminder_status(conn, reminder_id, status)
    finally:
        conn.close()
