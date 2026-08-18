"""模块 1.4 token 计量表的验收验证（离线、不依赖真实 LLM/网络）。

覆盖验收信号：
  1. 成功调用：token_usage 记录 input/output 与 mock usage 相符，task/model/notice_id 正确
  2. 重试区分：attempt 递增 → retry_count 0/1 能区分首调与重试，且重试也记账
  3. 失败也记账：success=0、error 记录，异常照常上抛（不影响原重试流程）
  4. 三条链路自动覆盖：extraction / todo / qa 统一走 utils.llm.run_agent
  5. embedding 链路：OpenAI-compatible 路径读取 usage.prompt_tokens 记账；
     本地路径记 count-only（tokens=0）
  6. get_token_usage_summary 近 N 天按任务 × 模型分组汇总正确

用法：python test_token_usage.py
"""
import asyncio
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.CRITICAL, format="%(levelname)s %(message)s")

import requests

import storage.db
from storage.db import get_connection, get_token_usage_summary
from utils import llm as utils_llm
from utils.llm import record_llm_usage, run_agent
from utils.embedding import _CountingEmbeddings, _MeteredOpenAIEmbeddings

TMP_DIR = tempfile.mkdtemp(prefix="wb_test_token_usage_")
TMP_DB = Path(TMP_DIR) / "test_token_usage.db"

storage.db.DB_PATH = TMP_DB


class FakeUsage:
    def __init__(self, input_tokens=0, output_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, input_tokens=0, output_tokens=0):
        self.usage = FakeUsage(input_tokens, output_tokens)


class FakeResult:
    def __init__(self, responses, final_output=None):
        self.raw_responses = responses
        self.final_output = final_output


class FakeRunner:
    """替身 Runner：记录调用，返回带 usage 的假结果。"""

    def __init__(self, result=None, error=None):
        self.result = result or FakeResult([FakeResponse(100, 50)])
        self.error = error
        self.calls = []

    async def run(self, agent, prompt):
        self.calls.append((agent, prompt))
        if self.error is not None:
            raise self.error
        return self.result


def reset_db():
    """删除临时库，下次 get_connection() 自动重建 SCHEMA（含 token_usage 表）。"""
    try:
        if TMP_DB.exists():
            TMP_DB.unlink()
    except OSError:
        pass


