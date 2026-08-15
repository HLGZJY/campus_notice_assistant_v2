"""Phase B：dspy.MIPROv2 自动搜待办生成 prompt 的 few-shot（M3 待办生成优化）。

用法（需要先安装开发依赖并配置 API key）：
    pip install -r requirements-dev.txt          # 引入 dspy-ai
    python tools/optimize_todo_prompt.py --dry-run   # 不装 dspy 也能跑的配置自检
    python tools/optimize_todo_prompt.py              # 实际跑 MIPROv2 优化（消耗 token）

设计：
  - 模型复用 utils.llm.get_model_for_task("todo")（与生产 TodoGenerator 同端点/模型）
  - trainset 直接用 data/eval/todo/golden_todo.json（无需手工改写标签：
    MIPROv2 用当前程序在 metric 校验下自行 bootstrap 示例）
  - metric 复用 evaluate_todo.score_entry（decision/action + 日期语义等价 + 反例断言）
  - 产物落 data/eval/todo/optimization/：compiled program + 可人工审阅的优化报告
    （优化后的 instructions + few-shot demos，审阅后再合入 core/todo.py 的 TODO_INSTRUCTIONS）

注意：dspy 为开发依赖（不入 requirements-backend.txt 生产镜像）。运行前确认
config/app.yaml 中 todo 任务的 provider 已配置 API key，并预留 token 预算。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GOLDEN_PATH = ROOT / "data" / "eval" / "todo" / "golden_todo.json"
OUTPUT_DIR = ROOT / "data" / "eval" / "todo" / "optimization"


def load_golden(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "entries" not in data or not isinstance(data["entries"], list):
        raise ValueError(f"{path}: 缺少 entries 列表")
    for e in data["entries"]:
        if "id" not in e or "notice" not in e or "expected" not in e:
            raise ValueError(f"{path}: 条目缺少 id/notice/expected")
    return data


def build_metric():
    from evaluate_todo import score_entry

    def metric(pred, gold) -> float:
        items = []
        decision = getattr(pred, "decision", "none")
        if decision == "action":
            items.append(
                type(
                    "T",
                    (),
                    {
                        "action": getattr(pred, "action", ""),
                        "due_at": getattr(pred, "due_at", None) or None,
                        "priority": getattr(pred, "priority", "normal"),
                    },
                )()
            )
        fields, _info = score_entry(items, gold.expected)
        return float(all(fields.values()))

    return metric


def dry_run(golden: dict) -> None:
    print("MIPROv2 配置自检（未加载 dspy）")
    print(f"  golden 集: {GOLDEN_PATH}  条目数={len(golden['entries'])}  prompt_version={golden.get('prompt_version')}")
    from utils.llm import get_model_for_task

    api_key, base_url, model = get_model_for_task("todo")
    mask = f"{api_key[:6]}...{api_key[-4:]}" if api_key else "<空>"
    print(f"  todo 模型端点: {base_url}  model={model}  api_key={mask}")
    print("  metric 引用: evaluate_todo.score_entry（decision/action，日期等价，not_contains）")
    print("  运行命令: pip install -r requirements-dev.txt && python tools/optimize_todo_prompt.py")
    print("  依赖: dspy-ai 未安装时本脚本可正常做 --dry-run，实际优化需先安装。")


def run_optimization(golden: dict, args) -> None:
    try:
        import dspy
    except ImportError as e:
        sys.exit(f"未安装 dspy，请先执行: pip install -r requirements-dev.txt ({e})")

    from utils.llm import get_model_for_task

    api_key, base_url, model = get_model_for_task("todo")
    if not api_key:
        sys.exit("未配置 todo 任务 API key，无法运行优化")

    dspy.configure(
        lm=dspy.OpenAI(
            model=model,
            api_key=api_key,
            api_base=base_url,
        )
    )

    class TodoSignature(dspy.Signature):
        """你是校园通知的待办生成助手。你的唯一职责：把结构化通知翻译成 0~1 条\"行动型待办\"。仅 competition / lecture / registration / scholarship / administrative / recruitment 生成待办；其余类型 decision 填 none。最多生成 1 条最关键行动，优先级：报名 > 提交 > 参加。action 只能用输入中已有的信息，严禁编造 deadline、时间、URL、地点；输入没有 deadline 时 action 不得出现任何具体时间。"""

        notice_json: str = dspy.InputField(desc="结构化通知 JSON（title/notice_type/deadline/signup_method/signup_url/location/summary 等）")
        decision: str = dspy.OutputField(desc="行动型通知填 action，非行动型（news/result/policy/other）填 none")
        action: str = dspy.OutputField(desc="一句自然中文，含具体动作与截止时间；无 deadline 时禁止出现任何时间；仅用输入已有信息")
        due_at: str = dspy.OutputField(desc="原样复制输入中的 deadline；无则填空字符串")
        priority: str = dspy.OutputField(desc="填 high（后端按距今天数重算）")

    program = dspy.Predict(TodoSignature)

    examples = []
    for entry in golden["entries"]:
        ex = dspy.Example(
            notice_json=json.dumps(entry["notice"], ensure_ascii=False, sort_keys=True),
            expected=entry["expected"],
        )
        examples.append(ex.with_inputs("notice_json"))

    optimizer = dspy.MIPROv2(metric=build_metric(), auto=args.auto, num_threads=args.threads)
    compile_kwargs = dict(
        trainset=examples,
        max_bootstrapped_demos=args.max_bootstrapped_demos,
        max_labeled_demos=args.max_labeled_demos,
        num_candidates=args.num_candidates,
        seed=args.seed,
    )
    if args.num_trials:
        compile_kwargs["num_trials"] = args.num_trials
    compiled = optimizer.compile(program, **compile_kwargs)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    compiled.save(OUTPUT_DIR / "compiled.json")

    sig = getattr(compiled, "signature", None)
    instructions = str(getattr(sig, "instructions", "") or "")
    demos = []
    for demo in getattr(compiled, "demos", None) or []:
        if isinstance(demo, tuple):
            demo = demo[0]
        demos.append(
            {
                "notice": demo.get("notice_json"),
                "decision": demo.get("decision"),
                "action": demo.get("action"),
                "due_at": demo.get("due_at"),
                "priority": demo.get("priority"),
            }
        )
    report = {
        "prompt_version": golden.get("prompt_version"),
        "model": model,
        "auto": args.auto,
        "seed": args.seed,
        "instructions": instructions,
        "demos": demos,
    }
    (OUTPUT_DIR / "optimization_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"优化完成，产物落 {OUTPUT_DIR}/（compiled.json + optimization_report.json）")
    print("请人工审阅 optimization_report.json 的 instructions 与 demos 后，再决定是否合入 core/todo.py 的 TODO_INSTRUCTIONS。")


def main() -> None:
    parser = argparse.ArgumentParser(description="dspy.MIPROv2 待办 prompt few-shot 优化")
    parser.add_argument("--trainset", type=Path, default=GOLDEN_PATH, help="黄金集路径")
    parser.add_argument("--dry-run", action="store_true", help="只做配置自检，不加载 dspy")
    parser.add_argument("--auto", choices=["light", "medium", "heavy"], default="light", help="MIPROv2 搜索预算")
    parser.add_argument("--threads", type=int, default=4, help="并行线程数")
    parser.add_argument("--max-bootstrapped-demos", type=int, default=4)
    parser.add_argument("--max-labeled-demos", type=int, default=4)
    parser.add_argument("--num-candidates", type=int, default=8)
    parser.add_argument("--num-trials", type=int, default=0, help="显式指定试验数（0=由 auto 决定）")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    if not args.trainset.is_file():
        sys.exit(f"找不到黄金集: {args.trainset}")
    golden = load_golden(args.trainset)

    if args.dry_run:
        dry_run(golden)
        return
    run_optimization(golden, args)


if __name__ == "__main__":
    main()