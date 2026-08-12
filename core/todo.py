"""待办生成 Agent（M3）。

输入：M2 的结构化提取结果（notices 表字段）。
输出：0~1 条可执行待办（MVP：每条通知最多 1 条主待办）。

设计要点：
  - 仅行动型通知生成待办，policy/result/news/other 返回空
  - due_at 直接用 M2 提取的 deadline，不重解析（确定性，防不一致）
  - LLM 输出经后处理校验（限 1 条、due_at 对齐 deadline、优先级确定性重算）
  - LLM 失败或返回空时，用确定性模板兜底，保证"点击生成"必有结果
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from agents import (
    Agent,
    ModelSettings,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from openai import AsyncOpenAI, BadRequestError

from core.models import ACTION_NOTICE_TYPES, TodoItem, TodoList
from utils.llm import get_model_for_task, run_agent

logger = logging.getLogger(__name__)

MAX_ITEMS = 1  # MVP：每条通知最多 1 条主待办
MAX_RETRIES = 1  # 生成任务简单，重试 1 次

TODO_INSTRUCTIONS = """你是校园通知的待办生成助手。输入是一条通知的结构化提取结果，输出 0~1 条可执行的待办。

## 输出字段说明
- action：待办内容，一句自然中文，包含【具体动作】和【截止时间】。
  例如："在 2026-09-30 17:00 前完成工创大赛校赛报名并提交报名表"。
  动作应尽量参考输入的 signup_method（如"通过QQ群报名""发送报名表至邮箱"）。
- due_at：必须直接使用输入中的 deadline 字段（ISO 8601），不要改写、不要重新推断。输入没有 deadline 则填 null。
- priority：只能填 high 或 normal。截止距今 ≤7 天填 high，否则 normal，无截止填 normal。

