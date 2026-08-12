"""W3 模块 3.1 订阅模型 + 规则引擎的验收验证（离线、不依赖 LLM/网络）。

覆盖验收信号：
  1. 新增订阅词后，库中含该词的通知被标记「命中订阅」，命中关系可查；
  2. 新抓取含词通知被标记（标题子串匹配，抓取路径）；
  3. 提取后摘要含词也被标记（摘要匹配）；
  4. 类型过滤：订阅限定类型时仅匹配该类型通知；
  5. 大小写不敏感（英文子串）；
  6. 停用订阅不产生命中，停用后旧命中被清理；
  7. 幂等：重复匹配不产生重复命中关系；
  8. 删除通知级联清理命中关系；
  9. 订阅修改（改词/改类型/清空类型）后陈旧命中清除、新命中建立。

用法：python test_subscription.py
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import storage.db
from crawler.web_crawler import _match_notice_by_url
from storage.db import (
    delete_notice,
    delete_notices_by_source,
    get_connection,
    get_matched_notice_ids,
    get_matches_for_notice,
    get_subscription_stats,
    insert_notice,
    update_extraction,
)
from storage.models import NoticeRecord
from services.subscription_service import (
    add_subscription,
    delete_subscription_record,
    find_matching_subscriptions,
    get_match_map,
    get_matched_notice_ids_set,
    get_subscriptions_for_ui,
    get_subscription_stats_ui,
    match_all_notices,
    match_notice,
    matches_subscription,
    toggle_subscription,
    update_subscription_record,
)

TMP_DB = Path(__file__).parent / "data" / "test_subscription.db"

storage.db.DB_PATH = TMP_DB


def reset_db():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


def insert_notice_sql(
    conn, url, title, summary=None, notice_type=None, status="extracted"
):
    conn.execute(
        """INSERT INTO notices
           (url, source, title, raw_content, published_at, crawled_at, status, notice_type, summary)
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
            summary,
        ),
    )
    conn.commit()
    return conn.execute("SELECT id FROM notices WHERE url = ?", (url,)).fetchone()["id"]


