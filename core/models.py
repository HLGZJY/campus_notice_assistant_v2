"""结构化提取的数据模型（Pydantic）。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 通知类型枚举（10 类）
NoticeType = Literal[
    "competition",  # 竞赛
    "lecture",  # 讲座
    "registration",  # 报名/选课/培训
    "scholarship",  # 奖学金
    "administrative",  # 行政事务（放假/注册/缴费）
    "recruitment",  # 招聘/实习
    "policy",  # 政策/资讯
    "result",  # 结果公示
    "news",  # 动态/新闻
    "other",  # 其他
]

# 行动型通知：能生成待办的类型
ACTION_NOTICE_TYPES = {
    "competition",
    "lecture",
    "registration",
    "scholarship",
    "administrative",
    "recruitment",
}
# 非行动型通知：只有展示价值，无截止/报名字段
NON_ACTION_NOTICE_TYPES = {"policy", "result", "news", "other"}


class KeyDate(BaseModel):
    """一个重要的日期/时间点（如报名截止、初赛、决赛、颁奖）。"""

    label: str  # 时间点含义，如"报名截止""初赛""决赛"
    date_raw: str = ""  # 原文时间片段，如"5月23日12:00-17:00"
    datetime: Optional[str] = None  # 规范化为 ISO 8601（后处理填充）


class NoticeExtraction(BaseModel):
    """LLM 结构化提取结果。"""

    notice_type: NoticeType
    title: str  # 从正文/标题修正后的规范标题
    target_audience: Optional[str] = None  # 面向对象
    signup_method: Optional[str] = None  # 报名方式（QQ群/邮箱/扫码/网址描述）
    signup_url: Optional[str] = None  # 报名网页链接
    location: Optional[str] = None  # 线下地点
    location_type: Optional[Literal["online", "offline", "hybrid"]] = None
    deadline_raw: Optional[str] = None  # 截止时间原文片段
    deadline: Optional[str] = None  # 截止时间 ISO 8601（后处理重算为准）
    key_dates: list[KeyDate] = Field(default_factory=list)  # 其他重要时间
    summary: Optional[str] = None  # 摘要

    @field_validator("title")
    @classmethod
    def _title_not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("标题不能为空")
        return v

    @field_validator("target_audience", "signup_method", "location", "summary")
    @classmethod
    def _cap_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 300:
            return v[:300]
        return v

    def is_action_type(self) -> bool:
        return self.notice_type in ACTION_NOTICE_TYPES

    def has_action_fields(self) -> bool:
        return bool(
            self.deadline_raw
            or self.signup_method
            or self.signup_url
            or self.location
        )


class TodoItem(BaseModel):
    """一条待办（M3）。"""

    action: str  # 待办内容，如"在 2026-09-30 17:00 前完成工创大赛校赛报名"
    due_at: Optional[str] = None  # 截止时间 ISO 8601（复用 notice.deadline）
    priority: str = "normal"  # high / normal / low

    @field_validator("action")
    @classmethod
    def _action_not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("待办内容不能为空")
        return v


class TodoList(BaseModel):
    """待办清单（LLM 输出）。"""

    items: list[TodoItem] = Field(default_factory=list)