## 生成规则
1. 只有行动型通知才生成待办：competition / lecture / registration / scholarship / administrative / recruitment。
2. policy / result / news / other 一律不生成，items 返回空数组。
3. 每条通知最多生成 1 条"最关键行动"的待办，优先级：报名 > 提交 > 参加。
4. 不要编造 deadline 之外的时间。
5. 输出必须是严格的 JSON：{"items":[{"action":"...","due_at":"...","priority":"..."}]}"""


@dataclass
class TodoOutcome:
    """一次待办生成的结果。"""

    status: str  # generated / none / failed
    items: list[TodoItem]
    error: Optional[str] = None


def compute_priority(due_at: Optional[str]) -> str:
    """确定性优先级：截止在 0~7 天内 high，其余 normal（过期不算 high）。"""
    if due_at:
        try:
            days = (datetime.fromisoformat(due_at) - datetime.now()).days
            if 0 <= days <= 7:
                return "high"
        except ValueError:
            pass
    return "normal"


def _build_prompt(notice: dict) -> str:
    meta = [f"通知标题：{notice['title']}"]
    meta.append("结构化提取结果：")
    meta.append(
        json.dumps(
            {
                "notice_type": notice.get("notice_type"),
                "deadline": notice.get("deadline"),
                "deadline_raw": notice.get("deadline_raw"),
                "target_audience": notice.get("target_audience"),
                "signup_method": notice.get("signup_method"),
                "signup_url": notice.get("signup_url"),
                "location": notice.get("location"),
                "summary": notice.get("summary"),
            },
            ensure_ascii=False,
        )
    )
    meta.append(f"今天日期：{datetime.now().strftime('%Y-%m-%d')}")
    return "\n".join(meta)


def template_fallback(notice: dict) -> TodoItem:
    """确定性模板兜底：保证行动型通知点击生成必有结果。"""
    title = notice["title"]
    deadline = notice.get("deadline")
    signup = notice.get("signup_method") or notice.get("signup_url")
    if deadline:
        action = f"在 {deadline} 前完成《{title}》相关报名/提交"
    elif signup:
        action = f"尽快按以下方式完成《{title}》报名：{signup}"
    else:
        action = f"查看《{title}》并跟进相关报名/事项"
    return TodoItem(action=action, due_at=deadline, priority=compute_priority(deadline))


class TodoGenerator:
    """基于 OpenAI Agents SDK 的待办生成器。"""

    def __init__(self):
        self.api_key, self.base_url, self.model = get_model_for_task("todo")
        self._agent: Optional[Agent] = None

    def _get_agent(self) -> Agent:
        if self._agent is None:
            set_tracing_disabled(True)  # 不向 OpenAI 导出 trace
            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
            set_default_openai_client(client, use_for_tracing=False)
            set_default_openai_api("chat_completions")
            self._agent = Agent(
                name="待办生成助手",
                instructions=TODO_INSTRUCTIONS,
                model=self.model,
                output_type=TodoList,
                model_settings=ModelSettings(
                    extra_body={"response_format": {"type": "json_object"}}
                ),
            )
        return self._agent

    async def generate_one(self, notice: dict) -> list[TodoItem]:
        """生成待办（后处理校验：限 1 条、due_at 对齐 deadline）。"""
        prompt = _build_prompt(notice)
        last_error: Optional[str] = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = await self._call(prompt, last_error, attempt=attempt, notice_id=notice.get("id"))
                return self._postprocess(result, notice)
            except BadRequestError:
                # 400 错误不可恢复，直接抛出由调用方 fallback 兜底
                raise
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                if len(last_error) > 500:
                    last_error = last_error[:500] + "..."
                logger.warning("待办生成失败，重试: %s", last_error[:160])
        raise RuntimeError(last_error or "待办生成失败")

    async def _call(
        self,
        prompt: str,
        error_msg: Optional[str],
        attempt: int = 0,
        notice_id: Optional[int] = None,
    ) -> TodoList:
        agent = self._get_agent()
        if error_msg:
            prompt = (
                prompt
                + "\n\n【注意】上一次输出未通过校验，请修正后重新生成：\n"
                + error_msg
            )
        result = await run_agent(
            agent,
            prompt,
            task="todo",
            model=self.model,
            attempt=attempt,
            notice_id=notice_id,
        )
        output = result.final_output
        if not isinstance(output, TodoList):
            raise ValueError(f"Agent 未返回 TodoList: {type(output).__name__}")
        return output

    def _postprocess(self, todo_list: TodoList, notice: dict) -> list[TodoItem]:
        """后处理：限 1 条；due_at 以 notice.deadline 为准；优先级确定性重算。"""
        deadline = notice.get("deadline")
        items = todo_list.items[:MAX_ITEMS]
        for item in items:
            item.due_at = deadline if deadline else None
            item.priority = compute_priority(item.due_at)
        return items


def generate_todos_for_notice(
    notice_id: int,
    replace: bool = True,
    dry_run: bool = False,
) -> TodoOutcome:
    """按需生成一条通知的待办（替换该通知旧的 pending 待办）。

    Args:
        notice_id: 通知 ID
        replace: 是否先删除该通知旧的 pending 待办（默认 True，防重复）
        dry_run: 只计算不写库
    """
    from storage.db import (
        delete_todos_for_notice,
        get_connection,
        insert_todo,
    )

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM notices WHERE id = ?", (notice_id,)
        ).fetchone()
        if row is None:
            return TodoOutcome(status="failed", items=[], error=f"通知 {notice_id} 不存在")
        notice = dict(row)

        if notice["notice_type"] not in ACTION_NOTICE_TYPES:
            return TodoOutcome(
                status="none",
                items=[],
                error=f"非行动型通知({notice['notice_type']})，不生成待办",
            )

        generator = TodoGenerator()
        try:
            items = asyncio.run(generator.generate_one(notice))
        except Exception as e:
            logger.warning("LLM 待办生成失败，使用模板兜底: %s", e)
            items = [template_fallback(notice)]

        if not items:
            items = [template_fallback(notice)]

        if not dry_run and replace and items:
            delete_todos_for_notice(conn, notice_id, status="pending")
            for it in items:
                insert_todo(
                    conn,
                    notice_id=notice_id,
                    action=it.action,
                    due_at=it.due_at,
                    priority=it.priority,
                )

        return TodoOutcome(status="generated", items=items)
    finally:
        conn.close()


def batch_generate(limit: int = 50, dry_run: bool = False) -> dict:
    """批处理：为所有行动型 extracted 通知生成待办（可选功能，MVP 不强制用）。"""
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, notice_type FROM notices "
            "WHERE status IN ('extracted','partial') "
            "AND notice_type IN ('competition','lecture','registration','scholarship','administrative','recruitment') "
            "ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        summary = {"generated": 0, "none": 0, "failed": 0, "details": []}
        for r in rows:
            outcome = generate_todos_for_notice(r["id"], replace=True, dry_run=dry_run)
            summary[outcome.status if outcome.status in summary else "failed"] += 1
            summary["details"].append(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "status": outcome.status,
                    "items": [it.action for it in outcome.items],
                    "error": outcome.error,
                }
            )
        return summary
    finally:
        conn.close()
