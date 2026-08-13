"""utils.llm.run_agent_stream 的回归验证（离线、不依赖真实 LLM/网络）。

背景：Runner.run_streamed 在 openai-agents 0.19.x 是同步函数（返回
RunResultStreaming，无 __await__）。历史版本误写 `await Runner.run_streamed(...)`
导致 TypeError: RunResultStreaming can't be used in 'await' expression，问答流式
SSE 一用就崩。本测试用替身 Runner 直接走 run_agent_stream 真实代码路径：

  1. 成功：逐 delta 产出正确、response.completed 的 usage 正确记账
  2. 中途失败：已产出部分不影响，success=0 记账且异常照常上抛
  3. 回归护栏：替身 run_streamed 返回的是「不可 await 的流对象」——
     若有人把调用改回 await 形式，本测试会立即 TypeError 失败

用法：python test_llm_stream.py
"""
import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.CRITICAL, format="%(levelname)s %(message)s")

import storage.db
from storage.db import get_connection
from utils import llm as utils_llm
from utils.llm import run_agent_stream

TMP_DB = Path(__file__).parent / "data" / "test_llm_stream.db"

storage.db.DB_PATH = TMP_DB


def reset_db():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


class FakeUsage:
    def __init__(self, input_tokens=0, output_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeStreamResult:
    """替身 RunResultStreaming：同步构造、async stream_events()。

    关键点：这个对象「不可 await」（没有 __await__）。若 run_agent_stream 误用
    await Runner.run_streamed(...)，拿到的将是本对象本身，await 会抛 TypeError。
    """

    def __init__(self, deltas, usage=None, fail_after=None, error=None):
        self.deltas = deltas
        self.usage = usage
        self.fail_after = fail_after
        self.error = error

    def stream_events(self):
        async def _gen():
            for i, d in enumerate(self.deltas):
                if self.fail_after is not None and i >= self.fail_after:
                    raise self.error
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(type="response.output_text.delta", delta=d),
                )
            if self.fail_after is None:
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(
                        type="response.completed",
                        response=SimpleNamespace(usage=self.usage),
                    ),
                )

        return _gen()


class FakeRunner:
    """替身 Runner：run_streamed 是同步函数，返回 FakeStreamResult。"""

    def __init__(self, stream, calls=None):
        self.stream = stream
        self.calls = calls if calls is not None else []

    def run_streamed(self, agent, prompt):
        self.calls.append((agent, prompt))
        return self.stream


def run():
    reset_db()
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    print("== 1. 成功：逐 delta 产出 + completed usage 记账 ==")
    calls = []
    utils_llm.Runner = FakeRunner(
        FakeStreamResult(
            ["最近有", "数学建模竞赛", "，截止 12 月 1 日。"],
            usage=FakeUsage(input_tokens=10, output_tokens=5),
        ),
        calls,
    )

    async def collect():
        parts = []
        async for d in run_agent_stream(None, "p", task="qa", model="m1", notice_id=1):
            parts.append(d)
        return "".join(parts)

    text = asyncio.run(collect())
    check(
        "delta 拼接完整",
        text == "最近有数学建模竞赛，截止 12 月 1 日。",
        f"got={text!r}",
    )
    row = get_connection().execute(
        "SELECT * FROM token_usage WHERE notice_id=1 AND task='qa'"
    ).fetchone()
    check(
        "成功记账 input=10 output=5",
        row and row["input_tokens"] == 10 and row["output_tokens"] == 5 and row["success"] == 1,
        f"in={row['input_tokens'] if row else None} out={row['output_tokens'] if row else None}",
    )
    check("run_streamed 收到 agent/prompt", calls == [(None, "p")], f"{calls}")

    print("\n== 2. 中途失败：success=0 记账 + 异常上抛 ==")
    utils_llm.Runner = FakeRunner(
        FakeStreamResult(
            ["前缀", "要抛错了"],
            fail_after=1,
            error=RuntimeError("stream boom"),
        )
    )
    raised = False
    collected = []

    async def collect_fail():
        async for d in run_agent_stream(None, "p", task="qa", model="m2", notice_id=2):
            collected.append(d)

    try:
        asyncio.run(collect_fail())
    except RuntimeError as e:
        raised = "stream boom" in str(e)
    check("异常照常上抛", raised)
    check(
        "失败前已产出的 delta 仍可消费",
        "".join(collected) == "前缀",
        f"got={''.join(collected)!r}",
    )
    row = get_connection().execute(
        "SELECT * FROM token_usage WHERE notice_id=2 AND task='qa' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    check(
        "失败记账 success=0 且 error 含 RuntimeError、tokens=0",
        row and row["success"] == 0 and row["error"].startswith("RuntimeError")
        and row["input_tokens"] == 0 and row["output_tokens"] == 0,
        f"success={row['success'] if row else None}",
    )

    print("\n== 3. 回归护栏：run_streamed 返回值不可 await ==")
    # run_agent_stream 已正确写成 result = Runner.run_streamed(...)（不加 await）。
    # 若有人改回 await 形式，FakeRunner 返回的 FakeStreamResult 无 __await__，
    # 直接抛 TypeError，本用例即失败。
    awaitable = hasattr(FakeStreamResult(["x"]), "__await__")
    check(
        "替身流对象无 __await__（旧写法必然 TypeError）",
        not awaitable,
    )

    cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


def cleanup():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    run()