"""订阅相关服务：订阅 CRUD + 确定性匹配引擎（W3 模块 3.1）。

设计原则：
  - 匹配引擎为纯函数（不依赖 LLM/网络），对通知的 标题/摘要 做子串匹配，
    通知类型为可选的精确过滤条件——符合"能用规则不用 AI"边界。
  - 命中关系写入 notice_subscription_matches 表（UNIQUE 保证幂等）。
  - 抓取/提取路径通过 match_notice() 增量维护；订阅增删改后通过
    match_all_notices() 全库回填，保证"新增订阅词后库中已有通知也被标记"。
"""
from __future__ import annotations

import logging
from typing import Optional

from core.models import NoticeType
from storage.db import (
    _UNSET,
    count_matches_by_subscription,
    create_subscription,
    delete_matches_for_notice,
    delete_matches_for_subscription,
    delete_subscription,
    get_connection,
    get_matched_notice_ids,
    get_matches_for_notice,
    get_notice_by_id,
    get_notice_rows_for_subscription,
    get_subscription_by_id,
    get_subscription_stats,
    insert_notice_subscription_match,
    list_subscriptions,
    update_subscription,
)

logger = logging.getLogger(__name__)

NOTICE_TYPE_LABELS: dict[str, str] = {
    "competition": "竞赛",
    "lecture": "讲座",
    "registration": "报名/选课/培训",
    "scholarship": "奖学金",
    "administrative": "行政事务",
    "recruitment": "招聘/实习",
    "policy": "政策/资讯",
    "result": "结果公示",
    "news": "动态/新闻",
    "other": "其他",
}


# ---------- 确定性匹配引擎（纯函数） ----------


def _substring_match(text: Optional[str], keyword: str) -> bool:
    """大小写不敏感的子串匹配（英文大小写不敏感，中文按字面）。"""
    if not text:
        return False
    return keyword.casefold() in text.casefold()


def matches_subscription(notice: dict, sub: dict) -> bool:
    """判定一条通知是否命中一条订阅。

    规则（全部满足）：
      1. 订阅处于启用状态（enabled = 1）
      2. 订阅限定类型时，通知类型必须完全相等；未限定时不设限
      3. 订阅词是通知标题或摘要的子串（大小写不敏感）
    """
    if not sub.get("enabled"):
        return False
    sub_type = sub.get("notice_type")
    if sub_type and notice.get("notice_type") != sub_type:
        return False
    keyword = (sub.get("keyword") or "").strip()
    if not keyword:
        return False
    return _substring_match(notice.get("title"), keyword) or _substring_match(
        notice.get("summary"), keyword
    )


def find_matching_subscriptions(notice: dict) -> list[dict]:
    """返回通知命中的所有启用订阅（不含停用的）。"""
    conn = get_connection()
    try:
        subs = list_subscriptions(conn, enabled_only=True)
    finally:
        conn.close()
    return [s for s in subs if matches_subscription(notice, s)]


def preview_subscription_matches(
    keyword: str,
    notice_type: Optional[str] = None,
    enabled: bool = True,
    sample_limit: int = 5,
) -> dict:
    """纯预览：统计按当前规则该订阅会命中库中多少条通知（只读，不写库）。

    规则与 matches_subscription 完全一致，因此预览结果与 add_subscription /
    update_subscription_record 回填后的一致（确定性），供两步式交互第一步展示
    影响面：新增/修改订阅前先告诉用户「会命中 N 条通知」并列出样例标题。
    """
    sub = {"keyword": keyword, "notice_type": notice_type, "enabled": enabled}
    matched = 0
    samples: list[str] = []
    sample_ids: list[int] = []
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, notice_type, summary FROM notices"
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        if matches_subscription(dict(r), sub):
            matched += 1
            if len(samples) < sample_limit:
                samples.append(r["title"])
                sample_ids.append(r["id"])
    return {
        "matched": matched,
        "total": len(rows),
        "samples": samples,
        "sample_ids": sample_ids,
    }


# ---------- 匹配维护 ----------


