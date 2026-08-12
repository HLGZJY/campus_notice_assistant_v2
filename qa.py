"""M4 入口：RAG 问答。

用法：
    python qa.py "最近有哪些比赛？"          # 单次问答
    python qa.py                           # 交互式问答（输入 exit/quit 退出）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.qa import ask_question


def _print_result(result) -> None:
    print("\n" + "=" * 60)
    print(result.answer)
    print("=" * 60)
    if result.sources:
        print("\n来源通知：")
        for i, src in enumerate(result.sources, 1):
            print(f"  [{i}] {src.title}")
            if src.deadline:
                print(f"      截止时间: {src.deadline}")
            if src.url:
                print(f"      链接: {src.url}")
    print(f"\n（检索到 {result.retrieved_chunks} 个相关片段，引用 {len(result.sources)} 条通知）")


def main():
    parser = argparse.ArgumentParser(description="校园通知 RAG 问答")
    parser.add_argument("question", nargs="?", help="问题（不传则进入交互模式）")
    args = parser.parse_args()

    if args.question:
        result = ask_question(args.question)
        _print_result(result)
        return

    print("校园通知智能问答（输入 exit / quit / q 退出）")
    while True:
        try:
            question = input("\n问题: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if question.lower() in {"exit", "quit", "q", ""}:
            print("再见。")
            break
        result = ask_question(question)
        _print_result(result)


if __name__ == "__main__":
    main()
