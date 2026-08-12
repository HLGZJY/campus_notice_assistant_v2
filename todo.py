"""M3 入口：待办生成 + 列表 + 状态管理。

用法：
    python todo.py --notice 2                # 为指定通知生成待办（按需）
    python todo.py --batch                   # 为所有行动型通知生成（可选）
    python todo.py --list                    # 列出全部待办（按截止升序）
    python todo.py --list --status pending   # 只列 pending
    python todo.py --done 3                  # 标记完成
    python todo.py --skip 3                  # 标记跳过
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

from core.todo import batch_generate, generate_todos_for_notice
from storage.db import get_connection, get_todos, set_todo_status

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _print_list(rows: list[dict]) -> None:
    if not rows:
        print("（没有待办）")
        return
    today = datetime.now().date().isoformat()
    print(f"{'ID':<4}{'状态':<12}{'优先级':<8}{'截止时间':<22}{'待办内容'}")
    print("-" * 90)
    for t in rows:
        expired = t["due_at"] and t["due_at"][:10] < today and t["status"] == "pending"
        tag = f"{t['status']}{'[过期]' if expired else ''}"
        print(
            f"{t['id']:<4}{tag:<12}{t['priority']:<8}"
            f"{(t['due_at'] or '-')[:19]:<22}{t['action'][:50]}"
        )
        if t.get("notice_title"):
            print(f"    └─ 来自: {t['notice_title']}")


def main():
    parser = argparse.ArgumentParser(description="校园通知待办管理")
    parser.add_argument("--notice", type=int, help="按需生成某通知的待办")
    parser.add_argument("--batch", action="store_true", help="批量为所有行动型通知生成")
    parser.add_argument("--dry-run", action="store_true", help="只计算不写库")
    parser.add_argument("--list", action="store_true", help="列出待办")
    parser.add_argument("--status", type=str, default=None, help="按状态过滤(pending/done/skipped)")
    parser.add_argument("--done", type=int, help="标记待办完成")
    parser.add_argument("--skip", type=int, help="标记待办跳过")
    parser.add_argument("--pending", type=int, help="重置待办为 pending")
    args = parser.parse_args()

    conn = get_connection()

    try:
        if args.notice is not None:
            outcome = generate_todos_for_notice(args.notice, dry_run=args.dry_run)
            print(f"状态: {outcome.status}")
            if outcome.error:
                print(f"提示: {outcome.error}")
            for it in outcome.items:
                print(f"  [{it.priority}] {it.action}   (due: {it.due_at or '-'})")

        elif args.batch:
            summary = batch_generate(dry_run=args.dry_run)
            print(f"生成: {summary['generated']}  无: {summary['none']}  失败: {summary['failed']}")
            for d in summary["details"]:
                mark = "✓" if d["status"] == "generated" else ("-" if d["status"] == "none" else "✗")
                print(f"  {mark} #{d['id']:<4} {d['status']:<10} {d['title']}")

        elif args.done is not None or args.skip is not None or args.pending is not None:
            tid, new_status = None, None
            if args.done is not None:
                tid, new_status = args.done, "done"
            elif args.skip is not None:
                tid, new_status = args.skip, "skipped"
            else:
                tid, new_status = args.pending, "pending"
            ok = set_todo_status(conn, tid, new_status)
            print(f"待办 #{tid} → {new_status}: {'成功' if ok else '未找到'}")

        else:  # 默认列出
            rows = get_todos(conn, status=args.status)
            _print_list(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
