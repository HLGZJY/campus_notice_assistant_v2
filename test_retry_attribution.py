"""批次 C 验收测试：重试归因分析（离线，不调 LLM）。

覆盖：
  1. 格式错误（deadline_raw 无法解析且 Fast Path 无命中）→ 不写入 errors，
     仅 1 次 LLM 调用（不触发重试）。
  2. 逻辑冲突（截止时间早于发布时间）→ 写入 errors，触发 LLM 重试，
     二次修正后成功（共 2 次调用）。
  3. 无冲突正常路径 → 0 重试。
  4. _call 在 error_msg 后追加固定【参考格式】正例样例。
  5. published_at 缺失/不可解析时冲突检查跳过（不误报）。
  6. 逻辑冲突的错误文案包含"早于发布时间"。

运行：python test_retry_attribution.py
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

import core.extractor as core_extractor  # noqa: E402
from core.extractor import NoticeExtractor  # noqa: E402
from core.models import NoticeExtraction  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def make_extractor(fake_run, models=("m",)) -> NoticeExtractor:
    """构造绕过 __init__ 的提取器：固定候选模型 + 替身 run_agent。"""
    core_extractor.run_agent = fake_run
    inst = core_extractor.NoticeExtractor.__new__(core_extractor.NoticeExtractor)
    inst.provider = "p"
    inst.models = list(models)
    inst._agents = {}
    inst._usage_cb = None
    inst._get_agent = lambda model: object()
    return inst


def seq_runner(exts: list[NoticeExtraction]):
    """按调用次序依次返回预置提取结果，并记录 (attempt, prompt)。"""

    async def fake_run(agent, prompt, **kwargs):
        fake_run.calls.append((kwargs["attempt"], prompt))
        out = exts[fake_run.n]
        fake_run.n += 1
        return SimpleNamespace(final_output=out)

    fake_run.calls = []
    fake_run.n = 0
    return fake_run


def test_format_error_no_retry() -> None:
    """格式错误（解析失败 + Fast Path 无命中）→ 仅 1 次调用，无重试。"""
    fake_run = seq_runner(
        [
            NoticeExtraction(
                title="t", notice_type="registration",
                deadline_raw="很久以前", summary="s",
            )
        ]
    )
    inst = make_extractor(fake_run)
    out = asyncio.run(
        inst.extract_one(
            "标题",
            "本次会议将在会议中心举行，欢迎参加。",
            published_at="2026-07-01T00:00:00",
        )
    )
    check("A1. 格式错误仅 1 次 LLM 调用", [c[0] for c in fake_run.calls] == [0], f"{[c[0] for c in fake_run.calls]}")
    check("A1. deadline 置 None", out.extraction is not None and out.extraction.deadline is None, f"{out.extraction and out.extraction.deadline}")
    check("A1. 无错误不阻塞（保留 LLM 字段）", out.status == "extracted" and out.error is None, f"{out.status}")


def test_logic_conflict_triggers_retry() -> None:
    """逻辑冲突（截止早于发布）→ 触发重试，二次修正后成功。"""
    fake_run = seq_runner(
        [
            # 第一次：LLM 显式写错年份（2025），发布时间是 2026
            NoticeExtraction(
                title="t", notice_type="registration",
                deadline_raw="2025年7月16日17:00", summary="s",
            ),
            # 第二次：LLM 修正为 2026
            NoticeExtraction(
                title="t", notice_type="registration",
                deadline_raw="2026年8月1日17:00", summary="s",
            ),
        ]
    )
    inst = make_extractor(fake_run)
    out = asyncio.run(
        inst.extract_one(
            "标题",
            "报名截止时间：2025年7月16日17:00。",
            published_at="2026-07-01T00:00:00",
        )
    )
    check("A2. 冲突触发重试（2 次调用）", [c[0] for c in fake_run.calls] == [0, 1], f"{[c[0] for c in fake_run.calls]}")
    check("A2. 重试 prompt 带冲突错误", "早于发布时间" in fake_run.calls[1][1], "")
    check("A2. 二次修正后成功", out.extraction is not None and out.extraction.deadline == "2026-08-01T17:00:00", f"{out.extraction and out.extraction.deadline}")
    check("A2. 状态 extracted", out.status == "extracted", f"{out.status}")


def test_no_conflict_no_retry() -> None:
    """截止晚于发布时间 → 0 重试。"""
    fake_run = seq_runner(
        [
            NoticeExtraction(
                title="t", notice_type="registration",
                deadline_raw="2026年8月1日17:00", summary="s",
            )
        ]
    )
    inst = make_extractor(fake_run)
    out = asyncio.run(
        inst.extract_one(
            "标题",
            "报名截止时间：2026年8月1日17:00。",
            published_at="2026-07-01T00:00:00",
        )
    )
    check("A3. 无冲突仅 1 次调用", [c[0] for c in fake_run.calls] == [0], f"{[c[0] for c in fake_run.calls]}")
    check("A3. 正常提取成功", out.status == "extracted" and out.extraction is not None, f"{out.status}")


def test_call_appends_format_sample() -> None:
    """_call 在 error_msg 后追加【参考格式】正例样例。"""
    captured: dict = {}

    async def fake_run(agent, prompt, **kwargs):
        captured["prompt"] = prompt
        return SimpleNamespace(
            final_output=NoticeExtraction(title="t", notice_type="registration", summary="s")
        )

    core_extractor.run_agent = fake_run
    inst = core_extractor.NoticeExtractor.__new__(core_extractor.NoticeExtractor)
    inst.provider = "p"
    inst.models = ["m"]
    inst._agents = {}
    inst._usage_cb = None
    inst._get_agent = lambda model: object()

    asyncio.run(inst._call("m", "原始PROMPT", "截止时间 2025-07-16T17:00:00 早于发布时间 2026-07-01T00:00:00", attempt=1))
    p = captured["prompt"]
    check("B1. 保留原 prompt", "原始PROMPT" in p, "")
    check("B1. 保留错误信息", "早于发布时间" in p, "")
    check(
        "B1. 追加固定正例样例",
        '【参考格式】deadline_raw: "7月16日17:00", deadline: "2026-07-16T17:00:00"' in p,
        "",
    )
    check("B1. 样例位于错误信息之后", p.index("【参考格式】") > p.index("早于发布时间"), "")


def test_resolve_conflict_unit() -> None:
    """_resolve_and_validate 单元：冲突判定 + 边界。"""
    inst = object.__new__(NoticeExtractor)
    ref = date(2026, 7, 1)

    # 冲突：显式年份早于发布时间
    ext = NoticeExtraction(
        title="t", notice_type="registration", deadline_raw="2025年7月16日17:00", summary="s"
    )
    out, errors = inst._resolve_and_validate(ext, ref, None, "2026-07-01T00:00:00")
    check("C1. 冲突 → deadline 保持原值", out.deadline == "2025-07-16T17:00:00", f"{out.deadline}")
    check("C1. 冲突 → 写入 errors", len(errors) == 1 and "早于发布时间" in errors[0], f"{errors}")

    # 无冲突：截止晚于发布
    ext = NoticeExtraction(
        title="t", notice_type="registration", deadline_raw="2026年8月1日17:00", summary="s"
    )
    out, errors = inst._resolve_and_validate(ext, ref, None, "2026-07-01T00:00:00")
    check("C2. 晚于发布 → 无 errors", errors == [], f"{errors}")

    # 同日截止（09:00 vs 00:00 发布）不算冲突
    ext = NoticeExtraction(
        title="t", notice_type="registration", deadline_raw="2026年7月1日9:00", summary="s"
    )
    out, errors = inst._resolve_and_validate(ext, ref, None, "2026-07-01T00:00:00")
    check("C3. 同日截止 → 无 errors", errors == [], f"{errors}")

    # published_at 不可解析 → 跳过检查
    ext = NoticeExtraction(
        title="t", notice_type="registration", deadline_raw="2025年7月16日17:00", summary="s"
    )
    out, errors = inst._resolve_and_validate(ext, ref, None, "not-a-date")
    check("C4. published_at 非法 → 不误报", errors == [], f"{errors}")

    # published_at 缺失 → 向后兼容
    ext = NoticeExtraction(
        title="t", notice_type="registration", deadline_raw="2025年7月16日17:00", summary="s"
    )
    out, errors = inst._resolve_and_validate(ext, ref, None)
    check("C5. published_at=None → 不误报", errors == [], f"{errors}")

    # 无 deadline → 无冲突检查
    ext = NoticeExtraction(title="t", notice_type="policy", summary="s")
    out, errors = inst._resolve_and_validate(ext, ref, None, "2026-07-01T00:00:00")
    check("C6. 无 deadline → 无 errors", errors == [], f"{errors}")


def run() -> None:
    print("== 1. 格式错误不触发重试（全链路） ==")
    test_format_error_no_retry()
    print("\n== 2. 逻辑冲突触发重试（全链路） ==")
    test_logic_conflict_triggers_retry()
    print("\n== 3. 无冲突正常路径 ==")
    test_no_conflict_no_retry()
    print("\n== 4. _call 正例格式样例 ==")
    test_call_appends_format_sample()
    print("\n== 5. _resolve_and_validate 冲突判定单元 ==")
    test_resolve_conflict_unit()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()
