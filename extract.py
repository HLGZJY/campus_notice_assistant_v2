"""M2 入口：批量结构化提取 raw 通知，更新 SQLite。

用法：
    python extract.py                          # 提取所有 status=raw 的通知（含前置过滤）
    python extract.py --limit 10               # 最多提取 10 条
    python extract.py --status failed          # 重试提取失败的
    python extract.py --source 创新创业学院-竞赛通知
    python extract.py --no-prefilter           # 关闭提取前置过滤（全部调 LLM）
    python extract.py --dry-run                # 只跑不写库
"""
import argparse
import asyncio
import logging
import sqlite3
import sys
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

from config.store import ConfigStore
from core.extractor import NoticeExtractor
from core.models import NoticeExtraction
from services.notice_service import prefilter_notice
from storage.db import (
    count_notices_by_status,
    get_connection,
    get_notices_by_status,
    mark_failed,
    mark_prefiltered,
    update_extraction,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def summarize(result: dict) -> None:
    """打印处理汇总。"""
    print("\n===== 提取完成汇总 =====")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print("========================\n")


async def run_batch(
    conn: sqlite3.Connection,
    notices: list[dict],
    dry_run: bool = False,
    limit: int = 50,
    extractor: NoticeExtractor | None = None,
) -> dict:
    """批量提取。extractor 可注入（测试用），默认真实 NoticeExtractor。"""
    extractor = extractor or NoticeExtractor()
    counter = {"成功(extracted)": 0, "部分(partial)": 0, "失败(failed)": 0}
    samples: list[dict] = []

    for i, n in enumerate(notices[:limit], start=1):
        logger.info("[%d/%d] 提取: %s", i, len(notices[:limit]), n["title"])
        outcome = await extractor.extract_one(
            title=n["title"],
            content=n["raw_content"] or "",
            published_at=n["published_at"],
            crawled_at=n["crawled_at"],
            notice_id=n["id"],
        )
        key = {"extracted": "成功(extracted)", "partial": "部分(partial)", "failed": "失败(failed)"}[
            outcome.status
        ]
        counter[key] += 1
        samples.append(
            {
                "id": n["id"],
                "title": n["title"],
                "status": outcome.status,
                "notice_type": outcome.extraction.notice_type if outcome.extraction else None,
                "deadline": outcome.extraction.deadline if outcome.extraction else None,
                "error": outcome.error,
            }
        )

        if not dry_run:
            if outcome.status == "failed":
                mark_failed(conn, n["id"], outcome.error or "")
            elif outcome.extraction is not None:
                update_extraction(
                    conn,
                    n["id"],
                    outcome.extraction.model_dump(),
                    outcome.status,
                )

        if outcome.error:
            logger.warning("  注意: %s", outcome.error)

    return {"明细": samples, **counter}


def main():
    parser = argparse.ArgumentParser(description="校园通知结构化提取")
    parser.add_argument("--limit", type=int, default=50, help="最多提取条数")
    parser.add_argument("--status", type=str, default="raw", help="处理的初始状态(raw/failed)")
    parser.add_argument("--source", type=str, default=None, help="只处理指定来源")
    parser.add_argument("--no-prefilter", action="store_true", help="关闭提取前置过滤（全部调 LLM）")
    parser.add_argument("--dry-run", action="store_true", help="只跑不写库")
    args = parser.parse_args()

    conn = get_connection()

    try:
        before = count_notices_by_status(conn)
        logger.info("当前各状态数量: %s", before)

        prefilter = not args.no_prefilter
        if prefilter:
            from config.schema import ExtractConfig

            cfg: ExtractConfig = ConfigStore.get_instance().get_extract()

        candidates = get_notices_by_status(
            conn,
            args.status,
            limit=args.limit * 3,
            source=args.source,
            exclude_prefiltered=prefilter,
        )

        notices = []
        skipped = 0
        if prefilter:
            for n in candidates:
                ok, reason = prefilter_notice(n, cfg)
                if ok:
                    notices.append(n)
                else:
                    skipped += 1
                    if not args.dry_run:
                        mark_prefiltered(conn, n["id"], reason)
                if len(notices) >= args.limit:
                    break
        else:
            notices = candidates[: args.limit]

        logger.info("待提取通知: %d 条 (status=%s%s)", len(notices), args.status,
                    f"，预筛跳过 {skipped} 条" if skipped else "")

        if not notices:
            print("没有待提取的通知。")
            return

        result = asyncio.run(run_batch(conn, notices, dry_run=args.dry_run, limit=args.limit))

        print("\n===== 单条明细 =====")
        for s in result["明细"]:
            print(
                f"  #{s['id']:<4} [{s['status']:<9}] {s['notice_type'] or '-':<13} "
                f"deadline={s['deadline'] or '-':<22} {s['title']}"
            )
            if s["error"]:
                print(f"         error: {s['error'][:160]}")

        if not args.dry_run:
            after = count_notices_by_status(conn)
            print("\n===== 提取后各状态数量 =====")
            for k, v in after.items():
                print(f"  {k}: {v}")
        else:
            print("\n(dry-run 模式，未写入数据库)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
