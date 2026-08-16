"""结构化提取 Agent：把通知原文提取为结构化字段。

技术方案：
  - OpenAI Agents SDK 的 Agent + output_type 约束输出（Pydantic 模型）
  - opencode-go 接口不支持 SDK 默认的 json_schema 模式，通过
    ModelSettings.extra_body 注入 response_format=json_object
  - 提取后再做语义校验 + 时间规范化（deadline_raw 用自研解析器重算）
  - 校验失败时把错误回传给 LLM 重试（最多 2 次）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from agents import (
    Agent,
    ModelSettings,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from openai import AsyncOpenAI, BadRequestError

from core.date_utils import (
    extract_reference_date,
    resolve_datetime,
    strip_deadline_noise,
)
from core.models import NoticeExtraction
from utils.llm import get_model_candidates, is_failover_worthy, run_agent

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 3000  # 正文截断，控制 token 成本
MAX_RETRIES = 2  # 校验失败重试次数

EXTRACTOR_INSTRUCTIONS = """你是一名校园通知结构化提取助手。你的任务是从一条学校通知中提取关键结构化信息。

## 输入
系统会提供：通知标题、通知正文、通知发布时间(published_at)。注意：正文里经常没有完整年份，你需要按发布时间推断年份。

## 输出字段说明
- notice_type：通知类型，必须从以下枚举中选一个：
  - competition 竞赛（数学建模、创新创业大赛、程序设计竞赛等）
  - lecture 讲座（学术报告、校友分享等）
  - registration 报名/培训/选课（如培训报名、活动报名）
  - scholarship 奖学金（国奖、校奖申请）
  - administrative 行政事务（放假、注册、缴费、评奖结果安排等）
  - recruitment 招聘/实习
  - policy 政策/资讯（创业扶持政策、政策清单、新闻稿等，非具体行动通知）
  - result 结果公示（比赛获奖名单、公示）
  - news 动态/新闻（非政策、非行动的一般动态）
  - other 其他
- title：规范后的通知标题（保持原意，不要随意改写）。
- target_audience：面向对象。如"全校本科生""在校本科生、研究生"。没有则 null。
- signup_method：报名/参加方式，用一句中文描述。如"加入QQ群1080817784在线填写"、"扫码填写腾讯文档报名表"、"登录 ibizsim.cn 官网注册报名"。没有则 null。
- signup_url：报名网页链接（http/https 开头）。如果只有 QQ 群号、邮箱、二维码，则填 null。没有则 null。
- location：线下活动地点，如"9号教学楼2楼实验室"。线上活动填 null。没有则 null。
- location_type：online（纯线上）/ offline（纯线下）/ hybrid（线上线下结合）。不确定填 null。
- deadline_raw：最关键的行动截止时间【原文片段】，只包含日期时间本身，不包含"即日起至""前""截止"等前缀词。例如原文"报名时间：即日起至7月16日17：00"，deadline_raw 应为"7月16日17：00"。
- deadline：把 deadline_raw 转成 ISO 8601 完整字符串（含年份），例如"2026-07-16T17:00:00"。年份推断规则：正文没写年份就用发布时间(published_at)的年份；若推断出的日期早于发布时间，则用下一年。此字段由你尽力给出，之后系统会再次校准。
- key_dates：通知中所有其他重要时间点（报名截止之外的初赛、决赛、颁奖、结果公布等），每项包含：
  - label：时间点含义，如"初赛""决赛""颁奖"
  - date_raw：该时间点的原文片段，如"5月23日12:00-17:00"
  - datetime：ISO 8601，尽力给出，可先为 null
- summary：1-2 句中文摘要，概括通知讲什么。