def run():
    reset_db()
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    print("== 1. record_llm_usage 直写 ==")
    record_llm_usage(
        task="extraction",
        model="m1",
        input_tokens=120,
        output_tokens=30,
        retry_count=2,
        notice_id=1,
        provider="p1",
    )
    conn = get_connection()
    row = conn.execute("SELECT * FROM token_usage WHERE notice_id = 1").fetchone()
    check(
        "字段完整且数值正确",
        row and row["task"] == "extraction" and row["model"] == "m1"
        and row["provider"] == "p1"
        and row["input_tokens"] == 120 and row["output_tokens"] == 30
        and row["retry_count"] == 2 and row["success"] == 1,
        f"task={row['task'] if row else None}",
    )

    print("\n== 2. run_agent 成功：usage 累加 + 参数记账 ==")
    utils_llm.Runner = FakeRunner(
        FakeResult([FakeResponse(100, 50), FakeResponse(30, 20)], final_output="ok")
    )
    result = asyncio.run(
        run_agent(None, "p", task="extraction", model="m2", attempt=2, notice_id=2, provider="p2")
    )
    check("返回 RunResult", result.final_output == "ok")
    row = conn.execute(
        "SELECT * FROM token_usage WHERE notice_id = 2 AND task = 'extraction'"
    ).fetchone()
    check(
        "input/output 与 mock usage 相符（130/70）",
        row and row["input_tokens"] == 130 and row["output_tokens"] == 70,
        f"in={row['input_tokens']} out={row['output_tokens']}",
    )
    check(
        "task/model/notice_id/retry_count/provider 正确",
        row and row["model"] == "m2" and row["retry_count"] == 2 and row["success"] == 1
        and row["provider"] == "p2",
    )

    print("\n== 3. run_agent 失败：记账 success=0 + 异常上抛 ==")
    utils_llm.Runner = FakeRunner(error=RuntimeError("boom"))
    raised = False
    try:
        asyncio.run(run_agent(None, "p", task="qa", model="m3", attempt=1, provider="p3"))
    except RuntimeError as e:
        raised = "boom" in str(e)
    check("异常照常上抛", raised)
    row = conn.execute(
        "SELECT * FROM token_usage WHERE task = 'qa' AND model = 'm3'"
    ).fetchone()
    check(
        "失败记账：success=0、error 含 RuntimeError、tokens=0、provider 保留",
        row and row["success"] == 0 and row["error"].startswith("RuntimeError")
        and row["input_tokens"] == 0 and row["retry_count"] == 1 and row["provider"] == "p3",
        f"success={row['success'] if row else None}",
    )

    print("\n== 4. 提取链路：extractor._call 走 run_agent(task=extraction) ==")
    import core.extractor as core_extractor
    from core.models import NoticeExtraction

    calls = []

    async def fake_extract_run(agent, prompt, **kwargs):
        calls.append(kwargs)
        return FakeResult(
            [], final_output=NoticeExtraction(title="t", notice_type="competition", summary="s")
        )

    core_extractor.run_agent = fake_extract_run
    extractor = core_extractor.NoticeExtractor.__new__(core_extractor.NoticeExtractor)
    extractor.provider = "extract-prov"
    extractor.models = ["extract-model"]
    extractor._agents = {}
    extractor._usage_cb = None
    extractor._get_agent = lambda model: object()
    out = asyncio.run(extractor._call("extract-model", "prompt", None, attempt=1, notice_id=7))
    check("返回 NoticeExtraction", isinstance(out, NoticeExtraction))
    check(
        "task/attempt/notice_id/provider 传入统一调用点",
        calls
        == [
            {
                "task": "extraction",
                "model": "extract-model",
                "attempt": 1,
                "notice_id": 7,
                "provider": "extract-prov",
                "usage_cb": None,
            }
        ],
        f"{calls}",
    )

    print("\n== 5. 待办链路：重试也记账，能区分首调(retry_count=0)与重试(retry_count=1) ==")
    import core.todo as core_todo
    from core.models import TodoItem, TodoList

    calls = []

    class FlakyRun:
        def __init__(self):
            self.n = 0

        async def __call__(self, agent, prompt, **kwargs):
            calls.append(kwargs)
            self.n += 1
            if self.n == 1:
                raise RuntimeError("temp fail")
            return FakeResult(
                [],
                final_output=TodoList(items=[TodoItem(action="在 X 前报名", due_at=None, priority="normal")]),
            )

    core_todo.run_agent = FlakyRun()
    gen = core_todo.TodoGenerator.__new__(core_todo.TodoGenerator)
    gen.provider = "todo-prov"
    gen.models = ["todo-model"]
    gen._agents = {}
    gen._usage_cb = None
    gen._get_agent = lambda model: object()
    notice = {
        "id": 3,
        "title": "工创大赛",
        "notice_type": "competition",
        "deadline": "2026-12-31T17:00:00",
        "deadline_raw": "12月31日17:00",
        "target_audience": None,
        "signup_method": "QQ群报名",
        "signup_url": None,
        "location": None,
        "summary": "报名",
    }
    items = asyncio.run(gen.generate_one(notice))
    check("首调失败后重试成功返回待办", len(items) == 1)
    check(
        "两次调用 attempt 递增（首调0 → 重试1）",
        [c["attempt"] for c in calls] == [0, 1],
        f"attempts={[c['attempt'] for c in calls]}",
    )
    check(
        "每次调用都带 task=todo / notice_id=3",
        all(c["task"] == "todo" and c["notice_id"] == 3 for c in calls),
        f"{calls}",
    )

    print("\n== 6. 问答链路：qa.ask 走 run_agent(task=qa) ==")
    import core.qa as core_qa

    calls = []

    async def fake_qa_run(agent, prompt, **kwargs):
        calls.append(kwargs)
        return FakeResult([], final_output="测试回答")

    core_qa.run_agent = fake_qa_run
    qa = core_qa.QAAgent.__new__(core_qa.QAAgent)
    qa.provider = "qa-prov"
    qa.models = ["qa-model"]
    qa._agents = {}
    qa.top_k = 6
    qa.max_sources = 5
    qa.search_mode = "vector"
    qa.strategy = "none"
    qa.expire_days = None
    qa.search_kwargs = {}
    qa._usage_cb = None
    qa._get_agent = lambda model: object()
    qa.index = SimpleNamespace(
        search=lambda question, k, **kwargs: [
            SimpleNamespace(
                metadata={
                    "notice_id": 1,
                    "title": "通知A",
                    "notice_type": "competition",
                    "source": "s",
                    "url": "u",
                    "deadline": "2026-12-31",
                },
                page_content="内容摘要",
            )
        ]
    )
    res = asyncio.run(qa.ask("有哪些比赛？"))
    check("回答返回", res.answer == "测试回答")
    check(
        "qa 链路 task/model/provider 正确（attempt/notice_id 走 run_agent 默认值）",
        calls == [{"task": "qa", "model": "qa-model", "provider": "qa-prov", "usage_cb": None}],
        f"{calls}",
    )

    print("\n== 7. embedding（OpenAI-compatible）：读 usage.prompt_tokens 记账 ==")
    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "data": [
                    {"embedding": [0.1, 0.2], "index": 0},
                    {"embedding": [0.3, 0.4], "index": 1},
                ],
                "usage": {"prompt_tokens": 200, "total_tokens": 200},
            }

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    requests.post = fake_post
    emb = _MeteredOpenAIEmbeddings("https://fake.emb/v1", "k", "emb-model", "emb-prov")
    vecs = emb.embed_documents(["a", "b"])
    check("向量按 index 排序返回", vecs == [[0.1, 0.2], [0.3, 0.4]])
    check(
        "请求体 model + input 正确",
        captured["json"]["model"] == "emb-model" and captured["json"]["input"] == ["a", "b"],
        f"{captured['json']}",
    )
    row = conn.execute(
        "SELECT * FROM token_usage WHERE task='embedding' AND model='emb-model' AND success=1"
    ).fetchone()
    check(
        "embedding 记账 input_tokens=200 + provider",
        row and row["input_tokens"] == 200 and row["provider"] == "emb-prov",
    )

    v = emb.embed_query("q")
    check("embed_query 返回单个向量", v == [0.1, 0.2])

    print("\n== 8. embedding 失败：记账 success=0 + 上抛 ==")

    def failing_post(url, headers=None, json=None, timeout=None):
        raise ConnectionError("conn refused")

    requests.post = failing_post
    raised = False
    try:
        emb.embed_documents(["x"])
    except ConnectionError:
        raised = True
    check("embedding 失败上抛 ConnectionError", raised)
    row = conn.execute(
        "SELECT * FROM token_usage WHERE task='embedding' AND success=0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    check(
        "embedding 失败记账 success=0 且带 error",
        row and row["error"].startswith("ConnectionError"),
        f"error={row['error'][:40] if row else None}",
    )

    print("\n== 9. embedding（本地）：count-only，tokens=0 ==")
    class FakeLocal:
        def embed_documents(self, texts):
            return [[1.0, 1.0]]

        def embed_query(self, text):
            return [1.0, 1.0]

    ce = _CountingEmbeddings(FakeLocal(), "local-model", "local-prov")
    ce.embed_documents(["a"])
    ce.embed_query("q")
    rows = conn.execute(
        "SELECT * FROM token_usage WHERE task='embedding' AND model='local-model'"
    ).fetchall()
    check(
        "本地 embedding 记 count-only 两条（input/output=0）+ provider",
        len(rows) == 2
        and all(
            r["input_tokens"] == 0 and r["output_tokens"] == 0 and r["provider"] == "local-prov"
            for r in rows
        ),
    )

    print("\n== 10. get_token_usage_summary 近 7 天分组汇总 ==")
    total_all = conn.execute("SELECT COUNT(*) AS n FROM token_usage").fetchone()["n"]
    summary = get_token_usage_summary(conn, days=7)
    check("summary.total.calls == 表内全部记录数", summary["total"]["calls"] == total_all, f"{summary['total']['calls']} vs {total_all}")
    check(
        "按任务×供应商×模型分组行数与 (task,provider,model) 组合数一致",
        len(summary["rows"]) == conn.execute(
            "SELECT COUNT(*) AS n FROM (SELECT DISTINCT task, COALESCE(provider,''), model FROM token_usage)"
        ).fetchone()["n"],
        f"rows={len(summary['rows'])}",
    )
    # 各分组 input 之和 == 总 input
    group_input = sum(r["input_tokens"] for r in summary["rows"])
    check("分组 input 之和 == total.input_tokens", group_input == summary["total"]["input_tokens"])
    # 分组行含 provider 与 task_label（服务层补中文标签）
    emb_group = next((r for r in summary["rows"] if r["task"] == "embedding" and r["model"] == "emb-model"), None)
    check(
        "分组行带 provider",
        emb_group is not None and emb_group["provider"] == "emb-prov",
        f"{emb_group}",
    )
    from services.usage_service import get_token_usage_summary as service_summary

    svc = service_summary(days=7)
    svc_emb = next((r for r in svc["rows"] if r["task"] == "embedding" and r["model"] == "emb-model"), None)
    check(
        "服务层 task_label 中文标签",
        svc_emb is not None
        and svc_emb["task_label"] == "Embedding"
        and svc["total"]["calls"] == summary["total"]["calls"],
        f"{svc_emb}",
    )
    conn.close()

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
