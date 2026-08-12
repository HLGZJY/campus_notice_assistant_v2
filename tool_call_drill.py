"""模块 1.5 Tool Calling 循环演练（支线脚本，不污染主线代码）。

演练内容：
  1. 注册假工具 query_mock_service(service_type)，返回硬编码的打印店/跑腿/文印店信息，不接真实服务。
  2. Runner.run(..., max_turns=5) 强制最大迭代守卫，超限捕获 MaxTurnsExceeded 优雅收尾。
  3. 工具函数内按本次 run 维度缓存结果，同一工具同参数重复调用直接返回守卫消息，不做真实查询。
  4. instructions 约束模型只能使用工具返回数据，工具返回不可用时如实告知，不编造电话/价格/营业时间。

用法：
    python tool_call_drill.py                              # 交互模式
    python tool_call_drill.py "帮我找打印店电话"            # 单次提问
    python tool_call_drill.py --scenario unavailable "帮我找打印店"
    python tool_call_drill.py --scenario duplicate "帮我找打印店"

每次运行都会把逐轮 tool_calls 日志落盘到 data/logs/tool_call_drill_<YYYYmmdd_HHMMSS>.json。

复用边界：只读调用 get_model_for_task("qa") 取模型配置，沿用 core/qa.py 的 client 初始化；
不导入 core/services 业务模块、不写 token_usage、不写库、不改配置。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 —— 非 UTF-8 终端不阻塞运行
    pass

logging.basicConfig(level=logging.CRITICAL, format="%(levelname)s %(message)s")

from agents import (  # noqa: E402
    Agent,
    AgentHooks,
    Runner,
    function_tool,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from agents.exceptions import MaxTurnsExceeded  # noqa: E402
from agents.tool import FunctionTool  # noqa: E402
from agents.tool_context import ToolContext  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402

from utils.llm import get_model_for_task  # noqa: E402

MAX_TURNS = 5
LOG_DIR = Path(__file__).parent / "data" / "logs"

# 假数据：不接真实服务，电话为虚构占位
MOCK_SERVICES: dict[str, dict] = {
    "print": {"name": "北苑打印店", "phone": "027-8888-0001", "hours": "8:00-22:00"},
    "errand": {"name": "校园跑腿帮", "phone": "027-8888-0002", "hours": "9:00-21:00"},
    "photocopy": {"name": "文印快线", "phone": "027-8888-0003", "hours": "8:30-20:30"},
}

GUARD_MESSAGE = (
    "该工具同参数已调用过，结果与上次一致（已缓存）。"
    "请换用其他方式或参数，不要重复调用。"
)

DRILL_INSTRUCTIONS = """你是校园服务查询演练助手，正在做 Tool Calling 循环演练。

规则：
1. 只能通过工具 query_mock_service 获取服务信息，禁止凭空编造电话、价格、营业时间。
2. 工具返回 {"ok": false, "reason": ...}（服务不可用）时，必须如实告知用户"暂无结果/服务暂不可用"，不得虚构任何字段。
3. 若同一工具同参数重复调用被守卫拦截（返回 guard: duplicate_call），说明结果与上次一致，请换用其他方式/参数，或直接如实回答。
4. 结论必须与工具实际返回一致，用中文回答。"""

DUPLICATE_TRAP_PROMPT = (
    "演练要求：请先用 query_mock_service 查询打印店（service_type=\"print\"），"
    "然后用完全相同的参数再调用一次 query_mock_service(service_type=\"print\")，"
    "最后汇报两次的结果。"
)


def _fmt_args(args_raw: str) -> str:
    """把工具参数的原始 JSON 字符串格式化为 key=\"value\" 展示。"""
    if not args_raw:
        return ""
    try:
        parsed = json.loads(args_raw)
    except (ValueError, TypeError):
        return args_raw
    if isinstance(parsed, dict):
        return ", ".join(f'{k}="{v}"' for k, v in parsed.items())
    return args_raw


def build_query_tool(cache: dict, lock: threading.Lock, unavailable: set) -> FunctionTool:
    """按本次 run 生成假工具，闭包持有该 run 的缓存，避免不同 prompt 之间串扰。

    缓存命中不重算：同一 service_type 第二次调用直接返回守卫消息（含 duplicate_call 标记）。
    """
    @function_tool
    def query_mock_service(service_type: str) -> str:
        """查询校园生活服务点信息。

        Args:
            service_type: 服务类型，取值 "print"（打印店）/ "errand"（跑腿）/ "photocopy"（文印店）。

        Returns:
            JSON 字符串。正常：{"ok": true, "service_type": ..., "name": ..., "phone": ..., "hours": ...}；
            服务不可用：{"ok": false, "reason": "服务暂不可用，请稍后再试"}；
            同参数重复调用：{"ok": false, "guard": "duplicate_call", "message": "..."}。
        """
        with lock:
            if service_type in cache:
                return json.dumps(
                    {"ok": False, "guard": "duplicate_call", "message": GUARD_MESSAGE},
                    ensure_ascii=False,
                )
            if service_type in unavailable:
                data = {"ok": False, "reason": "服务暂不可用，请稍后再试"}
            else:
                svc = MOCK_SERVICES.get(service_type)
                if svc is None:
                    data = {"ok": False, "reason": f"未知服务类型：{service_type}"}
                else:
                    data = {"ok": True, "service_type": service_type, **svc}
            cache[service_type] = data
        return json.dumps(data, ensure_ascii=False)

    return query_mock_service