def match_notice(notice_id: int) -> dict:
    """对单条通知执行匹配：先清旧命中，再按当前启用订阅重算写入。

    抓取插入、内容变更、提取成功、重新提取后调用，保证命中关系与订阅
    配置保持一致（幂等且无陈旧命中）。
    """
    conn = get_connection()
    try:
        notice = get_notice_by_id(conn, notice_id)
        if not notice:
            return {"ok": False, "error": f"通知不存在 notice_id={notice_id}"}
        subs = list_subscriptions(conn, enabled_only=True)
        delete_matches_for_notice(conn, notice_id)
        matched = []
        for sub in subs:
            if matches_subscription(notice, sub):
                insert_notice_subscription_match(conn, notice_id, sub["id"])
                matched.append({"id": sub["id"], "keyword": sub["keyword"]})
        return {"ok": True, "notice_id": notice_id, "matched": matched}
    finally:
        conn.close()


def match_all_notices(progress_cb=None) -> dict:
    """全库回填：对所有通知按当前启用订阅重新匹配。

    新增/修改订阅后调用；重复执行幂等（delete-then-insert 语义）。

    Args:
        progress_cb: 可选进度回调 (done:int, total:int) -> None，供任务管理器上报进度。
    """
    conn = get_connection()
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM notices ORDER BY id").fetchall()]
    finally:
        conn.close()

    matched_notices = 0
    total_matches = 0
    total = len(ids)
    for i, nid in enumerate(ids, start=1):
        if progress_cb is not None:
            progress_cb(i, total)
        result = match_notice(nid)
        if result.get("ok"):
            total_matches += len(result.get("matched", []))
            if result.get("matched"):
                matched_notices += 1
    return {
        "ok": True,
        "notices": len(ids),
        "matched_notices": matched_notices,
        "total_matches": total_matches,
    }


# ---------- 订阅 CRUD ----------


def _validate_keyword(keyword: str) -> Optional[str]:
    kw = (keyword or "").strip()
    if not kw:
        return "订阅词不能为空"
    return None


def validate_subscription_input(
    keyword: Optional[str], notice_type: Optional[str] = None
) -> Optional[str]:
    """校验订阅输入（订阅词 + 通知类型），合法返回 None，非法返回错误文案。

    供路由同步校验（400 立即返回，不进入异步任务），语义与 add_subscription /
    update_subscription_record 内部校验完全一致。
    keyword 传 None 表示不修改订阅词（更新场景只改类型时跳过关键词校验）。
    """
    if keyword is not None:
        err = _validate_keyword(keyword)
        if err:
            return err
    notice_type = notice_type or None
    if notice_type and notice_type not in NoticeType.__args__:
        return f"未知通知类型: {notice_type}"
    return None


def add_subscription(
    keyword: str,
    notice_type: Optional[str] = None,
    enabled: bool = True,
    progress_cb=None,
) -> dict:
    """新增订阅并全库回填匹配。

    Args:
        progress_cb: 可选进度回调 (done:int, total:int) -> None，透传给 match_all_notices。
    """
    err = _validate_keyword(keyword)
    if err:
        return {"ok": False, "error": err}
    notice_type = notice_type or None
    if notice_type and notice_type not in NoticeType.__args__:
        return {"ok": False, "error": f"未知通知类型: {notice_type}"}

    conn = get_connection()
    try:
        sub_id = create_subscription(conn, keyword.strip(), notice_type, enabled)
    finally:
        conn.close()

    backfill = (
        match_all_notices(progress_cb=progress_cb)
        if enabled
        else {"ok": True, "notices": 0}
    )
    return {
        "ok": True,
        "id": sub_id,
        "keyword": keyword.strip(),
        "notice_type": notice_type,
        "enabled": enabled,
        "backfill": backfill,
    }


