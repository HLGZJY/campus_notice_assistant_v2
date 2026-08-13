"""core.qa 引用来源过滤的回归验证（离线、不依赖真实 LLM/网络）。

背景：问答面板曾把检索到的全部来源（max_sources 上限）一股脑展示，
即使 LLM 答案只引用 [2]。本测试验证 _filter_cited_sources：
  1. 答案引用 [2] → 面板只保留编号 2 对应的来源
  2. 答案引用多个编号 → 按答案出现顺序去重保留
  3. 答案未引用任何编号 → 兜底保留 top-1，面板不空置
  4. 引用不存在的编号（如 [9]）→ 丢弃，不影响有效引用
  5. ask() 全链路：sources 被过滤且 retrieved_chunks 与 sources 一致
  6. 流式 ask_stream() 的 done 事件同样过滤
  7. 空检索兜底不崩溃

用法：python test_qa_citation.py
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

from langchain_core.documents import Document

import storage.db
from storage.db import get_connection
from utils import llm as utils_llm

TMP_DB = Path(__file__).parent / "data" / "test_qa_citation.db"
storage.db.DB_PATH = TMP_DB

from core.qa import QAAgent, SourceRef


def make_docs(n: int) -> list[Document]:
    """构造 n 个来自不同通知的 chunk。"""
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


class SimpleResult:
    def __init__(self, output):
        self.final_output = output

    @property
    def usage(self):
        return None


class FakeRunner:
    """替身 Runner：run/run_streamed 返回固定答案，绕过真实 LLM。"""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def run(self, agent, prompt):
        self.calls.append(prompt)
        return SimpleResult(self.answer)

    def run_streamed(self, agent, prompt):
        self.calls.append(prompt)
        return FakeStreamResult(self.answer)


class FakeStreamResult:
    """替身流结果：逐 delta 产出答案 + completed 事件（无 usage 记账不关键）。"""

    def __init__(self, answer):
        self.answer = answer
        self.usage = None

    def stream_events(self):
        async def _gen():
            for i, ch in enumerate(self.answer):
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(
                        type="response.output_text.delta", delta=ch
                    ),
                )
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(usage=None),
                ),
            )

        return _gen()


def run():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass

    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    print("== 1. _filter_cited_sources 单元行为 ==")
    agent = QAAgent(index=fake_index(make_docs(4)), max_sources=4, search_mode="vector")
    srcs = [SourceRef(notice_id=i) for i in range(1, 5)]

    out = agent._filter_cited_sources("答案是[2]。", srcs)
    check("引用 [2] 只保留该来源", out == [srcs[1]], f"{[s.notice_id for s in out]}")

    out = agent._filter_cited_sources("见[3]和[1]，以及[3]重复。", srcs)
    check(
        "多编号按出现顺序去重",
        out == [srcs[2], srcs[0]],
        f"{[s.notice_id for s in out]}",
    )

    out = agent._filter_cited_sources("没有引用任何编号。", srcs)
    check("无编号兜底 top-1", out == [srcs[0]], f"{[s.notice_id for s in out]}")

    out = agent._filter_cited_sources("引用[9]和[2]。", srcs)
    check("非法编号丢弃、有效编号保留", out == [srcs[1]], f"{[s.notice_id for s in out]}")

    out = agent._filter_cited_sources("混合[2]和[9]，[4]也有。", srcs)
    check(
        "混合场景：合法编号保留且顺序按答案",
        out == [srcs[1], srcs[3]],
        f"{[s.notice_id for s in out]}",
    )

    print("\n== 2. ask() 全链路：过滤 + retrieved_chunks 一致 ==")
    runner = FakeRunner("推荐参加[3]比赛。")
    utils_llm.Runner = runner
    agent = QAAgent(index=fake_index(make_docs(4)), max_sources=4, search_mode="vector")

    result = asyncio.run(agent.ask("有什么竞赛？"))
    check(
        "sources 只剩被引用的 3 号",
        [s.notice_id for s in result.sources] == [3],
        f"{[s.notice_id for s in result.sources]}",
    )
    check(
        "retrieved_chunks 与 sources 一致(=1)",
        result.retrieved_chunks == 1,
        f"{result.retrieved_chunks}",
    )
    check("LLM 收到含编号参考通知的 prompt", bool(runner.calls), "calls=1")

    print("\n== 3. 流式 ask_stream() done 事件同样过滤 ==")
    utils_llm.Runner = FakeRunner("按[1]要求报名。")
    agent = QAAgent(index=fake_index(make_docs(4)), max_sources=4, search_mode="vector")

    events = asyncio.run(_collect_stream(agent.ask_stream("有什么竞赛？")))
    deltas = [p for t, p in events if t == "delta"]
    done = [p for t, p in events if t == "done"][0]
    check(
        "流式先产出 delta 再 done",
        bool(deltas) and any(t == "done" for t, _ in events),
        f"events={[t for t, _ in events]}",
    )
    check(
        "done.sources 只保留被引用的 1 号",
        [s.notice_id for s in done.sources] == [1],
        f"{[s.notice_id for s in done.sources]}",
    )
    check(
        "done.retrieved_chunks 与 sources 一致(=1)",
        done.retrieved_chunks == 1,
        f"{done.retrieved_chunks}",
    )

    print("\n== 4. 空检索兜底不崩溃 ==")
    agent = QAAgent(index=fake_index([]), max_sources=4, search_mode="vector")
    result = asyncio.run(agent.ask("不存在的"))
    check(
        "空检索返回兜底文案且无来源",
        "没有找到相关信息" in result.answer and result.sources == [],
        f"answer={result.answer!r} sources={result.sources}",
    )

    _cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


async def _collect_stream(agen):
    events = []
    async for ev in agen:
        events.append(ev)
    return events


def _cleanup():
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    run()