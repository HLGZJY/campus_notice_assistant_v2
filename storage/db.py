"""SQLite 存储层。"""
import contextvars
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .models import NoticeRecord

DB_PATH = Path(__file__).parent.parent / "data" / "notices.db"

# update_subscription 的"未提供"哨兵：区分"不修改"与"清空为 NULL"
_UNSET = object()

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    raw_content TEXT,
    published_at TEXT,
    crawled_at TEXT NOT NULL,
    status TEXT DEFAULT 'raw'
);
CREATE INDEX IF NOT EXISTS idx_notices_status ON notices(status);
CREATE INDEX IF NOT EXISTS idx_notices_source ON notices(source);

CREATE TABLE IF NOT EXISTS crawl_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    total_discovered INTEGER,
    total_new INTEGER,
    total_skipped INTEGER,
    total_changed INTEGER,
    total_failed INTEGER,
    errors TEXT,
    crawled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduler_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL,          -- success / failed
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    failure_count INTEGER DEFAULT 0,  -- 该 job 本次运行时的连续失败次数
    message TEXT,
    details TEXT                      -- JSON 详情
);
CREATE INDEX IF NOT EXISTS idx_scheduler_log_job ON scheduler_log(job_name);
CREATE INDEX IF NOT EXISTS idx_scheduler_log_started ON scheduler_log(started_at);

CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id INTEGER NOT NULL,          -- 关联通知
    action TEXT NOT NULL,                -- 待办内容，如"在 X 前完成报名"
    due_at TEXT,                         -- 截止时间（复用 notice.deadline）
    priority TEXT DEFAULT 'normal',      -- high/normal/low
    status TEXT DEFAULT 'pending',       -- pending/done/skipped
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (notice_id) REFERENCES notices(id)
);
CREATE INDEX IF NOT EXISTS idx_todos_notice ON todos(notice_id);
CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
CREATE INDEX IF NOT EXISTS idx_todos_due ON todos(due_at);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id INTEGER NOT NULL,         -- 关联通知（恒非空：通知路与待办兜底路都带）
    todo_id INTEGER,                    -- 关联待办（有待办时挂上，可空，仅展示用）
    due_at TEXT NOT NULL,               -- 截止时间（复用 notice.deadline / todo.due_at）
    tier TEXT NOT NULL,                 -- 提醒档位：3d / 1d
    remind_on TEXT NOT NULL,            -- 触发日期 YYYY-MM-DD（幂等键：同一天同一对象同一档位不重复）
    status TEXT DEFAULT 'pending',      -- pending / read / ignored
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE(notice_id, tier, remind_on), -- 注意：todo_id 不能进唯一键（SQLite 将 NULL 视为互异）
    FOREIGN KEY (notice_id) REFERENCES notices(id),
    FOREIGN KEY (todo_id) REFERENCES todos(id)
);
CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at);

CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,                  -- extraction / qa / todo / embedding / test
    provider TEXT,                       -- 供应商名（多供应商阶段 7.2 起记录）
    model TEXT,
    notice_id INTEGER,                   -- 关联通知（提取任务），可空
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    success INTEGER DEFAULT 1,           -- 1 成功 / 0 失败
    retry_count INTEGER DEFAULT 0,       -- 该次调用是第几次尝试（0 表示首调）
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_usage_task ON token_usage(task);
CREATE INDEX IF NOT EXISTS idx_token_usage_notice ON token_usage(notice_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,               -- 订阅词
    notice_type TEXT,                    -- 关注的通知类型（NULL = 不限类型）
    enabled INTEGER DEFAULT 1,           -- 1 启用 / 0 停用
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_enabled ON subscriptions(enabled);

CREATE TABLE IF NOT EXISTS notice_subscription_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id INTEGER NOT NULL,          -- 关联通知
    subscription_id INTEGER NOT NULL,    -- 命中的订阅
    matched_at TEXT NOT NULL,
    UNIQUE(notice_id, subscription_id),
    FOREIGN KEY (notice_id) REFERENCES notices(id),
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);
CREATE INDEX IF NOT EXISTS idx_nsm_notice ON notice_subscription_matches(notice_id);
CREATE INDEX IF NOT EXISTS idx_nsm_subscription ON notice_subscription_matches(subscription_id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,          -- 事件类型：page_view / qa_ask / todo_generate / todo_done / service_button_click
    ref_id INTEGER,                    -- 关联对象 id（notice_id / todo_id / 服务 key），可空
    note TEXT,                         -- 备注（页面标识 / 问题文本 / 服务名），可空
    event_at TEXT NOT NULL             -- 事件时间（ISO）
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(event_at);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,                -- crawl_source / crawl_all / extract_batch /
                                       -- subscription_add / subscription_update / match_all /
                                       -- rebuild_index / generate_todos
    params_json TEXT,                  -- 请求参数 JSON
    status TEXT NOT NULL DEFAULT 'queued',  -- queued / running / success / failed
    progress REAL NOT NULL DEFAULT 0,  -- 0.0 ~ 1.0
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(type);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
"""

# M2 结构化提取新增列（对已存在的库做 ALTER 迁移）
_MIGRATIONS = [
    "ALTER TABLE notices ADD COLUMN notice_type TEXT",
    "ALTER TABLE notices ADD COLUMN target_audience TEXT",
    "ALTER TABLE notices ADD COLUMN signup_method TEXT",
    "ALTER TABLE notices ADD COLUMN signup_url TEXT",
    "ALTER TABLE notices ADD COLUMN location TEXT",
    "ALTER TABLE notices ADD COLUMN location_type TEXT",
    "ALTER TABLE notices ADD COLUMN deadline TEXT",
    "ALTER TABLE notices ADD COLUMN deadline_raw TEXT",
    "ALTER TABLE notices ADD COLUMN key_dates_json TEXT",
    "ALTER TABLE notices ADD COLUMN summary TEXT",
    "ALTER TABLE notices ADD COLUMN extracted_at TEXT",
    # 模块 1.2 内容指纹变更检测
    "ALTER TABLE notices ADD COLUMN content_hash TEXT",
    "ALTER TABLE crawl_log ADD COLUMN total_changed INTEGER",
    # 待办中心：备注列（编辑/延期后记进展）
    "ALTER TABLE todos ADD COLUMN notes TEXT",
    # 阶段 7 提取前置过滤：预筛跳过原因（非 NULL 表示不参与 LLM 提取）
    "ALTER TABLE notices ADD COLUMN extract_skipped_reason TEXT",
    # 阶段 7 Token 用量：记录调用供应商（多供应商候选列表 + 连通性测试）
    "ALTER TABLE token_usage ADD COLUMN provider TEXT",
]


def compute_content_hash(content: str) -> str:
    """计算正文内容指纹（SHA-256）。

    先折叠连续空白并去除首尾空白，避免网页重抓时由换行/空格差异造成的误报。
    """
    if not content:
        return ""
    normalized = re.sub(r"\s+", " ", content).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _migrate(conn: sqlite3.Connection) -> None:
    """对已存在的库补齐新增列（幂等），并为旧数据回填 content_hash。"""
    def _table_cols(table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    notices_cols = _table_cols("notices")
    crawl_log_cols = _table_cols("crawl_log")
    todos_cols = _table_cols("todos")
    token_usage_cols = _table_cols("token_usage")
    cols_by_table = {
        "notices": notices_cols,
        "crawl_log": crawl_log_cols,
        "todos": todos_cols,
        "token_usage": token_usage_cols,
    }
    for stmt in _MIGRATIONS:
        table = stmt.split("ALTER TABLE ")[1].split(" ")[0]
        col = stmt.split("ADD COLUMN ")[1].split(" ")[0]
        cols = cols_by_table.get(table, set())
        if col not in cols:
            conn.execute(stmt)

    # 回填已有正文的 content_hash，避免升级后首轮抓取触发全量"变更"
    if "content_hash" in notices_cols or "content_hash" in _table_cols("notices"):
        rows = conn.execute(
            """SELECT id, raw_content FROM notices
               WHERE (content_hash IS NULL OR content_hash = '')
                 AND raw_content IS NOT NULL AND raw_content != ''"""
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE notices SET content_hash = ? WHERE id = ?",
                (compute_content_hash(row["raw_content"]), row["id"]),
            )
    conn.commit()


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """获取 SQLite 连接，自动建库建表 + 迁移。

    并发加固（阶段 A）：check_same_thread=False 允许跨线程复用连接；
    timeout=30.0 在写锁竞争时等待而非立即抛 "database is locked"。
    """
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# 当前协程/任务上下文的专属连接（contextvars 缓存）。
_TASK_CONN: contextvars.ContextVar[Optional[sqlite3.Connection]] = contextvars.ContextVar(
    "task_conn", default=None
)


def get_task_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """获取当前任务上下文的专属连接（contextvars 缓存）。

    同一上下文内重复调用返回同一连接；异步并发（asyncio.create_task）下每个协程
    自动持有独立 context，天然实现"每协程专属连接"，避免全局连接共享冲突。
    任务结束后必须调用 close_task_connection() 释放。
    """
    conn = _TASK_CONN.get()
    if conn is None:
        conn = get_connection(db_path)
        _TASK_CONN.set(conn)
    return conn


def close_task_connection() -> None:
    """关闭并清空当前任务上下文的专属连接。"""
    conn = _TASK_CONN.get()
    if conn is not None:
        conn.close()
        _TASK_CONN.set(None)


def url_exists(conn: sqlite3.Connection, url: str) -> bool:
    """检查 URL 是否已存在（去重依据）。"""
    row = conn.execute("SELECT 1 FROM notices WHERE url = ?", (url,)).fetchone()
    return row is not None


def get_notice_by_url(conn: sqlite3.Connection, url: str) -> Optional[dict]:
    """按 URL 查询已有记录，返回 dict 或 None。

    返回字段包含 published_at / raw_content / content_hash，供内容指纹比较。
    """
    row = conn.execute(
        "SELECT id, published_at, raw_content, content_hash FROM notices WHERE url = ?",
        (url,),
    ).fetchone()
    return dict(row) if row else None


def get_notice_by_id(conn: sqlite3.Connection, notice_id: int) -> Optional[dict]:
    """按 ID 查询通知，返回 dict 或 None。"""
    row = conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone()
    return dict(row) if row else None


def update_notice_date(conn: sqlite3.Connection, url: str, published_at: str) -> bool:
    """更新已有记录的 published_at 字段。"""
    conn.execute("UPDATE notices SET published_at = ? WHERE url = ?", (published_at, url))
    conn.commit()
    return True


def update_notice_content(
    conn: sqlite3.Connection,
    url: str,
    title: str,
    raw_content: str,
    content_hash: str,
) -> bool:
    """正文变更：更新正文/标题/指纹/抓取时间，并重置 status='raw' 等待重新提取。

    同时清除 extract_skipped_reason（阶段 7：内容已变，预筛需重新判定）。
    """
    cur = conn.execute(
        """UPDATE notices SET
               title = ?,
               raw_content = ?,
               content_hash = ?,
               crawled_at = ?,
               status = 'raw',
               extract_skipped_reason = NULL
           WHERE url = ?""",
        (title, raw_content, content_hash, datetime.now().isoformat(), url),
    )
    conn.commit()
    return cur.rowcount > 0


def insert_notice(conn: sqlite3.Connection, record: NoticeRecord) -> bool:
    """插入一条通知，返回是否新增（False 表示已存在）。"""
    try:
        conn.execute(
            """INSERT INTO notices (url, source, title, raw_content, published_at, crawled_at, status, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.url,
                record.source,
                record.title,
                record.raw_content,
                record.published_at,
                record.crawled_at,
                record.status,
                record.content_hash,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # URL 已存在，跳过
        return False


def get_notices_by_status(
    conn: sqlite3.Connection,
    status: str,
    limit: int = 100,
    source: Optional[str] = None,
    exclude_prefiltered: bool = False,
) -> list[dict]:
    """按状态查询通知（断点续跑的游标）。

    Args:
        status: 游标状态，如 raw（待提取）/ failed（待重试）
        limit: 单批上限
        source: 可选来源过滤（在 SQL 内做，避免 LIMIT 先截断再过滤）
        exclude_prefiltered: 排除已预筛跳过的通知（extract_skipped_reason 非空）

    按 id 升序返回，保证游标单调推进：无论中断多少次，未完成项始终按
    相同顺序被取出，不会因排序抖动而重复或漏取。
    """
    prefilter_sql = " AND extract_skipped_reason IS NULL" if exclude_prefiltered else ""
    if source:
        rows = conn.execute(
            f"""SELECT * FROM notices
               WHERE status = ? AND source = ?{prefilter_sql}
               ORDER BY id ASC LIMIT ?""",
            (status, source, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT * FROM notices
               WHERE status = ?{prefilter_sql}
               ORDER BY id ASC LIMIT ?""",
            (status, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def update_extraction(
    conn: sqlite3.Connection,
    notice_id: int,
    extraction: dict,
    status: str,
) -> None:
    """更新通知的结构化提取结果。extraction 为 NoticeExtraction 的 dict。"""
    key_dates = extraction.get("key_dates") or []
    conn.execute(
        """UPDATE notices SET
               notice_type = ?,
               target_audience = ?,
               signup_method = ?,
               signup_url = ?,
               location = ?,
               location_type = ?,
               deadline = ?,
               deadline_raw = ?,
               key_dates_json = ?,
               summary = ?,
               status = ?,
               extracted_at = ?
           WHERE id = ?""",
        (
            extraction.get("notice_type"),
            extraction.get("target_audience"),
            extraction.get("signup_method"),
            extraction.get("signup_url"),
            extraction.get("location"),
            extraction.get("location_type"),
            extraction.get("deadline"),
            extraction.get("deadline_raw"),
            json.dumps(key_dates, ensure_ascii=False) if key_dates else None,
            extraction.get("summary"),
            status,
            datetime.now().isoformat(),
            notice_id,
        ),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, notice_id: int, error: str) -> None:
    """把通知标记为提取失败。"""
    conn.execute(
        "UPDATE notices SET status = 'failed', extracted_at = ? WHERE id = ?",
        (datetime.now().isoformat(), notice_id),
    )
    conn.commit()


def mark_prefiltered(conn: sqlite3.Connection, notice_id: int, reason: str) -> None:
    """记录提取预筛跳过原因（阶段 7）。状态保持 raw，跳过项不再参与提取。"""
    conn.execute(
        "UPDATE notices SET extract_skipped_reason = ? WHERE id = ?",
        (reason, notice_id),
    )
    conn.commit()


def clear_prefiltered(conn: sqlite3.Connection, notice_id: Optional[int] = None) -> int:
    """清除预筛跳过标记（全部或单条）。返回更新条数。"""
    if notice_id is None:
        cur = conn.execute("UPDATE notices SET extract_skipped_reason = NULL")
    else:
        cur = conn.execute(
            "UPDATE notices SET extract_skipped_reason = NULL WHERE id = ?",
            (notice_id,),
        )
    conn.commit()
    return cur.rowcount


def count_notices_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """按状态统计通知数量。"""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM notices GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


# ---------- 通知管理（M6 CRUD） ----------


def get_notice_ids_by_source(conn: sqlite3.Connection, source: str) -> list[int]:
    """返回某来源下所有通知 ID。"""
    rows = conn.execute("SELECT id FROM notices WHERE source = ?", (source,)).fetchall()
    return [r["id"] for r in rows]


def get_notice_ids_by_status(conn: sqlite3.Connection, status: str) -> list[int]:
    """返回某状态下所有通知 ID。"""
    rows = conn.execute("SELECT id FROM notices WHERE status = ?", (status,)).fetchall()
    return [r["id"] for r in rows]


def delete_notice(conn: sqlite3.Connection, notice_id: int) -> int:
    """删除单条通知及其关联待办、提醒与订阅命中。返回删除条数。"""
    delete_reminders_for_notice(conn, notice_id)
    conn.execute("DELETE FROM todos WHERE notice_id = ?", (notice_id,))
    conn.execute("DELETE FROM notice_subscription_matches WHERE notice_id = ?", (notice_id,))
    cur = conn.execute("DELETE FROM notices WHERE id = ?", (notice_id,))
    conn.commit()
    return cur.rowcount


def delete_notices_by_source(conn: sqlite3.Connection, source: str) -> tuple[list[int], int]:
    """按来源批量删除通知。返回 (被删 ID 列表, 删除条数)。"""
    ids = get_notice_ids_by_source(conn, source)
    for nid in ids:
        delete_reminders_for_notice(conn, nid)
        conn.execute("DELETE FROM todos WHERE notice_id = ?", (nid,))
        conn.execute("DELETE FROM notice_subscription_matches WHERE notice_id = ?", (nid,))
    cur = conn.execute("DELETE FROM notices WHERE source = ?", (source,))
    conn.commit()
    return ids, cur.rowcount


def delete_notices_by_status(conn: sqlite3.Connection, status: str) -> tuple[list[int], int]:
    """按状态批量删除通知。返回 (被删 ID 列表, 删除条数)。"""
    ids = get_notice_ids_by_status(conn, status)
    for nid in ids:
        delete_reminders_for_notice(conn, nid)
        conn.execute("DELETE FROM todos WHERE notice_id = ?", (nid,))
        conn.execute("DELETE FROM notice_subscription_matches WHERE notice_id = ?", (nid,))
    cur = conn.execute("DELETE FROM notices WHERE status = ?", (status,))
    conn.commit()
    return ids, cur.rowcount


def reset_notice_status(conn: sqlite3.Connection, notice_id: int, status: str = "raw") -> bool:
    """重置通知状态（用于重新提取），同时清除提取预筛跳过标记。"""
    cur = conn.execute(
        "UPDATE notices SET status = ?, extract_skipped_reason = NULL WHERE id = ?",
        (status, notice_id),
    )
    conn.commit()
    return cur.rowcount > 0


# ---------- 通知筛选条件（管理页批量操作 / 列表时间筛选共用） ----------


def _date_upper_bound(value: str) -> str:
    """把日期/时间字符串转成区间上界（含当天）：date-only 补齐到当天结束，ISO 原样。"""
    if value and len(value) == 10 and value[4] == "-":
        return value + "T23:59:59.999999"
    return value


def build_notice_where(f: dict) -> tuple[list[str], list]:
    """根据筛选条件字典构造 notices 表的 WHERE 子句与参数。

    支持的键：
      status / source / notice_type             等值过滤
      published_from / published_to             发布时间区间（含边界）
      published_before                          发布时间严格早于（清理预设用，不含当天）
      crawled_from / crawled_to                 抓取时间区间（含边界）
    """
    where: list[str] = []
    params: list = []

    if f.get("status"):
        where.append("status = ?")
        params.append(f["status"])
    if f.get("source"):
        where.append("source = ?")
        params.append(f["source"])
    if f.get("notice_type"):
        where.append("notice_type = ?")
        params.append(f["notice_type"])

    if f.get("published_from"):
        where.append("published_at >= ?")
        params.append(f["published_from"])
    if f.get("published_to"):
        where.append("published_at <= ?")
        params.append(_date_upper_bound(f["published_to"]))
    if f.get("published_before"):
        where.append("published_at < ?")
        params.append(f["published_before"])

    if f.get("crawled_from"):
        where.append("crawled_at >= ?")
        params.append(f["crawled_from"])
    if f.get("crawled_to"):
        where.append("crawled_at <= ?")
        params.append(_date_upper_bound(f["crawled_to"]))

    return where, params


def count_notices_by_filter(conn: sqlite3.Connection, f: dict) -> int:
    """按筛选条件统计通知数量。"""
    where, params = build_notice_where(f)
    sql = "SELECT COUNT(*) AS n FROM notices"
    if where:
        sql += " WHERE " + " AND ".join(where)
    row = conn.execute(sql, params).fetchone()
    return row["n"] if row else 0


def get_notice_ids_by_filter(conn: sqlite3.Connection, f: dict) -> list[int]:
    """返回命中筛选条件的通知 ID 列表。"""
    where, params = build_notice_where(f)
    sql = "SELECT id FROM notices"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    rows = conn.execute(sql, params).fetchall()
    return [r["id"] for r in rows]


def delete_notices_by_filter(conn: sqlite3.Connection, f: dict) -> tuple[list[int], int]:
    """按筛选条件批量删除通知（级联删提醒/待办/订阅命中）。返回 (被删 ID, 条数)。"""
    ids = get_notice_ids_by_filter(conn, f)
    for nid in ids:
        delete_reminders_for_notice(conn, nid)
        conn.execute("DELETE FROM todos WHERE notice_id = ?", (nid,))
        conn.execute("DELETE FROM notice_subscription_matches WHERE notice_id = ?", (nid,))
    where, params = build_notice_where(f)
    sql = "DELETE FROM notices"
    if where:
        sql += " WHERE " + " AND ".join(where)
    cur = conn.execute(sql, params)
    conn.commit()
    return ids, cur.rowcount


def reset_notices_by_filter(
    conn: sqlite3.Connection, f: dict, target_status: str = "raw"
) -> tuple[list[int], int]:
    """按筛选条件批量重置通知状态（供重新提取），同时清除预筛跳过标记。返回 (命中的 ID, 更新条数)。"""
    ids = get_notice_ids_by_filter(conn, f)
    if not ids:
        return [], 0
    where, params = build_notice_where(f)
    sql = "UPDATE notices SET status = ?, extract_skipped_reason = NULL"
    if where:
        sql += " WHERE " + " AND ".join(where)
    cur = conn.execute(sql, [target_status] + params)
    conn.commit()
    return ids, cur.rowcount


# ---------- todos（M3 待办） ----------


def insert_todo(
    conn: sqlite3.Connection,
    notice_id: int,
    action: str,
    due_at: Optional[str] = None,
    priority: str = "normal",
) -> int:
    """插入一条待办，返回新 id。"""
    cur = conn.execute(
        """INSERT INTO todos (notice_id, action, due_at, priority, status, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?)""",
        (notice_id, action, due_at, priority, datetime.now().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def get_todos(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    notice_id: Optional[int] = None,
) -> list[dict]:
    """查询待办，按截止时间升序（无截止的排在最后）。带通知标题与原文链接。"""
    where: list[str] = []
    params: list = []
    if status:
        where.append("t.status = ?")
        params.append(status)
    if notice_id is not None:
        where.append("t.notice_id = ?")
        params.append(notice_id)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""SELECT t.*, n.title AS notice_title, n.url AS notice_url, n.notice_type
            FROM todos t
            LEFT JOIN notices n ON n.id = t.notice_id
            {w}
            ORDER BY t.due_at IS NULL, t.due_at ASC, t.id ASC""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def set_todo_status(conn: sqlite3.Connection, todo_id: int, status: str) -> bool:
    """更新待办状态（pending/done/skipped）。done 时记录 completed_at。

    数据卫生（模块 3.2）：待办完成/跳过后，其关联的待处理提醒自动收敛为已读，
    避免红点因陈旧提醒滞留。
    """
    cur = conn.execute(
        """UPDATE todos SET status = ?,
               completed_at = CASE WHEN ? = 'done' THEN ? ELSE NULL END
           WHERE id = ?""",
        (status, status, datetime.now().isoformat(), todo_id),
    )
    if cur.rowcount > 0 and status in ("done", "skipped"):
        resolve_reminders_for_todo(conn, todo_id, status="read")
    conn.commit()
    return cur.rowcount > 0


def get_todo_by_id(conn: sqlite3.Connection, todo_id: int) -> Optional[dict]:
    """按 id 查询待办（带通知标题与原文链接），返回 dict 或 None。"""
    row = conn.execute(
        """SELECT t.*, n.title AS notice_title, n.url AS notice_url, n.notice_type
           FROM todos t
           LEFT JOIN notices n ON n.id = t.notice_id
           WHERE t.id = ?""",
        (todo_id,),
    ).fetchone()
    return dict(row) if row else None


def update_todo(
    conn: sqlite3.Connection,
    todo_id: int,
    action: object = _UNSET,
    due_at: object = _UNSET,
    notes: object = _UNSET,
) -> int:
    """更新待办部分字段（action / due_at / notes）。

    _UNSET（默认）表示不修改；显式 None 表示清空为 NULL。
    """
    sets: list[str] = []
    params: list = []
    if action is not _UNSET:
        sets.append("action = ?")
        params.append(action)
    if due_at is not _UNSET:
        sets.append("due_at = ?")
        params.append(due_at)
    if notes is not _UNSET:
        sets.append("notes = ?")
        params.append(notes)
    if not sets:
        return 0
    params.append(todo_id)
    cur = conn.execute(
        f"UPDATE todos SET {', '.join(sets)} WHERE id = ?", params
    )
    conn.commit()
    return cur.rowcount


def delete_todos_for_notice(
    conn: sqlite3.Connection,
    notice_id: int,
    status: Optional[str] = None,
) -> int:
    """删除某通知的待办（按需重新生成前调用，防重复）。返回删除条数。"""
    if status:
        cur = conn.execute(
            "DELETE FROM todos WHERE notice_id = ? AND status = ?",
            (notice_id, status),
        )
    else:
        cur = conn.execute("DELETE FROM todos WHERE notice_id = ?", (notice_id,))
    conn.commit()
    return cur.rowcount


# ---------- reminders（W3 模块 3.2 截止提醒） ----------


def insert_reminder(
    conn: sqlite3.Connection,
    notice_id: int,
    todo_id: Optional[int],
    due_at: str,
    tier: str,
    remind_on: str,
) -> bool:
    """插入一条提醒，返回是否新增（UNIQUE(notice_id, tier, remind_on) 幂等）。

    SQLite 将 UNIQUE 列中的 NULL 视为互异，因此唯一键只用恒非空的 notice_id，
    不能加入可空的 todo_id，否则无待办的提醒会失去幂等性。
    """
    cur = conn.execute(
        """INSERT OR IGNORE INTO reminders
           (notice_id, todo_id, due_at, tier, remind_on, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
        (notice_id, todo_id, due_at, tier, remind_on, datetime.now().isoformat()),
    )
    conn.commit()
    return cur.rowcount > 0


def get_reminders(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """查询提醒列表，带通知标题与待办动作文案，按截止时间升序。"""
    where: list[str] = []
    params: list = []
    if status:
        where.append("r.status = ?")
        params.append(status)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""SELECT r.*, n.title AS notice_title, n.source AS notice_source,
                     t.action AS todo_action
              FROM reminders r
              LEFT JOIN notices n ON n.id = r.notice_id
              LEFT JOIN todos t ON t.id = r.todo_id
              {w}
              ORDER BY r.due_at ASC, r.id ASC"""
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def set_reminder_status(
    conn: sqlite3.Connection, reminder_id: int, status: str
) -> bool:
    """更新提醒状态（pending/read/ignored）。read/ignored 时记录 read_at。"""
    cur = conn.execute(
        """UPDATE reminders SET status = ?,
               read_at = CASE WHEN ? IN ('read', 'ignored') THEN ? ELSE NULL END
           WHERE id = ?""",
        (status, status, datetime.now().isoformat(), reminder_id),
    )
    conn.commit()
    return cur.rowcount > 0


def count_reminders_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """按状态统计提醒数量。"""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM reminders GROUP BY status"
    ).fetchall()
    stats = {r["status"]: r["n"] for r in rows}
    return {
        "pending": stats.get("pending", 0),
        "read": stats.get("read", 0),
        "ignored": stats.get("ignored", 0),
        "total": sum(stats.values()),
    }


def count_pending_reminders(conn: sqlite3.Connection) -> int:
    """待处理提醒数（首页红点）。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM reminders WHERE status = 'pending'"
    ).fetchone()
    return row["n"] if row else 0


def resolve_reminders_for_todo(
    conn: sqlite3.Connection, todo_id: int, status: str = "read"
) -> int:
    """待办完成/跳过后，将其关联的待处理提醒收敛为已读。返回更新条数。"""
    cur = conn.execute(
        """UPDATE reminders SET status = ?,
               read_at = CASE WHEN ? = 'read' THEN ? ELSE NULL END
           WHERE todo_id = ? AND status = 'pending'""",
        (status, status, datetime.now().isoformat(), todo_id),
    )
    conn.commit()
    return cur.rowcount


def delete_reminders_for_notice(
    conn: sqlite3.Connection, notice_id: int
) -> int:
    """删除某通知的提醒（含悬挂在其待办上的提醒），级联清理用。"""
    cur = conn.execute(
        """DELETE FROM reminders
           WHERE notice_id = ? OR todo_id IN (
               SELECT id FROM todos WHERE notice_id = ?)""",
        (notice_id, notice_id),
    )
    conn.commit()
    return cur.rowcount


def delete_reminders_for_todo(conn: sqlite3.Connection, todo_id: int) -> int:
    """删除某待办的提醒。"""
    cur = conn.execute("DELETE FROM reminders WHERE todo_id = ?", (todo_id,))
    conn.commit()
    return cur.rowcount


def log_crawl(
    conn: sqlite3.Connection,
    source: str,
    total_discovered: int,
    total_new: int,
    total_skipped: int,
    total_failed: int,
    errors: list[str],
    total_changed: int = 0,
) -> None:
    """记录抓取日志。"""
    conn.execute(
        """INSERT INTO crawl_log (source, total_discovered, total_new, total_skipped, total_changed, total_failed, errors, crawled_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source,
            total_discovered,
            total_new,
            total_skipped,
            total_changed,
            total_failed,
            "\n".join(errors),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()


# ---------- 调度器（W1 模块 1.1） ----------


def log_scheduler_run(
    conn: sqlite3.Connection,
    job_name: str,
    status: str,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    failure_count: int,
    message: str = "",
    details: Optional[dict] = None,
) -> None:
    """记录一次调度 job 的运行结果（成功/失败、耗时、连续失败计数）。"""
    conn.execute(
        """INSERT INTO scheduler_log
           (job_name, status, started_at, finished_at, duration_ms, failure_count, message, details)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_name,
            status,
            started_at,
            finished_at,
            duration_ms,
            failure_count,
            message,
            json.dumps(details, ensure_ascii=False) if details else None,
        ),
    )
    conn.commit()


def get_recent_scheduler_log(
    conn: sqlite3.Connection, limit: int = 20
) -> list[dict]:
    """查询最近 N 条调度运行记录（重启后恢复运行状态用）。"""
    rows = conn.execute(
        """SELECT * FROM scheduler_log
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- token 计量表（W1 模块 1.4 地基） ----------


def log_llm_usage(
    conn: sqlite3.Connection,
    task: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    success: bool = True,
    retry_count: int = 0,
    error: Optional[str] = None,
    notice_id: Optional[int] = None,
    provider: Optional[str] = None,
) -> int:
    """记录一次 LLM 调用到 token 计量表，返回新 id。

    调用点统一在 LLM 返回处（如 core/extractor.py 的 _call），成功与失败都记账；
    retry_count 标记该次是第几次尝试，便于区分首调与重试。
    """
    cur = conn.execute(
        """INSERT INTO token_usage
           (task, provider, model, notice_id, input_tokens, output_tokens, success, retry_count, error, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            task,
            provider,
            model,
            notice_id,
            input_tokens,
            output_tokens,
            1 if success else 0,
            retry_count,
            error,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def count_token_usage_by_task(
    conn: sqlite3.Connection, task: str, days: Optional[int] = None
) -> dict:
    """按任务统计 token 计量：记录数、input/output 总量、成功/失败数。

    Args:
        task: extraction / qa / todo / embedding
        days: 只统计最近 N 天（None 表示全部）
    """
    where = "WHERE task = ?"
    params: list = [task]
    if days is not None:
        where += " AND created_at >= ?"
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        params.append(cutoff)

    row = conn.execute(
        f"""SELECT COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed
            FROM token_usage {where}""",
        params,
    ).fetchone()
    return {
        "task": task,
        "calls": row["calls"],
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "success": row["success"] or 0,
        "failed": row["failed"] or 0,
    }


def get_token_usage_summary(
    conn: sqlite3.Connection, days: int = 7
) -> dict:
    """近 N 天 token 计量汇总：按任务 × 供应商 × 模型分组 + 总计（供配置页展示）。

    Args:
        days: 统计最近 N 天

    Returns:
        rows: [{task, provider, model, calls, success, failed, retry_calls, input_tokens, output_tokens}]
        total: 上述各指标的合计
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT task,
                  COALESCE(provider, '') AS provider,
                  COALESCE(model, '') AS model,
                  COUNT(*) AS calls,
                  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success,
                  SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed,
                  COALESCE(SUM(retry_count), 0) AS retry_calls,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens
           FROM token_usage
           WHERE created_at >= ?
           GROUP BY task, provider, model
           ORDER BY task, provider, model""",
        (cutoff,),
    ).fetchall()

    items = []
    total = {
        "calls": 0,
        "success": 0,
        "failed": 0,
        "retry_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    for r in rows:
        item = dict(r)
        for k in total:
            total[k] += item[k] or 0
        items.append(item)
    return {"days": days, "rows": items, "total": total}


def get_all_notice_ids(conn: sqlite3.Connection) -> list[int]:
    """返回 SQLite 中全部通知 ID（幽灵向量判定的参照集，模块 2.5）。

    幽灵向量定义为"notice_id 已不存在于 SQLite 的通知向量"，因此参照集必须取
    全量存在 ID 而非"可索引"子集——通知处于 raw/failed 状态时其向量仍属有效内容，
    不应被当作残留误删。
    """
    rows = conn.execute("SELECT id FROM notices ORDER BY id").fetchall()
    return [r["id"] for r in rows]


def get_indexable_notice_ids(conn: sqlite3.Connection) -> list[int]:
    """返回"应当存在于向量索引"的通知 ID（已提取且有正文）。"""
    rows = conn.execute(
        """SELECT id FROM notices
           WHERE status IN ('extracted', 'partial')
             AND raw_content IS NOT NULL AND raw_content != ''
           ORDER BY id"""
    ).fetchall()
    return [r["id"] for r in rows]


# ---------- 订阅模型 + 命中关系（W3 模块 3.1） ----------


def create_subscription(
    conn: sqlite3.Connection,
    keyword: str,
    notice_type: Optional[str] = None,
    enabled: bool = True,
) -> int:
    """新增一条订阅，返回新 id。"""
    cur = conn.execute(
        """INSERT INTO subscriptions (keyword, notice_type, enabled, created_at)
           VALUES (?, ?, ?, ?)""",
        (keyword, notice_type, 1 if enabled else 0, datetime.now().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def update_subscription(
    conn: sqlite3.Connection,
    subscription_id: int,
    keyword: Optional[str] = None,
    notice_type: object = _UNSET,
    enabled: Optional[bool] = None,
) -> bool:
    """更新订阅（仅更新非 None / 非哨兵字段），返回是否找到。

    notice_type 用 _UNSET 哨兵区分"不修改"；传 None 表示清空类型过滤。
    """
    sets: list[str] = []
    params: list = []
    if keyword is not None:
        sets.append("keyword = ?")
        params.append(keyword)
    if notice_type is not _UNSET:
        sets.append("notice_type = ?")
        params.append(notice_type)
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)
    if not sets:
        return get_subscription_by_id(conn, subscription_id) is not None
    params.append(subscription_id)
    cur = conn.execute(
        f"UPDATE subscriptions SET {', '.join(sets)} WHERE id = ?", params
    )
    conn.commit()
    return cur.rowcount > 0


def delete_subscription(conn: sqlite3.Connection, subscription_id: int) -> int:
    """删除订阅及其全部命中关系，返回删除条数。"""
    conn.execute(
        "DELETE FROM notice_subscription_matches WHERE subscription_id = ?",
        (subscription_id,),
    )
    cur = conn.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
    conn.commit()
    return cur.rowcount


def get_subscription_by_id(
    conn: sqlite3.Connection, subscription_id: int
) -> Optional[dict]:
    """按 ID 查询订阅，返回 dict 或 None。"""
    row = conn.execute(
        "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
    ).fetchone()
    return dict(row) if row else None


def list_subscriptions(
    conn: sqlite3.Connection, enabled_only: bool = False
) -> list[dict]:
    """列出订阅，按 id 升序。enabled_only=True 只返回启用的。"""
    sql = "SELECT * FROM subscriptions"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id ASC"
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def insert_notice_subscription_match(
    conn: sqlite3.Connection, notice_id: int, subscription_id: int
) -> bool:
    """记录一条命中关系（幂等：重复插入被 UNIQUE 忽略），返回是否新增。"""
    cur = conn.execute(
        """INSERT OR IGNORE INTO notice_subscription_matches
           (notice_id, subscription_id, matched_at)
           VALUES (?, ?, ?)""",
        (notice_id, subscription_id, datetime.now().isoformat()),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_matches_for_notice(conn: sqlite3.Connection, notice_id: int) -> int:
    """删除某通知的全部命中关系（重新匹配前调用，防陈旧）。"""
    cur = conn.execute(
        "DELETE FROM notice_subscription_matches WHERE notice_id = ?", (notice_id,)
    )
    conn.commit()
    return cur.rowcount


def delete_matches_for_subscription(
    conn: sqlite3.Connection, subscription_id: int
) -> int:
    """删除某订阅的全部命中关系（订阅停用/修改后清理）。"""
    cur = conn.execute(
        "DELETE FROM notice_subscription_matches WHERE subscription_id = ?",
        (subscription_id,),
    )
    conn.commit()
    return cur.rowcount


def get_matches_for_notice(
    conn: sqlite3.Connection, notice_id: int
) -> list[dict]:
    """查询某通知命中的订阅列表（含订阅词）。"""
    rows = conn.execute(
        """SELECT m.notice_id, m.subscription_id, m.matched_at,
                  s.keyword, s.notice_type, s.enabled
           FROM notice_subscription_matches m
           LEFT JOIN subscriptions s ON s.id = m.subscription_id
           WHERE m.notice_id = ?
           ORDER BY m.id ASC""",
        (notice_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_matches_by_subscription(
    conn: sqlite3.Connection, subscription_id: int
) -> int:
    """统计某订阅的命中通知数。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM notice_subscription_matches WHERE subscription_id = ?",
        (subscription_id,),
    ).fetchone()
    return row["n"] if row else 0


def get_notice_rows_for_subscription(
    conn: sqlite3.Connection, subscription_id: int, page: int = 1, page_size: int = 20
) -> dict:
    """分页查询某订阅命中的通知（含全部通知字段）。

    Returns:
        {"items": [notice dict, ...], "total": int, "page": int, "page_size": int}
    """
    base = "FROM notice_subscription_matches m JOIN notices n ON n.id = m.notice_id"
    total = conn.execute(
        f"SELECT COUNT(*) AS n {base} WHERE m.subscription_id = ?",
        (subscription_id,),
    ).fetchone()["n"]
    offset = max(0, (page - 1) * page_size)
    rows = conn.execute(
        f"SELECT n.* {base} WHERE m.subscription_id = ?"
        " ORDER BY n.crawled_at DESC, n.id DESC LIMIT ? OFFSET ?",
        (subscription_id, page_size, offset),
    ).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_matched_notice_ids(conn: sqlite3.Connection) -> list[int]:
    """返回全部有命中关系的通知 ID（去重）。"""
    rows = conn.execute(
        "SELECT DISTINCT notice_id FROM notice_subscription_matches ORDER BY notice_id"
    ).fetchall()
    return [r["notice_id"] for r in rows]


def get_subscription_stats(conn: sqlite3.Connection) -> dict:
    """订阅统计：订阅总数、启用数、命中总数。"""
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled
           FROM subscriptions"""
    ).fetchone()
    match_row = conn.execute(
        "SELECT COUNT(*) AS n FROM notice_subscription_matches"
    ).fetchone()
    return {
        "total": row["total"] or 0,
        "enabled": row["enabled"] or 0,
        "matches": match_row["n"] or 0,
    }


# ---------- 事件埋点（W4 模块 4.1） ----------


def insert_event(
    conn: sqlite3.Connection,
    event_type: str,
    ref_id: Optional[int] = None,
    note: Optional[str] = None,
) -> int:
    """写入一条埋点事件，返回新 id。events 为纯追加日志，无 FK 无级联。"""
    cur = conn.execute(
        """INSERT INTO events (event_type, ref_id, note, event_at)
           VALUES (?, ?, ?, ?)""",
        (event_type, ref_id, note, datetime.now().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def count_events_by_type(
    conn: sqlite3.Connection, event_type: str, days: Optional[int] = None
) -> int:
    """按事件类型计数（可选只统计最近 N 天）。"""
    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE event_type = ? AND event_at >= ?",
            (event_type, cutoff),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE event_type = ?", (event_type,)
        ).fetchone()
    return row["n"] if row else 0


def get_event_stats(
    conn: sqlite3.Connection, days: Optional[int] = None
) -> dict[str, int]:
    """按事件类型统计数量（可选只统计最近 N 天）。"""
    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """SELECT event_type, COUNT(*) AS n FROM events
               WHERE event_at >= ? GROUP BY event_type ORDER BY event_type""",
            (cutoff,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT event_type, COUNT(*) AS n FROM events
               GROUP BY event_type ORDER BY event_type"""
        ).fetchall()
    stats = {r["event_type"]: r["n"] for r in rows}
    stats["total"] = sum(stats.values())
    return stats


def get_recent_events(
    conn: sqlite3.Connection, limit: int = 100, event_type: Optional[str] = None
) -> list[dict]:
    """查询最近 N 条埋点事件，按时间倒序（可选按类型过滤）。"""
    sql = "SELECT * FROM events"
    params: list = []
    if event_type:
        sql += " WHERE event_type = ?"
        params.append(event_type)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------- 每日体检（模块 4.2） ----------


def get_crawl_log_stats(
    conn: sqlite3.Connection, start: str, end: str
) -> dict:
    """窗口内抓取统计（每日体检：抓取成功率）。

    主口径：success_rate = (Σdiscovered − Σfailed) / Σdiscovered。
    附加口径：来源级失败（errors 非空或整源抓取异常）数量，用于展示。

    Args:
        start: 窗口起始（ISO 字符串，含）
        end: 窗口结束（ISO 字符串，不含）
    """
    rows = conn.execute(
        """SELECT source,
                  COALESCE(total_discovered, 0) AS discovered,
                  COALESCE(total_failed, 0) AS failed,
                  COALESCE(errors, '') AS errors,
                  crawled_at
           FROM crawl_log
           WHERE crawled_at >= ? AND crawled_at < ?
           ORDER BY crawled_at""",
        (start, end),
    ).fetchall()
    per_source: dict[str, dict] = {}
    for r in rows:
        name = r["source"]
        item = per_source.setdefault(
            name,
            {"source": name, "discovered": 0, "failed": 0, "errors": 0, "runs": 0},
        )
        item["discovered"] += r["discovered"]
        item["failed"] += r["failed"]
        item["runs"] += 1
        if r["errors"] and r["errors"].strip():
            item["errors"] += 1

    attempted = sum(v["discovered"] for v in per_source.values())
    failed = sum(v["failed"] for v in per_source.values())
    return {
        "attempted": attempted,
        "failed": failed,
        "success_rate": (attempted - failed) / attempted if attempted > 0 else None,
        "sources_total": len(per_source),
        "sources_with_errors": sum(1 for v in per_source.values() if v["errors"] > 0),
        "per_source": sorted(per_source.values(), key=lambda v: v["source"]),
    }


def get_extraction_status_stats(
    conn: sqlite3.Connection, start: str, end: str
) -> dict:
    """窗口内提取结果统计（每日体检：提取失败率）。

    主口径：failure_rate = failed / (extracted + partial + failed)。
    统计对象是 extracted_at 落在窗口内的通知。

    Args:
        start: 窗口起始（ISO 字符串，含）
        end: 窗口结束（ISO 字符串，不含）
    """
    rows = conn.execute(
        """SELECT status, COUNT(*) AS n FROM notices
           WHERE extracted_at >= ? AND extracted_at < ?
           GROUP BY status""",
        (start, end),
    ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    extracted = counts.get("extracted", 0)
    partial = counts.get("partial", 0)
    failed = counts.get("failed", 0)
    total = extracted + partial + failed
    return {
        "total": total,
        "extracted": extracted,
        "partial": partial,
        "failed": failed,
        "failure_rate": failed / total if total > 0 else None,
    }


def get_token_usage_stats(
    conn: sqlite3.Connection, start: str, end: str
) -> dict:
    """窗口内 token 计量统计（每日体检：token 消耗），按任务 × 模型分组 + 总计。"""
    rows = conn.execute(
        """SELECT task,
                  COALESCE(model, '') AS model,
                  COUNT(*) AS calls,
                  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success,
                  SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed,
                  COALESCE(SUM(retry_count), 0) AS retry_calls,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens
           FROM token_usage
           WHERE created_at >= ? AND created_at < ?
           GROUP BY task, model
           ORDER BY task, model""",
        (start, end),
    ).fetchall()
    items = []
    total = {
        "calls": 0,
        "success": 0,
        "failed": 0,
        "retry_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    for r in rows:
        item = dict(r)
        for k in total:
            total[k] += item[k] or 0
        items.append(item)
    return {"rows": items, "total": total}


def get_scheduler_failed_runs(
    conn: sqlite3.Connection, start: str, end: str
) -> list[dict]:
    """窗口内调度 job 失败记录（每日体检：异常日志）。"""
    rows = conn.execute(
        """SELECT id, job_name, status, failure_count, started_at, finished_at,
                  message, details
           FROM scheduler_log
           WHERE started_at >= ? AND started_at < ? AND status = 'failed'
           ORDER BY id""",
        (start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def get_scheduler_runs(
    conn: sqlite3.Connection, job_name: str, start: str, end: str
) -> list[dict]:
    """窗口内某 job 的全部运行记录（每日体检：运行连续性用）。"""
    rows = conn.execute(
        """SELECT id, job_name, status, started_at, finished_at, duration_ms
           FROM scheduler_log
           WHERE job_name = ? AND started_at >= ? AND started_at < ?
           ORDER BY id""",
        (job_name, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def get_run_gaps(
    conn: sqlite3.Connection, job_name: str, start: str, end: str
) -> dict:
    """窗口内某 job 的运行时间序列与相邻缺口（分钟）。

    用于证明"无人干预连续运行"：返回运行数、首末时间、最大相邻缺口。
    缺口 = 两次相邻运行的 started_at 间隔分钟数。
    """
    runs = get_scheduler_runs(conn, job_name, start, end)
    if not runs:
        return {"runs": 0, "first": None, "last": None, "max_gap_minutes": None}

    def _ts(iso: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(iso)
        except (ValueError, TypeError):
            return None

    timestamps = [t for r in runs if (t := _ts(r["started_at"])) is not None]
    gaps = [
        int((timestamps[i] - timestamps[i - 1]).total_seconds() // 60)
        for i in range(1, len(timestamps))
    ]
    return {
        "runs": len(runs),
        "first": runs[0]["started_at"],
        "last": runs[-1]["started_at"],
        "max_gap_minutes": max(gaps) if gaps else None,
    }


def get_billing_snapshot(
    conn: sqlite3.Connection, task: str = "extraction"
) -> dict[int, int]:
    """崩溃恢复演练：某任务按 notice_id 的计费记录数快照。"""
    rows = conn.execute(
        """SELECT notice_id, COUNT(*) AS c FROM token_usage
           WHERE task = ? AND notice_id IS NOT NULL
           GROUP BY notice_id""",
        (task,),
    ).fetchall()
    return {r["notice_id"]: r["c"] for r in rows}


def get_notice_status_snapshot(conn: sqlite3.Connection) -> dict[int, str]:
    """崩溃恢复演练：全部通知的当前状态快照（判断"已处理"集合）。"""
    rows = conn.execute("SELECT id, status FROM notices").fetchall()
    return {r["id"]: r["status"] for r in rows}


# ---------- 异步任务（阶段 4） ----------


def create_task(conn: sqlite3.Connection, task_type: str, params: Optional[dict] = None) -> int:
    """写入一条 queued 任务，返回新 id。"""
    now = datetime.now().isoformat()
    cur = conn.execute(
        """INSERT INTO tasks (type, params_json, status, progress, created_at, updated_at)
           VALUES (?, ?, 'queued', 0, ?, ?)""",
        (task_type, json.dumps(params, ensure_ascii=False) if params else None, now, now),
    )
    conn.commit()
    return cur.lastrowid


def claim_next_task(conn: sqlite3.Connection) -> Optional[dict]:
    """取出最早一条 queued 任务并置为 running（原子认领）。

    用 `UPDATE ... WHERE status='queued'` 的 rowcount 保证同一任务只被认领一次；
    无任务返回 None。
    """
    row = conn.execute(
        "SELECT * FROM tasks WHERE status = 'queued' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    cur = conn.execute(
        "UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ? AND status = 'queued'",
        (datetime.now().isoformat(), row["id"]),
    )
    conn.commit()
    if cur.rowcount == 0:
        return claim_next_task(conn)
    task = dict(row)
    task["params"] = _load_task_json(task.get("params_json"))
    return task


def update_task_progress(conn: sqlite3.Connection, task_id: int, progress: float) -> None:
    """更新任务进度（0.0 ~ 1.0）。"""
    conn.execute(
        "UPDATE tasks SET progress = ?, updated_at = ? WHERE id = ?",
        (max(0.0, min(1.0, progress)), datetime.now().isoformat(), task_id),
    )
    conn.commit()


def complete_task(conn: sqlite3.Connection, task_id: int, result: dict) -> None:
    """标记任务成功并写入结果。"""
    conn.execute(
        """UPDATE tasks SET status = 'success', progress = 1.0, result_json = ?, updated_at = ?
           WHERE id = ?""",
        (json.dumps(result, ensure_ascii=False), datetime.now().isoformat(), task_id),
    )
    conn.commit()


def fail_task(conn: sqlite3.Connection, task_id: int, error: str) -> None:
    """标记任务失败并写入错误。"""
    conn.execute(
        "UPDATE tasks SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
        (error, datetime.now().isoformat(), task_id),
    )
    conn.commit()


def recover_interrupted_tasks(conn: sqlite3.Connection) -> int:
    """进程重启恢复：把遗留的 queued / running 任务标记为 failed。

    任务记录保留（结果可查），底层操作幂等，用户可重新提交；
    error 用固定文案标记为"进程重启中断"。
    """
    cur = conn.execute(
        """UPDATE tasks SET status = 'failed',
               error = '进程重启中断，任务未完成，请重新提交',
               updated_at = ?
           WHERE status IN ('queued', 'running')""",
        (datetime.now().isoformat(),),
    )
    conn.commit()
    return cur.rowcount


def get_task(conn: sqlite3.Connection, task_id: int) -> Optional[dict]:
    """按 ID 查询任务，返回 dict（含解析后的 params / result）或 None。"""
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    task = dict(row)
    task["params"] = _load_task_json(task.get("params_json"))
    task["result"] = _load_task_json(task.get("result_json"))
    return task


def list_tasks(
    conn: sqlite3.Connection, limit: int = 50, status: Optional[str] = None
) -> list[dict]:
    """查询最近 N 条任务（可按状态过滤），按 id 倒序。"""
    sql = "SELECT * FROM tasks"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    tasks = []
    for r in rows:
        task = dict(r)
        task["params"] = _load_task_json(task.get("params_json"))
        task["result"] = _load_task_json(task.get("result_json"))
        tasks.append(task)
    return tasks


def _load_task_json(raw: Optional[str]) -> Optional[dict]:
    """解析任务表的 JSON 列；损坏时返回 None（不抛出，保持查询可用）。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None