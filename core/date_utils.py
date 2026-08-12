"""中文通知时间解析：deadline_raw / key_dates 原文 -> ISO 8601。

主解析器为基于正则的中文日期解析（校园通知时间格式高度规律），
dateparser 作为兜底。年份推断规则：
  - 原文含年份 -> 直接用
  - 无年份 -> 用参考日期（通知发布时间）的年份
  - 推断结果早于参考日期 -> 用下一年
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

_FULLWIDTH = str.maketrans(
    {ord(c): ord(h) for c, h in zip("：０１２３４５６７８９（）－", ":0123456789()-")}
)

_DATE_FULL = re.compile(r"(?P<y>\d{4})[年./-](?P<m>\d{1,2})[月./-](?P<d>\d{1,2})日?")
_DATE_YMD = re.compile(r"(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日?")
_DATE_MD = re.compile(r"(?P<m>\d{1,2})月(?P<d>\d{1,2})[日号]")
_DATE_SLASH = re.compile(r"(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?")
_TIME = re.compile(r"(?P<h>\d{1,2})[:：](?P<min>\d{2})")

# 表示"时间点"的关键词，用于定位截止/报名时间
_DEADLINE_KEYWORDS = ("截止", "报名截止", "提交", "前", "止")


def _normalize(text: str) -> str:
    text = text.translate(_FULLWIDTH)
    text = text.replace("号", "日").replace(" ", "").replace("\u3000", "")
    return text


def parse_chinese_datetime(text: str, reference: Optional[date] = None) -> Optional[datetime]:
    """从中文时间片段解析为 datetime。

    Args:
        text: 原文时间片段，如 "7月16日17：00"、"2026年3月18日17:00"
        reference: 参考日期（通知发布时间），用于无年份时的年份推断

    Returns:
        解析后的 datetime；失败返回 None
    """
    if not text:
        return None
    t = _normalize(text)

    m = _DATE_FULL.search(t) or _DATE_YMD.search(t) or _DATE_MD.search(t) or _DATE_SLASH.search(t)
    if not m:
        return None

    groups = m.groupdict()
    year = int(groups["y"]) if groups.get("y") else None
    month, day = int(groups["m"]), int(groups["d"])

    # 校验合法日期
    try:
        if year:
            dt = date(year, month, day)
        else:
            dt = None
    except ValueError:
        return None

    # 时间
    tm = _TIME.search(t)
    hour, minute = (int(tm.group("h")), int(tm.group("min"))) if tm else (0, 0)

    ref = reference or date.today()
    if dt is None:
        # 年份推断：默认用参考年份；早于参考日期则视为下一年（截止时间通常不早于发布时间）
        dt = date(ref.year, month, day)
        if dt < ref:
            dt = date(ref.year + 1, month, day)

    # 防御性处理小时边界：24:00 视为当天结束，非法小时返回 None
    if hour > 24 or minute > 59:
        return None
    if hour == 24:
        return datetime(dt.year, dt.month, dt.day, 23, 59, 59)

    return datetime(dt.year, dt.month, dt.day, hour, minute)


def strip_deadline_noise(text: str) -> str:
    """去掉"即日起至""前""截止"等前后缀，只留日期时间片段。"""
    t = _normalize(text or "")
    t = re.sub(r"^(即日|自|从|在|于|截至|截止)?(起|起至|至|截至|截止到|于)?", "", t)
    t = re.sub(r"(前|止|之前|以前|截止|截止日期|以前)$", "", t)
    return t


def resolve_datetime(raw: str, reference: Optional[date] = None) -> Optional[str]:
    """把原始时间片段解析为 ISO 8601 字符串（无则 None）。"""
    try:
        dt = parse_chinese_datetime(raw, reference)
        return dt.isoformat() if dt else None
    except (ValueError, TypeError):
        return None


def extract_reference_date(published_at: Optional[str], crawled_at: Optional[str]) -> Optional[date]:
    """从 published_at / crawled_at 提取参考日期。"""
    for v in (published_at, crawled_at):
        if not v:
            continue
        v = _normalize(v)[:10]
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
    return None
