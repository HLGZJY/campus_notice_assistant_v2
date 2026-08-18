"""批次 B 验收测试：Fast Path 正则兜底 + 结构化输出硬约束（离线，不调 LLM）。

覆盖：
  1. fast_extract 单元：URL 干净提取（不含中文尾巴）、截止关键词邻近的时间片段、
     公示期区间/纯日期不误触发、空输入返回空、去重保序。
  2. NoticeExtractor._resolve_and_validate 集成：
     - deadline_raw 解析失败 → Fast Path 从正文覆盖，errors 为空（不触发 LLM 重试）；
     - Fast Path 也捞不到 → deadline=None 且无错误；
     - signup_url 非 http(s) → Fast Path 链接覆盖 / 无链接置 None；
     - 合法 signup_url 保留并去掉尾部中文标点。
  3. extract_one 全链路：deadline 被 Fast Path 兜底后仅 1 次 LLM 调用（无重试）。
  4. 结构化输出硬约束：extractor / todo 构造的 Agent 不再注入
     response_format=json_object（extra_body 为空），output_type 保留。

运行：python test_fast_path.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from core.date_utils import fast_extract  # noqa: E402
from core.extractor import NoticeExtractor  # noqa: E402
from core.models import NoticeExtraction  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def test_fast_extract_unit() -> None:
    """fast_extract 单元用例。"""
    r = fast_extract(
        "报名截止时间：即日起至7月16日17：00。报名请访问 https://cwc.scuec.edu.cn/apply 填写。"
    )
    check("U1. URL 干净提取（无中文尾巴）", r.urls == ["https://cwc.scuec.edu.cn/apply"], f"{r.urls}")
    check("U1. 邻近「截止」的时间片段", r.deadlines == ["7月16日17:00"], f"{r.deadlines}")

    r = fast_extract("请于2026年9月1日17:00前完成提交，具体见 https://yjsy.scuec.edu.cn/info/1021/2210.htm。")
    check("U2. 带年份 + 「前」关键词", r.deadlines == ["2026年9月1日17:00"], f"{r.deadlines}")
    check("U2. 路径带数字的 URL 保留", r.urls == ["https://yjsy.scuec.edu.cn/info/1021/2210.htm"], f"{r.urls}")

    r = fast_extract("公示期为8月8日-8月14日，公示期间如有异议请联系 027-67842879。")
    check("U3. 公示期区间不误触发", r.deadlines == [], f"{r.deadlines}")

    r = fast_extract("本次会议将于9月10日在会议中心举行，欢迎参加。")
    check("U4. 纯日期无截止关键词不触发", r.deadlines == [], f"{r.deadlines}")

    r = fast_extract("官网：https://scuec.edu.cn，截止时间为7月1日17:00。")
    check("U5. 时间在关键词后也能命中", r.deadlines == ["7月1日17:00"], f"{r.deadlines}")

    r = fast_extract("报名地址 https://qg.nju.edu.cn/action/show?id=12345678")
    check("U6. 查询串参数保留", r.urls == ["https://qg.nju.edu.cn/action/show?id=12345678"], f"{r.urls}")

    r = fast_extract("两个链接 https://a.cn 和 https://a.cn，去重保留一个。")
    check("U7. 链接去重", r.urls == ["https://a.cn"], f"{r.urls}")

    r = fast_extract("")
    check("U8. 空文本", r == fast_extract(None) and r.urls == [] and r.deadlines == [], f"{r}")
    r = fast_extract(None)
    check("U8. None 文本", r.urls == [] and r.deadlines == [], f"{r}")


def test_resolve_fast_path() -> None:
    """_resolve_and_validate 集成：Fast Path 兜底 + signup_url 校验。"""
    inst = object.__new__(NoticeExtractor)
    ref = date(2026, 7, 1)

    # 1. deadline_raw 解析失败 → Fast Path 覆盖，无错误
    ext = NoticeExtraction(
        title="t", notice_type="registration", deadline_raw="看不懂的截止", summary="s"
    )
    out, errors = inst._resolve_and_validate(ext, ref, "报名截止时间：即日起至7月16日17：00。")
    check("I1. 解析失败被 Fast Path 覆盖", out.deadline == "2026-07-16T17:00:00", f"{out.deadline}")
    check("I1. 不加入重试队列", errors == [], f"{errors}")

    # 2. deadline_raw 解析失败 + Fast Path 也捞不到 → deadline=None 且无错误
    ext = NoticeExtraction(
        title="t", notice_type="registration", deadline_raw="很久以前", summary="s"
    )
    out, errors = inst._resolve_and_validate(ext, ref, "本次会议将在会议中心举行，欢迎参加。")
    check("I2. Fast Path 无命中 → deadline=None", out.deadline is None, f"{out.deadline}")
    check("I2. 无错误不触发重试", errors == [], f"{errors}")

    # 3. deadline_raw 原本可解析 → 正常路径不受影响
    ext = NoticeExtraction(
        title="t", notice_type="registration", deadline_raw="7月16日17：00", summary="s"
    )
    out, errors = inst._resolve_and_validate(ext, ref, "报名截止时间：即日起至7月16日17：00。")
    check("I3. 正常解析路径不受影响", out.deadline == "2026-07-16T17:00:00", f"{out.deadline}")
    check("I3. 无错误", errors == [], f"{errors}")

    # 4. signup_url 非法（无协议）→ Fast Path 链接覆盖
    ext = NoticeExtraction(
        title="t", notice_type="registration", signup_url="www.cwc.scuec.edu.cn", summary="s"
    )
    out, _ = inst._resolve_and_validate(ext, ref, "报名请访问 https://cwc.scuec.edu.cn/apply 填写。")
    check("I4. 非法 signup_url 被 Fast Path 覆盖", out.signup_url == "https://cwc.scuec.edu.cn/apply", f"{out.signup_url}")

    # 5. signup_url 非法 + Fast Path 无链接 → 置 None
    ext = NoticeExtraction(
        title="t", notice_type="registration", signup_url="QQ群：123456", summary="s"
    )
    out, _ = inst._resolve_and_validate(ext, ref, "报名截止时间为8月1日，详情见报名通知。")
    check("I5. 非法 signup_url 无兜底 → None", out.signup_url is None, f"{out.signup_url}")

    # 6. 合法 signup_url 保留 + 去尾部中文标点
    ext = NoticeExtraction(
        title="t", notice_type="registration", signup_url="https://yjsy.scuec.edu.cn/info/1021/2210.htm。", summary="s"
    )
    out, _ = inst._resolve_and_validate(ext, ref, "报名链接见下方。")
    check("I6. 合法 signup_url 保留并去噪声", out.signup_url == "https://yjsy.scuec.edu.cn/info/1021/2210.htm", f"{out.signup_url}")

    # 7. 无 signup_url 字段不受影响
    ext = NoticeExtraction(title="t", notice_type="registration", signup_method="QQ群报名", summary="s")
    out, _ = inst._resolve_and_validate(ext, ref, "报名截止时间为8月1日。")
    check("I7. 无 signup_url 不注入", out.signup_url is None, f"{out.signup_url}")


def test_no_retry_on_fast_path() -> None:
    """extract_one 全链路：Fast Path 兜底后仅 1 次 LLM 调用，无校验重试。"""
    import core.extractor as core_extractor

    calls: list[int] = []

    async def fake_run(agent, prompt, **kwargs):
        calls.append(kwargs["attempt"])
        return SimpleNamespace(
            final_output=NoticeExtraction(
                title="t",
                notice_type="registration",
                deadline_raw="看不懂的截止",
                signup_method="QQ群",
                summary="s",
            )
        )

    core_extractor.run_agent = fake_run
    extractor = core_extractor.NoticeExtractor.__new__(core_extractor.NoticeExtractor)
    extractor.provider = "p"
    extractor.models = ["m"]
    extractor._agents = {}
    extractor._get_agent = lambda model: object()
    out = asyncio.run(
        extractor.extract_one(
            "标题",
            "报名截止时间：即日起至7月16日17：00。",
            published_at="2026-07-01T00:00:00",
        )
    )
    check("F1. 仅 1 次 LLM 调用（无重试）", calls == [0], f"{calls}")
    check("F1. Fast Path 覆盖 deadline", out.extraction is not None and out.extraction.deadline == "2026-07-16T17:00:00", f"{out.extraction and out.extraction.deadline}")


def test_structured_output_hard_constraint() -> None:
    """B3：Agent 构造不再注入 json_object（extra_body 为空），output_type 保留。"""
    import core.extractor as core_extractor
    import core.todo as core_todo
    from core.models import TodoList

    def noop(*args, **kwargs):
        return None

    captured_ext: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured_ext.update(kwargs)

    core_extractor.Agent = FakeAgent
    core_extractor.AsyncOpenAI = lambda **kw: object()
    core_extractor.set_tracing_disabled = noop
    core_extractor.set_default_openai_client = noop
    core_extractor.set_default_openai_api = noop

    e = core_extractor.NoticeExtractor.__new__(core_extractor.NoticeExtractor)
    e.api_key = "k"
    e.base_url = "https://x/v1"
    e._agents = {}
    e._get_agent("m")
    settings = captured_ext["model_settings"]
    check("S1. extractor output_type 保留", captured_ext["output_type"] is NoticeExtraction)
    check("S1. extractor 不再注入 json_object（extra_body 为空）", settings.extra_body is None, f"{settings.extra_body!r}")

    captured_todo: dict = {}

    class FakeTodoAgent:
        def __init__(self, **kwargs):
            captured_todo.update(kwargs)

    core_todo.Agent = FakeTodoAgent
    core_todo.AsyncOpenAI = lambda **kw: object()
    core_todo.set_tracing_disabled = noop
    core_todo.set_default_openai_client = noop
    core_todo.set_default_openai_api = noop

    g = core_todo.TodoGenerator.__new__(core_todo.TodoGenerator)
    g.api_key = "k"
    g.base_url = "https://x/v1"
    g.temperature = 0.0
    g._agents = {}
    g._get_agent("m")
    t_settings = captured_todo["model_settings"]
    check("S2. todo output_type 保留", captured_todo["output_type"] is TodoList)
    check("S2. todo 不再注入 json_object（extra_body 为空）", t_settings.extra_body is None, f"{t_settings.extra_body!r}")
    check("S2. todo temperature 保留", t_settings.temperature == 0.0, f"{t_settings.temperature!r}")


def run() -> None:
    print("== 1. fast_extract 单元 ==")
    test_fast_extract_unit()
    print("\n== 2. _resolve_and_validate Fast Path 集成 ==")
    test_resolve_fast_path()
    print("\n== 3. extract_one 全链路无重试 ==")
    test_no_retry_on_fast_path()
    print("\n== 4. 结构化输出硬约束（B3） ==")
    test_structured_output_hard_constraint()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()
