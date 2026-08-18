"""批次 C 验收测试：Lost-in-the-Middle 上下文重排（离线，不调 LLM）。

覆盖：
  1. _build_context：context 整体反转（Top-1 落到底部紧邻问题区），
     sources 保持检索序（[n] 编号与 sources 按索引对应，不破坏引用映射）。
  2. _build_prompt：问题：位于 参考通知： 之后。
  3. ask() 全链路：prompt 布局正确、[1] 引用映射回 Top-1。
  4. ask_stream() 同样布局。
  5. 反转后多编号引用映射依然正确（[2] → 2 号来源）。

运行：python test_qa_lost_middle.py
"""
from __future__ import annotations

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

from langchain_core.documents import Document

import storage.db
from storage.db import get_connection  # noqa: F401
from utils import llm as utils_llm

TMP_DB = Path(__file__).parent / "data" / "test_qa_lost_middle.db"
storage.db.DB_PATH = TMP_DB

from core.qa import QAAgent  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def make_docs(n: int) -> list[Document]:
    """构造 n 个来自不同通知的 chunk（按检索相关度从高到低）。"""
    return [
        Document(
            page_content=f"通知{i}的内容",
            metadata={
                "notice_id": i,
                "title": f"通知标题{i}",
                "notice_type": "竞赛",
                "source": "scuec",
                "url": f"http://example.com/{i}",
                "deadline": "2026-12-01",
            },
        )
        for i in range(1, n + 1)
    ]


def fake_index(docs: list[Document]):
    """替身索引：search 直接返回预置 docs，绕过真实 Chroma。"""

    class _Fake:
        def __init__(self, docs):
            self.docs = docs

        def search(self, *args, **kwargs):
            return self.docs

        def stats(self):
            return {}

    return _Fake(docs)


class FakeRunner:
    """替身 Runner：run/run_streamed 返回固定答案并记录 prompt。"""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def run(self, agent, prompt):
        self.calls.append(prompt)
        return SimpleNamespace(final_output=self.answer, usage=None)

    def run_streamed(self, agent, prompt):
        self.calls.append(prompt)
        return _FakeStreamResult(self.answer)


class _FakeStreamResult:
    def __init__(self, answer):
        self.answer = answer
        self.usage = None

    def stream_events(self):
        async def _gen():
            for ch in self.answer:
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(type="response.output_text.delta", delta=ch),
                )
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(usage=None),
                ),
            )

        return _gen()


def _build_context_checks() -> None:
    docs = make_docs(3)
    agent = QAAgent(index=fake_index(docs), max_sources=3, search_mode="vector")
    context, sources = agent._build_context(docs)

    check("B1. context 反转：Top-1 在末尾", context.rstrip().endswith("通知1的内容"), "")
    check("B1. context 反转：Top-3 在开头", context.startswith("[3] 标题：通知标题3"), context[:30])
    check("B1. sources 保持检索序", [s.notice_id for s in sources] == [1, 2, 3], f"{[s.notice_id for s in sources]}")
    check("B1. 编号与来源索引对应", "[1] 标题：通知标题1" in context, "")
    check("B1. 编号标签完整", all(f"[{i}] 标题：通知标题{i}" in context for i in (1, 2, 3)), "")


def _prompt_layout(prompt: str) -> tuple[bool, bool, bool]:
    """检查 prompt 布局：参考通知在问题之前、Top-1 紧邻问题区。"""
    idx_notice = prompt.index("参考通知：")
    idx_question = prompt.index("问题：")
    idx_top1 = prompt.index("通知标题1")
    idx_question_after = prompt.index("问题：", idx_top1)
    return (
        idx_question > idx_notice,
        idx_question_after > idx_top1,
        idx_question_after - idx_top1 < 200,  # Top-1 紧邻问题区（无中间块）
    )


def test_prompt_layout() -> None:
    runner = FakeRunner("推荐参加[1]比赛。")
    utils_llm.Runner = runner
    agent = QAAgent(index=fake_index(make_docs(4)), max_sources=4, search_mode="vector")

    result = asyncio.run(agent.ask("有什么竞赛？"))
    prompt = runner.calls[0]
    a, b, c = _prompt_layout(prompt)
    check("B2. 问题在参考通知之后", a, "")
    check("B2. Top-1 位于问题之前", b, "")
    check("B2. Top-1 紧邻问题区（中间无其他块）", c, "")
    check("B2. 引用 [1] 映射回 Top-1", [s.notice_id for s in result.sources] == [1], f"{[s.notice_id for s in result.sources]}")

    # 多编号映射在反转后依然正确
    runner2 = FakeRunner("先看[2]再看[1]。")
    utils_llm.Runner = runner2
    result2 = asyncio.run(agent.ask("有什么竞赛？"))
    check("B3. [2]→2 号、[1]→1 号（按答案顺序）", [s.notice_id for s in result2.sources] == [2, 1], f"{[s.notice_id for s in result2.sources]}")


def test_stream_layout() -> None:
    runner = FakeRunner("按[1]要求报名。")
    utils_llm.Runner = runner
    agent = QAAgent(index=fake_index(make_docs(3)), max_sources=3, search_mode="vector")

    events = asyncio.run(_collect(agent.ask_stream("有什么竞赛？")))
    prompt = runner.calls[0]
    a, b, c = _prompt_layout(prompt)
    check("B4. 流式同样布局：问题在参考通知之后", a, "")
    check("B4. 流式 Top-1 紧邻问题区", b and c, "")
    done = [p for t, p in events if t == "done"][0]
    check("B4. 流式引用 [1] 映射回 Top-1", [s.notice_id for s in done.sources] == [1], f"{[s.notice_id for s in done.sources]}")


def test_no_citation_fallback() -> None:
    """无编号引用时兜底保留 Top-1（sources 未反转，首元素即最相关）。"""
    agent = QAAgent(index=fake_index(make_docs(3)), max_sources=3, search_mode="vector")
    _, sources = agent._build_context(make_docs(3))
    out = agent._filter_cited_sources("没有引用任何编号。", sources)
    check("B5. 无编号兜底保留 Top-1", [s.notice_id for s in out] == [1], f"{[s.notice_id for s in out]}")


async def _collect(agen):
    events = []
    async for ev in agen:
        events.append(ev)
    return events


def run() -> None:
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass

    print("== 1. _build_context 反转与编号对应 ==")
    _build_context_checks()
    print("\n== 2. ask() 全链路布局与引用映射 ==")
    test_prompt_layout()
    print("\n== 3. ask_stream() 流式布局 ==")
    test_stream_layout()
    print("\n== 4. 无编号兜底 ==")
    test_no_citation_fallback()

    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass

    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()