## 关键规则
1. 只有通知中明确写了时间才填 deadline_raw 和 deadline，不要凭空推断。截止时间优先级：报名截止 > 提交截止 > 活动开始前的最后一个时间点。
2. 找不到某字段就填 null 或空数组，严禁编造。
3. signup_url 只填真实网页链接；QQ群号、邮箱、二维码一律放到 signup_method。
4. 纯线上活动 location 填 null、location_type 填 online。
5. 政策/新闻/结果公示类通知没有报名和截止时间，deadline_raw/deadline/signup_method 填 null。
6. 输出必须是严格的 JSON 对象，字段名和上面的完全一致。"""


@dataclass
class ExtractionOutcome:
    """单条通知的提取结果。"""

    status: str  # extracted / partial / failed
    extraction: Optional[NoticeExtraction] = None
    error: Optional[str] = None


def _truncate(content: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    if not content:
        return ""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n……（正文过长已截断）"


def build_prompt(
    title: str,
    content: str,
    published_at: Optional[str] = None,
    crawled_at: Optional[str] = None,
) -> str:
    """拼接提取 Prompt。"""
    meta = []
    meta.append(f"通知标题：{title}")
    meta.append(f"通知正文：\n{_truncate(content)}")
    if published_at:
        meta.append(f"通知发布时间：{published_at}")
    else:
        meta.append("通知发布时间：未知")
    if crawled_at:
        meta.append(f"通知抓取时间：{crawled_at}")
    return "\n\n".join(meta)


def classify_status(ext: NoticeExtraction) -> str:
    """提取成功后的状态：extracted / partial。"""
    if ext.is_action_type() and ext.has_action_fields():
        return "extracted"
    return "partial"


class NoticeExtractor:
    """基于 OpenAI Agents SDK 的提取器。

    models 为有序候选列表（同供应商内失败切换）：首模型抛可恢复错误
    （配额/网络/5xx/404 等）时自动切下一个；400/401/403 不切换直接失败。
    """

    def __init__(self):
        self.api_key, self.base_url, self.models = get_model_candidates("extraction")
        self._agents: dict[str, Agent] = {}

    def _get_agent(self, model: str) -> Agent:
        agent = self._agents.get(model)
        if agent is None:
            set_tracing_disabled(True)  # 不向 OpenAI 导出 trace
            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
            set_default_openai_client(client, use_for_tracing=False)
            set_default_openai_api("chat_completions")
            agent = Agent(
                name="通知提取助手",
                instructions=EXTRACTOR_INSTRUCTIONS,
                model=model,
                output_type=NoticeExtraction,
                model_settings=ModelSettings(
                    extra_body={"response_format": {"type": "json_object"}}
                ),
            )
            self._agents[model] = agent
        return agent

    async def extract_one(
        self,
        title: str,
        content: str,
        published_at: Optional[str] = None,
        crawled_at: Optional[str] = None,
        notice_id: Optional[int] = None,
    ) -> ExtractionOutcome:
        """提取单条通知，带校验重试 + 同供应商模型失败切换。

        400 类永久错误不切换直接失败；可恢复错误切换下一个候选模型。
        """
        reference: Optional[date] = extract_reference_date(published_at, crawled_at)
        prompt = build_prompt(title, content, published_at, crawled_at)

        switched_errors: list[str] = []
        for model in self.models:
            outcome = await self._try_model(model, prompt, reference, title, notice_id)
            if outcome is not None:
                return outcome
            switched_errors.append(f"模型 {model} 失败")
            logger.warning("提取模型切换 %s → 下一个候选 %s", model, title[:40])

        return ExtractionOutcome(
            status="failed",
            extraction=None,
            error="；".join(switched_errors) or "所有候选模型均提取失败",
        )

    async def _try_model(
        self,
        model: str,
        prompt: str,
        reference: Optional[date],
        title: str,
        notice_id: Optional[int],
    ) -> Optional[ExtractionOutcome]:
        """在单个候选模型上执行「调用 + 校验重试」。

        Returns:
            None 表示该模型抛出可恢复错误，应切换到下一个候选模型。
        """
        last_error: Optional[str] = None
        best: Optional[NoticeExtraction] = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                ext = await self._call(model, prompt, last_error, attempt=attempt, notice_id=notice_id)
            except BadRequestError as e:
                # 400 错误（prompt 被上游拒绝/内容过滤/token 超限等）不可恢复
                msg = f"LLM 请求被拒绝: {type(e).__name__}: {e}"
                logger.warning("提取失败(不可恢复) %s: %s", title, msg[:200])
                return ExtractionOutcome(status="failed", extraction=None, error=msg)
            except Exception as e:
                if not is_failover_worthy(e):
                    msg = f"LLM 调用失败(不可恢复): {type(e).__name__}: {e}"
                    logger.warning("提取失败(不可恢复) %s: %s", title, msg[:200])
                    return ExtractionOutcome(status="failed", extraction=None, error=msg)
                last_error = f"模型 {model} 调用失败: {type(e).__name__}: {e}"
                if len(last_error) > 500:
                    last_error = last_error[:500] + "..."
                logger.warning("提取模型 %s 失败，尝试下一个: %s", model, last_error[:200])
                return None

            best = ext
            ext, errors = self._resolve_and_validate(ext, reference)
            if not errors:
                return ExtractionOutcome(status=classify_status(ext), extraction=ext, error=None)
            last_error = "；".join(errors)
            if len(last_error) > 500:
                last_error = last_error[:500] + "..."
            logger.info("提取校验未通过(%d/%d) %s: %s", attempt + 1, MAX_RETRIES + 1, title, last_error)

        # 校验重试耗尽：保留最后一次结果（不切换模型，校验失败切模型收益不大）
        if best is not None:
            return ExtractionOutcome(
                status=classify_status(best),
                extraction=best,
                error=last_error,
            )
        return None

    async def _call(
        self,
        model: str,
        prompt: str,
        error_msg: Optional[str],
        attempt: int = 0,
        notice_id: Optional[int] = None,
    ) -> NoticeExtraction:
        agent = self._get_agent(model)
        if error_msg:
            prompt = (
                prompt
                + "\n\n【注意】上一次输出未通过校验，请修正后重新提取：\n"
                + error_msg
            )
        result = await run_agent(
            agent,
            prompt,
            task="extraction",
            model=model,
            attempt=attempt,
            notice_id=notice_id,
        )
        output = result.final_output
        if not isinstance(output, NoticeExtraction):
            raise ValueError(f"Agent 未返回 NoticeExtraction: {type(output).__name__}")
        return output

    def _resolve_and_validate(
        self,
        ext: NoticeExtraction,
        reference: Optional[date],
    ) -> tuple[NoticeExtraction, list[str]]:
        """时间规范化 + 语义校验。返回 (修正后的模型, 错误列表)。"""
        errors: list[str] = []

        # 1. deadline：以 deadline_raw 用自研解析器重算（年份推断更可靠）
        if ext.deadline_raw:
            cleaned = strip_deadline_noise(ext.deadline_raw)
            resolved = resolve_datetime(cleaned, reference) or resolve_datetime(
                ext.deadline_raw, reference
            )
            if resolved:
                ext.deadline = resolved
            else:
                ext.deadline = None
                errors.append(f"截止时间无法解析: {ext.deadline_raw!r}")
        else:
            ext.deadline = None

        # 2. key_dates：尽力解析，不因解析失败阻断
        for kd in ext.key_dates:
            cleaned = strip_deadline_noise(kd.date_raw)
            resolved = resolve_datetime(cleaned, reference) or resolve_datetime(
                kd.date_raw, reference
            )
            kd.datetime = resolved

        return ext, errors
