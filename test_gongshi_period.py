"""公示期确定性提取测试（离线，不调 LLM）。

覆盖：
  1. extract_gongshi_period 单位用例：参考两则真实公示（教务处/研究生院）、
     缺年份起止、全角连接符、跨年推断、单日、无匹配返回 []。
  2. NoticeExtractor._resolve_and_validate 集成：result 类正文含公示期 →
     key_dates 前置「公示期开始/结束」且 datetime 正确、移除 LLM 重复条目、
     其余 key_dates 正常解析；非公示期正文不受影响。

运行：
    python test_gongshi_period.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# 项目根（与引擎脚本保持一致）
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from core.date_utils import extract_gongshi_period
from core.extractor import NoticeExtractor
from core.models import KeyDate, NoticeExtraction

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def test_unit():
    """extract_gongshi_period 单位用例。"""
    ref = date(2026, 8, 8)

    # 教务处参考：公示期为8月8日-8月14日
    got = extract_gongshi_period(
        "公示期为8月8日-8月14日，公示期间如有异议，请以书面形式实名向本科生院反映。联系电话：67842879。",
        ref,
    )
    check("U1. 教务处公示期拆两条", len(got) == 2, f"{len(got)}")
    if len(got) == 2:
        check(
            "U1. 开始日期正确",
            got[0]["label"] == "公示期开始"
            and got[0]["date_raw"] == "8月8日"
            and got[0]["datetime"] == "2026-08-08T00:00:00",
            f"{got[0]}",
        )
        check(
            "U1. 结束日期正确",
            got[1]["label"] == "公示期结束"
            and got[1]["date_raw"] == "8月14日"
            and got[1]["datetime"] == "2026-08-14T00:00:00",
            f"{got[1]}",
        )

    # 研究生院参考：公示期从2025年11月3日至2025年11月10日
    got = extract_gongshi_period(
        "公示期从2025年11月3日至2025年11月10日。凡对公示结果有异议者，请以书面方式向研究生院反映。联系电话：027-67843298。",
        ref,
    )
    check("U2. 研究生院公示期拆两条", len(got) == 2, f"{len(got)}")
    if len(got) == 2:
        check(
            "U2. 带年份起止日期正确",
            got[0]["datetime"] == "2025-11-03T00:00:00"
            and got[1]["datetime"] == "2025-11-10T00:00:00",
            f"{got}",
        )

    # 全角冒号 + 止缺年份
    got = extract_gongshi_period("公示期：2025年11月3日至11月10日", ref)
    check(
        "U3. 止缺年份以起日为参考推断",
        len(got) == 2
        and got[0]["datetime"] == "2025-11-03T00:00:00"
        and got[1]["datetime"] == "2025-11-10T00:00:00",
        f"{got}",
    )

    # 跨年：自X日起至X日止
    got = extract_gongshi_period("公示期自2025年12月28日起至1月3日止", ref)
    check(
        "U4. 跨年推断进下一年",
        len(got) == 2
        and got[0]["datetime"] == "2025-12-28T00:00:00"
        and got[1]["datetime"] == "2026-01-03T00:00:00",
        f"{got}",
    )

    # 单日兜底
    got = extract_gongshi_period("公示期为2025年12月31日", ref)
    check(
        "U5. 单日公示期一条",
        len(got) == 1
        and got[0]["label"] == "公示期"
        and got[0]["datetime"] == "2025-12-31T00:00:00",
        f"{got}",
    )

    # 无公示期
    got = extract_gongshi_period("本次报名截止日为8月8日，欢迎踊跃参加。", ref)
    check("U6. 无公示期返回空", got == [], f"{got}")

    got = extract_gongshi_period("", ref)
    check("U6. 空文本返回空", got == [])

    # 真实数据格式："公示异议期：2026年7月1日--7月4日"（双连字符 + 异议期前缀）
    got = extract_gongshi_period(
        "一、公示异议期：2026年7月1日--7月4日\n二、异议申诉及处理程序如下：",
        date(2026, 7, 1),
    )
    check(
        "U7. 公示异议期 + 双连字符",
        len(got) == 2
        and got[0]["datetime"] == "2026-07-01T00:00:00"
        and got[1]["datetime"] == "2026-07-04T00:00:00",
        f"{got}",
    )

    got = extract_gongshi_period(
        "一、公示异议期：2026年6月23日--6月25日。\n二、异议申诉及处理程序如下：",
        date(2026, 6, 23),
    )
    check(
        "U8. 公示异议期止缺年份",
        len(got) == 2
        and got[0]["datetime"] == "2026-06-23T00:00:00"
        and got[1]["datetime"] == "2026-06-25T00:00:00",
        f"{got}",
    )

    # 正文里无日期的"公示期内"不应误触发
    got = extract_gongshi_period(
        "公示期内向大赛组委会提出书面申请，方可进行修正。公示期结束，大赛平台关闭。",
        date(2026, 7, 1),
    )
    check("U9. 无日期「公示期内」不误触发", got == [], f"{got}")


def test_integration():
    """_resolve_and_validate 集成：公示期合并去重 + 其余 key_dates 解析。"""
    inst = object.__new__(NoticeExtractor)  # 只测后处理，不触发模型初始化
    ref = date(2026, 8, 8)

    content = (
        "关于2026年度拟申报新增本科专业的公示。公示期为8月8日-8月14日，"
        "公示期间如有异议，请以书面形式实名向本科生院反映。结果将于8月16日公布。"
    )
    ext = NoticeExtraction(
        notice_type="result",
        title="关于2026年度拟申报新增本科专业的公示",
        key_dates=[
            KeyDate(label="公示期", date_raw="8月8日-8月14日", datetime=None),
            KeyDate(label="结果公布", date_raw="8月16日", datetime=None),
        ],
    )
    out, errors = inst._resolve_and_validate(ext, ref, content)
    check("I1. 无校验错误", not errors, f"{errors}")
    check("I2. 公示期两条前置", len(out.key_dates) == 3, f"{[kd.label for kd in out.key_dates]}")
    if len(out.key_dates) >= 2:
        check(
            "I2. 公示期开始/结束确定性覆盖",
            out.key_dates[0].label == "公示期开始"
            and out.key_dates[0].datetime == "2026-08-08T00:00:00"
            and out.key_dates[1].label == "公示期结束"
            and out.key_dates[1].datetime == "2026-08-14T00:00:00",
            f"{[(k.label, k.datetime) for k in out.key_dates[:2]]}",
        )
    check(
        "I3. 移除 LLM 重复「公示期」条目",
        all("公示期" not in (kd.label or "") for kd in out.key_dates[2:]),
        f"{[kd.label for kd in out.key_dates]}",
    )
    check(
        "I4. 其余 key_dates 正常解析保留",
        len(out.key_dates) == 3
        and out.key_dates[2].label == "结果公布"
        and out.key_dates[2].datetime == "2026-08-16T00:00:00",
        f"{[(k.label, k.datetime) for k in out.key_dates]}",
    )

    # 非公示期正文不受影响
    ext2 = NoticeExtraction(
        notice_type="competition",
        title="竞赛报名",
        key_dates=[KeyDate(label="初赛", date_raw="9月23日", datetime=None)],
    )
    out2, _ = inst._resolve_and_validate(ext2, ref, "报名截止时间为9月1日17：00，初赛于9月23日举行。")
    check(
        "I5. 非公示期正文不注入",
        [kd.label for kd in out2.key_dates] == ["初赛"]
        and out2.key_dates[0].datetime == "2026-09-23T00:00:00",
        f"{[(k.label, k.datetime) for k in out2.key_dates]}",
    )


def run():
    print("== 公示期确定性提取（date_utils.extract_gongshi_period） ==")
    test_unit()
    print("\n== 提取器集成（NoticeExtractor._resolve_and_validate） ==")
    test_integration()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()