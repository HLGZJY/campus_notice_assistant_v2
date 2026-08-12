"""M4 入口：构建/更新 Chroma 向量索引。

用法：
    python index.py                          # 全量重建索引（默认）
    python index.py --no-rebuild             # 不删除旧索引（仅用于特殊场景）
    python index.py --notice 2               # 单条通知重新索引
    python index.py --dry-run                # 只统计 chunk 数
    python index.py --status extracted       # 只索引 extracted（默认 extracted,partial）
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from storage.db import get_connection, get_notice_by_id
from storage.vectorstore import VectorIndex

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def parse_status(status_str: str) -> list[str]:
    return [s.strip() for s in status_str.split(",") if s.strip()]


def fetch_notices(conn, statuses: list[str]) -> list[dict]:
    placeholders = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"""SELECT * FROM notices
            WHERE status IN ({placeholders})
              AND raw_content IS NOT NULL
              AND raw_content != ''
            ORDER BY id""",
        statuses,
    ).fetchall()
    return [dict(r) for r in rows]


def main():
    parser = argparse.ArgumentParser(description="校园通知向量索引")
    parser.add_argument(
        "--notice",
        type=int,
        default=None,
        help="只索引/重新索引指定通知 ID",
    )
    parser.add_argument(
        "--status",
        type=str,
        default="extracted,partial",
        help="索引哪些状态的通知，逗号分隔（默认 extracted,partial）",
    )
    parser.add_argument(
        "--rebuild",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否全量重建索引（默认 True）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计 chunk 数，不写向量库",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="输出详细日志",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    conn = get_connection()
    index = VectorIndex()

    try:
        if args.notice is not None:
            notice = get_notice_by_id(conn, args.notice)
            if notice is None:
                print(f"通知 ID={args.notice} 不存在")
                return
            if not notice.get("raw_content"):
                print(f"通知 ID={args.notice} 没有正文，无法索引")
                return

            if args.dry_run:
                from storage.vectorstore import _split_notice
                chunks = _split_notice(notice)
                print(f"[dry-run] 通知 #{args.notice} 将生成 {len(chunks)} 个 chunk")
                return

            result = index.add_notice(notice)
            print(
                f"已更新通知 #{args.notice}：{notice.get('title', '')[:40]}... "
                f"({result['chunks']} chunks)"
            )
            print(f"当前向量库文档数: {index.count()}")
            return

        statuses = parse_status(args.status)
        notices = fetch_notices(conn, statuses)
        print(f"待索引通知: {len(notices)} 条 (status={statuses})")

        if not notices:
            print("没有可索引的通知。先运行 python extract.py 进行结构化提取。")
            return

        if args.dry_run:
            result = index.rebuild(notices, dry_run=True)
            print(
                f"[dry-run] 将索引 {result['notices']} 条通知，"
                f"生成 {result['chunks']} 个 chunk"
            )
            return

        if args.rebuild:
            result = index.rebuild(notices)
            print(
                f"索引重建完成: {result['notices']} 条通知, "
                f"{result['chunks']} 个 chunk"
            )
        else:
            # 增量模式：逐条 add_notice（会先去重）
            total_chunks = 0
            for notice in notices:
                r = index.add_notice(notice)
                total_chunks += r["chunks"]
            print(f"增量索引完成: {len(notices)} 条通知, {total_chunks} 个 chunk")

        print(f"当前向量库文档数: {index.count()}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