class DrillHooks(AgentHooks):
    """挂到 Agent.hooks 的逐轮日志钩子：记录并打印每轮 LLM 调用与每次 tool_call。"""

    def __init__(self, records: list[dict]):
        super().__init__()
        self.records = records
        self.turn = 0
        self._pending: dict[str, dict] = {}

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self.turn += 1
        self.records.append({"event": "llm_start", "turn": self.turn})
        print(f"[轮次 {self.turn}]")

    async def on_tool_start(self, context, agent, tool):
        ctx = context if isinstance(context, ToolContext) else None
        name = ctx.tool_name if ctx else getattr(tool, "name", "?")
        args_raw = ctx.tool_arguments if ctx else ""
        call_id = ctx.tool_call_id if ctx else ""
        self._pending[call_id] = {
            "event": "tool_call",
            "turn": self.turn,
            "tool_call_id": call_id,
            "tool": name,
            "args": args_raw,
        }
        print(f"  -> tool_call: {name}({_fmt_args(args_raw)})")

    async def on_tool_end(self, context, agent, tool, result):
        ctx = context if isinstance(context, ToolContext) else None
        call_id = ctx.tool_call_id if ctx else ""
        rec = self._pending.pop(call_id, None)
        if rec is None:
            rec = {
                "event": "tool_call",
                "turn": self.turn,
                "tool_call_id": call_id,
                "tool": getattr(tool, "name", "?"),
                "args": "",
            }
        text = str(result)
        guarded = '"guard": "duplicate_call"' in text
        rec["return"] = text
        rec["guarded"] = guarded
        self.records.append(rec)
        marker = "  [守卫拦截]" if guarded else ""
        print(f"  <- 返回: {text}{marker}")


_MODEL: Optional[str] = None


def _get_model() -> str:
    global _MODEL
    if _MODEL is None:
        _MODEL = get_model_for_task("qa")[2]
    return _MODEL


def _configure_client() -> None:
    """沿用 core/qa.py 的 client 初始化。"""
    api_key, base_url, model = get_model_for_task("qa")
    global _MODEL
    _MODEL = model
    set_tracing_disabled(True)
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    set_default_openai_client(client, use_for_tracing=False)
    set_default_openai_api("chat_completions")


async def run_drill(prompt: str, scenario: str, unavailable_service: str) -> tuple[list[dict], str]:
    """执行一次演练：建独立缓存 → 注册假工具 → max_turns=5 跑通 → 返回(逐轮记录, 最终回答)。"""
    cache: dict = {}
    lock = threading.Lock()
    unavailable = {unavailable_service} if scenario == "unavailable" else set()
    tool = build_query_tool(cache, lock, unavailable)

    if scenario == "duplicate":
        prompt = f"{prompt}\n\n{DUPLICATE_TRAP_PROMPT}"

    records: list[dict] = []
    hooks = DrillHooks(records)

    _configure_client()
    agent = Agent(
        name="ToolCalling演练助手",
        instructions=DRILL_INSTRUCTIONS,
        model=_get_model(),
        tools=[tool],
        hooks=hooks,
    )

    print(f"== 演练开始（scenario={scenario}, max_turns={MAX_TURNS}）==")
    try:
        result = await Runner.run(agent, prompt, max_turns=MAX_TURNS)
        answer = str(result.final_output or "").strip()
    except MaxTurnsExceeded:
        answer = f"已达最大轮数（{MAX_TURNS} 轮），工具调用循环被强制终止。"
        print(f"[中止] {answer}")
    print("== 演练结束 ==")

    if not answer:
        answer = "模型未返回有效回答。"
    print(f"最终回答：\n{answer}\n")

    llm_calls = sum(1 for r in records if r["event"] == "llm_start")
    tool_calls = sum(1 for r in records if r["event"] == "tool_call")
    guarded = sum(1 for r in records if r["event"] == "tool_call" and r.get("guarded"))
    print(f"[汇总] LLM 调用 {llm_calls} 次 / 工具调用 {tool_calls} 次 / 守卫拦截 {guarded} 次")

    return records, answer


def _save_log(records: list[dict], answer: str, scenario: str, prompt: str) -> Path:
    """把逐轮日志落盘到 data/logs/tool_call_drill_<YYYYmmdd_HHMMSS>.json。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    log_path = LOG_DIR / f"tool_call_drill_{now.strftime('%Y%m%d_%H%M%S')}.json"
    payload = {
        "module": "模块1.5 Tool Calling 循环演练",
        "timestamp": now.isoformat(),
        "scenario": scenario,
        "model": _get_model(),
        "max_turns": MAX_TURNS,
        "prompt": prompt,
        "final_answer": answer,
        "records": records,
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"日志已落盘：{log_path}")
    return log_path


def main() -> None:
    parser = argparse.ArgumentParser(description="模块1.5 Tool Calling 循环演练")
    parser.add_argument("prompt", nargs="?", default=None, help="提问内容；缺省进入交互模式")
    parser.add_argument(
        "--scenario",
        choices=["normal", "unavailable", "duplicate"],
        default="normal",
        help="演练场景：normal 正常 / unavailable 服务不可用 / duplicate 重复调用守卫",
    )
    parser.add_argument(
        "--unavailable-service",
        choices=sorted(MOCK_SERVICES),
        default="print",
        help="--scenario unavailable 时标记为不可用的服务类型",
    )
    args = parser.parse_args()

    if args.prompt is None:
        print("进入交互模式，输入问题回车执行；输入 exit/quit 或 Ctrl+C 退出。")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in ("exit", "quit"):
                break
            records, answer = asyncio.run(
                run_drill(line, args.scenario, args.unavailable_service)
            )
            _save_log(records, answer, args.scenario, line)
        return

    records, answer = asyncio.run(run_drill(args.prompt, args.scenario, args.unavailable_service))
    _save_log(records, answer, args.scenario, args.prompt)


if __name__ == "__main__":
    main()
