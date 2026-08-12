"""API 响应模型：services 返回 dict，此处声明契约（盘点 §5.7 已核实无 sqlite3.Row 泄漏）。

原则：服务层返回的 dict 直接 `model_validate`，不引入转换器；
`core/qa.py` 的 `QAResult` 是唯一例外，序列化在路由层完成。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    version: str = "1.0"
    notices: int = 0
    db: str = "ok"


class StatusCounts(BaseModel):
    """通知状态统计（raw/extracted/partial/failed…）。"""

    model_config = ConfigDict(extra="allow")

    raw: int = 0
    extracted: int = 0
    partial: int = 0
    failed: int = 0


class NoticeSummary(BaseModel):
    """通知列表项（浏览页卡片所需字段，避免整条 raw_content 传输）。"""

    id: int
    url: str
    source: str
    title: str
    published_at: Optional[str] = None
    crawled_at: str
    status: str
    notice_type: Optional[str] = None
    deadline: Optional[str] = None
    summary: Optional[str] = None
    keywords: list[str] = []  # 订阅命中词（浏览页徽标用，无则空）


class NoticeDetail(BaseModel):
    """通知详情（含正文与关键日期）。"""

    id: int
    url: str
    source: str
    title: str
    raw_content: Optional[str] = None
    published_at: Optional[str] = None
    crawled_at: str
    status: str
    notice_type: Optional[str] = None
    target_audience: Optional[str] = None
    signup_method: Optional[str] = None
    signup_url: Optional[str] = None
    location: Optional[str] = None
    location_type: Optional[str] = None
    deadline: Optional[str] = None
    deadline_raw: Optional[str] = None
    key_dates: list[dict] = []
    summary: Optional[str] = None
    extracted_at: Optional[str] = None
    keywords: list[str] = []