def match_rows(conn, notice_id):
    return conn.execute(
        "SELECT m.subscription_id, s.keyword FROM notice_subscription_matches m "
        "JOIN subscriptions s ON s.id = m.subscription_id WHERE m.notice_id = ?",
        (notice_id,),
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
    sub_cols = {r[1] for r in conn.execute("PRAGMA table_info(subscriptions)")}
    check(
        "subscriptions 列齐全",
        {"keyword", "notice_type", "enabled", "created_at"} <= sub_cols,
        f"cols={sorted(sub_cols)}",
    )
    m_cols = {r[1] for r in conn.execute("PRAGMA table_info(notice_subscription_matches)")}
    check(
        "notice_subscription_matches 列齐全",
        {"notice_id", "subscription_id", "matched_at"} <= m_cols,
        f"cols={sorted(m_cols)}",
    )
    conn.close()

    # ---------- Part A：纯匹配引擎 ----------
    print("\n== A. 确定性匹配引擎（纯函数，不用 LLM） ==")
    notice_math = {"title": "第十六届数学建模大赛报名通知", "summary": None, "notice_type": "competition"}
    check("标题子串命中：数学建模", matches_subscription(notice_math, {"enabled": 1, "notice_type": None, "keyword": "数学建模"}))
    check("英文大小写不敏感：China/china", matches_subscription({"title": "China Daily 报名", "summary": None, "notice_type": None}, {"enabled": 1, "notice_type": None, "keyword": "china"}))
    check("摘要子串命中：summary 含奖学金", matches_subscription({"title": "通知", "summary": "国家奖学金评定", "notice_type": None}, {"enabled": 1, "notice_type": None, "keyword": "奖学金"}))
    check("类型过滤：指定类型不匹配其他类型", not matches_subscription({"title": "数学建模", "summary": None, "notice_type": "lecture"}, {"enabled": 1, "notice_type": "competition", "keyword": "数学建模"}))
    check("类型过滤：指定类型命中同类型", matches_subscription({"title": "数学建模", "summary": None, "notice_type": "competition"}, {"enabled": 1, "notice_type": "competition", "keyword": "数学建模"}))
    check("停用订阅不命中", not matches_subscription(notice_math, {"enabled": 0, "notice_type": None, "keyword": "数学建模"}))
    check("空订阅词不命中", not matches_subscription(notice_math, {"enabled": 1, "notice_type": None, "keyword": "  "}))
    check("空标题/摘要不命中", not matches_subscription({"title": None, "summary": None, "notice_type": None}, {"enabled": 1, "notice_type": None, "keyword": "x"}))

    # ---------- Part B：新增订阅回填全库，命中关系可查 ----------
    print("\n== B. 新增订阅词 → 全库回填命中，库中可查关系 ==")
    reset_db()
    conn = get_connection()
    n_math = insert_notice_sql(conn, "https://t/1", "第十七届数学建模大赛报名通知")
    n_scholar = insert_notice_sql(conn, "https://t/2", "关于开展评选的通知", summary="请符合国家奖学金条件的同学申报")
    n_other = insert_notice_sql(conn, "https://t/3", "校运会志愿者招募")
    conn.close()

    r = add_subscription("数学建模")
    check("add_subscription 成功", r["ok"], f"{r}")
    check("回填后含词通知被标记", r["backfill"]["matched_notices"] == 1, f"backfill={r['backfill']}")

    conn = get_connection()
    rows = match_rows(conn, n_math)
    check("库中可查命中关系：notice 命中订阅词", len(rows) == 1 and rows[0]["keyword"] == "数学建模", f"rows={rows}")
    matched_ids = get_matched_notice_ids(conn)
    check("get_matched_notice_ids 只含命中的通知", matched_ids == [n_math], f"matched={matched_ids}")
    check("get_matches_for_notice 命中关系可查", bool(get_matches_for_notice(conn, n_math)))
    conn.close()

    # ---------- Part C：抓取路径（标题命中） + 提取路径（摘要命中） ----------
    print("\n== C. 抓取插入/提取后自动匹配 ==")
    reset_db()
    conn = get_connection()
    add_subscription("数学建模")  # 先建订阅，再"抓取"
    url = "https://t/new1"
    insert_notice(
        conn,
        NoticeRecord(url=url, source="测试来源", title="数学建模国赛宣讲会", raw_content="正文"),
    )
    conn.close()
    _match_notice_by_url(url)  # 模拟抓取插入后的匹配 hook
    conn = get_connection()
    new_id = conn.execute("SELECT id FROM notices WHERE url = ?", (url,)).fetchone()["id"]
    check("抓取插入含词通知 → 标记命中", len(match_rows(conn, new_id)) == 1, f"rows={match_rows(conn, new_id)}")
    conn.close()

    # 提取路径：raw 通知标题不含词、摘要提取后才含词
    url2 = "https://t/new2"
    conn = get_connection()
    insert_notice(
        conn,
        NoticeRecord(url=url2, source="测试来源", title="关于申报工作的通知", raw_content="正文"),
    )
    conn.close()
    _match_notice_by_url(url2)
    conn = get_connection()
    new2_id = conn.execute("SELECT id FROM notices WHERE url = ?", (url2,)).fetchone()["id"]
    check("提取前标题不含词 → 未命中", len(match_rows(conn, new2_id)) == 0)
    # 模拟提取成功写库（notice_service 提取成功后调 match_notice）
    update_extraction(
        conn,
        new2_id,
        {"notice_type": "scholarship", "title": "关于申报工作的通知", "summary": "数学建模训练营报名", "key_dates": []},
        "extracted",
    )
    conn.close()
    match_notice(new2_id)
    conn = get_connection()
    check("提取后摘要含词 → 标记命中", len(match_rows(conn, new2_id)) == 1, f"rows={match_rows(conn, new2_id)}")
    conn.close()

    # ---------- Part D：类型过滤 ----------
    print("\n== D. 订阅限定通知类型 ==")
    reset_db()
    conn = get_connection()
    n_comp = insert_notice_sql(conn, "https://t/d1", "创新创业大赛报名", notice_type="competition")
    n_lec = insert_notice_sql(conn, "https://t/d2", "创新创业专题讲座", notice_type="lecture")
    conn.close()
    r = add_subscription("创新创业", notice_type="competition")
    conn = get_connection()
    check("限定类型：competition 命中", len(match_rows(conn, n_comp)) == 1)
    check("限定类型：lecture 不命中", len(match_rows(conn, n_lec)) == 0, f"rows={match_rows(conn, n_lec)}")
    conn.close()

    # ---------- Part E：启停 ----------
    print("\n== E. 停用订阅清理旧命中、启用后重建 ==")
    conn = get_connection()
    sub_id = r["id"]
    rr = toggle_subscription(sub_id, enabled=False)
    check("停用后旧命中被清理", rr["ok"] and get_subscription_stats(conn)["matches"] == 0)
    conn.close()
    rr = match_all_notices()
    check("停用后全库重跑不产生命中", rr["total_matches"] == 0)
    rr = toggle_subscription(sub_id, enabled=True)
    check("重新启用后回填命中", rr["ok"] and rr["backfill"]["total_matches"] == 1, f"{rr}")
    conn = get_connection()
    check("重新启用后命中关系恢复", len(match_rows(conn, n_comp)) == 1)
    conn.close()

    # ---------- Part F：幂等 ----------
    print("\n== F. 重复匹配幂等（无重复命中关系） ==")
    conn = get_connection()
    match_all_notices()
    match_all_notices()
    n = conn.execute("SELECT COUNT(*) FROM notice_subscription_matches").fetchone()[0]
    check("重复 match_all 命中数不变", n == 1, f"matches={n}")
    match_notice(n_comp)
    match_notice(n_comp)
    n = conn.execute("SELECT COUNT(*) FROM notice_subscription_matches").fetchone()[0]
    check("重复 match_notice 命中数不变", n == 1, f"matches={n}")
    conn.close()

    # ---------- Part G：删除通知级联清理 ----------
    print("\n== G. 删除通知/批量删除级联清理命中 ==")
    reset_db()
    conn = get_connection()
    n1 = insert_notice_sql(conn, "https://t/g1", "数学建模校内选拔赛")
    insert_notice_sql(conn, "https://t/g2", "数学建模国赛宣讲")
    conn.close()
    add_subscription("数学建模")
    conn = get_connection()
    check("删除前有 2 条命中", get_subscription_stats(conn)["matches"] == 2)
    delete_notice(conn, n1)
    check("删除单条通知后命中关系级联清理", get_subscription_stats(conn)["matches"] == 1, f"stats={get_subscription_stats(conn)}")
    conn.close()
    conn = get_connection()
    delete_notices_by_source(conn, "测试来源")
    check("批量删除来源后命中清零", get_subscription_stats(conn)["matches"] == 0)
    conn.close()

    # ---------- Part H：订阅修改（改词/清空类型） ----------
    print("\n== H. 修改订阅后陈旧命中清除、新命中建立 ==")
    reset_db()
    conn = get_connection()
    n_a = insert_notice_sql(conn, "https://t/h1", "奖学金评定通知", notice_type="scholarship")
    n_b = insert_notice_sql(conn, "https://t/h2", "暑期实习招聘", notice_type="recruitment")
    conn.close()
    r = add_subscription("奖学金", notice_type="scholarship")
    conn = get_connection()
    check("改前：奖学金通知命中", len(match_rows(conn, n_a)) == 1)
    check("改前：招聘通知未命中", len(match_rows(conn, n_b)) == 0)
    conn.close()

    # 改词 + 清空类型：奖学金 → 实习（全部类型）
    update_subscription_record(r["id"], keyword="实习", notice_type=None)
    conn = get_connection()
    check("改词后：奖学金通知旧命中被清除", len(match_rows(conn, n_a)) == 0, f"rows={match_rows(conn, n_a)}")
    check("改词后：招聘通知新命中建立", len(match_rows(conn, n_b)) == 1, f"rows={match_rows(conn, n_b)}")
    conn.close()

    # 改回限定类型 scholarship：招聘通知不再命中
    update_subscription_record(r["id"], notice_type="scholarship")
    conn = get_connection()
    check("改类型后：recruitment 不再命中", len(match_rows(conn, n_b)) == 0, f"rows={match_rows(conn, n_b)}")
    conn.close()
    update_subscription_record(r["id"], notice_type=None)  # 全部类型
    conn = get_connection()
    sub_row = conn.execute("SELECT notice_type FROM subscriptions WHERE id = ?", (r["id"],)).fetchone()
    check("清空类型：notice_type 置 NULL", sub_row["notice_type"] is None, f"type={sub_row['notice_type']}")
    conn.close()

    # ---------- Part I：服务/UI 查询 ----------
    print("\n== I. UI 查询接口 ==")
    reset_db()
    conn = get_connection()
    insert_notice_sql(conn, "https://t/i1", "数学建模竞赛报名", notice_type="competition")
    insert_notice_sql(conn, "https://t/i2", "无关通知")
    conn.close()
    add_subscription("数学建模")
    conn = get_connection()
    stats = get_subscription_stats_ui()
    check("统计：订阅 1 命中 1", stats["total"] == 1 and stats["matches"] == 1, f"{stats}")
    ids = get_matched_notice_ids_set()
    check("浏览页命中 ID 集合", ids == {1}, f"{ids}")
    conn.close()
    subs_ui = get_subscriptions_for_ui()
    check("订阅列表含命中数", subs_ui[0]["match_count"] == 1, f"{subs_ui}")
    m = get_match_map([1, 2])
    check("get_match_map 返回 1 → ['数学建模']", m.get(1) == ["数学建模"] and 2 not in m, f"{m}")

    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()