def update_subscription_record(
    subscription_id: int,
    keyword: Optional[str] = None,
    notice_type: object = _UNSET,
    enabled: Optional[bool] = None,
    progress_cb=None,
) -> dict:
    """更新订阅并重算其命中关系。

    notice_type 传 _UNSET（默认）表示不修改；传 None 表示清空类型过滤（全部类型）。

    Args:
        progress_cb: 可选进度回调 (done:int, total:int) -> None，透传给 match_all_notices。
    """
    conn = get_connection()
    try:
        current = get_subscription_by_id(conn, subscription_id)
        if not current:
            return {"ok": False, "error": "订阅不存在"}
        if keyword is not None:
            err = _validate_keyword(keyword)
            if err:
                return {"ok": False, "error": err}
            keyword = keyword.strip()
        if notice_type is not _UNSET:
            notice_type = notice_type or None
            if notice_type and notice_type not in NoticeType.__args__:
                return {"ok": False, "error": f"未知通知类型: {notice_type}"}

        new_enabled = enabled if enabled is not None else bool(current["enabled"])
        update_subscription(
            conn, subscription_id, keyword=keyword, notice_type=notice_type, enabled=enabled
        )
        # 清掉该订阅的旧命中，再决定是否全库重算
        delete_matches_for_subscription(conn, subscription_id)
    finally:
        conn.close()

    backfill = None
    if new_enabled:
        backfill = match_all_notices(progress_cb=progress_cb)
    return {"ok": True, "id": subscription_id, "backfill": backfill}


def toggle_subscription(subscription_id: int, enabled: bool) -> dict:
    """启用/停用订阅。停用清理旧命中；启用后全库回填。"""
    return update_subscription_record(subscription_id, enabled=enabled)


def delete_subscription_record(subscription_id: int) -> dict:
    """删除订阅及其命中关系。"""
    conn = get_connection()
    try:
        count = delete_subscription(conn, subscription_id)
    finally:
        conn.close()
    return {"ok": count > 0, "deleted": count}


# ---------- 查询（UI 展示） ----------


def get_subscriptions_for_ui() -> list[dict]:
    """订阅列表（含各自命中数），供管理页展示。"""
    conn = get_connection()
    try:
        subs = list_subscriptions(conn)
        for s in subs:
            s["match_count"] = count_matches_by_subscription(conn, s["id"])
            s["type_label"] = NOTICE_TYPE_LABELS.get(s["notice_type"], s["notice_type"] or "")
        return subs
    finally:
        conn.close()


def get_subscription_stats_ui() -> dict:
    """订阅统计：总数/启用数/命中总数 + 全库通知数（口径参照）。"""
    conn = get_connection()
    try:
        stats = get_subscription_stats(conn)
        row = conn.execute("SELECT COUNT(*) AS n FROM notices").fetchone()
    finally:
        conn.close()
    stats["total_notices"] = int(row["n"])
    return stats


def get_subscription_record(subscription_id: int) -> Optional[dict]:
    """按 ID 查询订阅（供路由同步校验 404，避免误提交任务）。"""
    conn = get_connection()
    try:
        return get_subscription_by_id(conn, subscription_id)
    finally:
        conn.close()


def count_all_notices() -> int:
    """返回库中通知总数（重匹配影响面预览用）。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM notices").fetchone()
        return int(row["n"])
    finally:
        conn.close()


def get_matched_notices_for_subscription(
    subscription_id: int, page: int = 1, page_size: int = 20
) -> Optional[dict]:
    """分页返回某订阅命中的通知列表（含全部通知字段）。

    订阅不存在返回 None（路由据此 404）；否则返回
    {"items": [...], "total": int, "page": int, "page_size": int}。
    """
    conn = get_connection()
    try:
        if get_subscription_by_id(conn, subscription_id) is None:
            return None
        return get_notice_rows_for_subscription(conn, subscription_id, page, page_size)
    finally:
        conn.close()


def get_matched_notice_ids_set() -> set[int]:
    """返回全部有命中关系的通知 ID 集合（浏览页筛选开关用）。"""
    conn = get_connection()
    try:
        return set(get_matched_notice_ids(conn))
    finally:
        conn.close()


def get_match_map(notice_ids: list[int]) -> dict[int, list[str]]:
    """按通知 ID 批量查询命中订阅词，返回 {notice_id: [keyword, ...]}。

    只包含仍启用的订阅（停用订阅的命中会在停用时被清理，此处双保险过滤）。
    """
    if not notice_ids:
        return {}
    result: dict[int, list[str]] = {}
    conn = get_connection()
    try:
        for nid in notice_ids:
            matches = get_matches_for_notice(conn, nid)
            keywords = [
                m["keyword"]
                for m in matches
                if m.get("enabled") and m.get("keyword")
            ]
            if keywords:
                result[nid] = keywords
    finally:
        conn.close()
    return result


def get_notice_types() -> list[str]:
    """返回可订阅的通知类型列表（来自 core.models.NoticeType）。"""
    return list(NoticeType.__args__)
