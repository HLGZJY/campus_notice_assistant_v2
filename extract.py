"""M2 入口：批量结构化提取 raw 通知，更新 SQLite。

用法：
    python extract.py                          # 提取所有 status=raw 的通知（含前置过滤）
    python extract.py --limit 10               # 最多提取 10 条
    python extract.py --status failed          # 重试提取失败的
    python extract.py --source 创新创业学院-竞赛通知
    python extract.py --no-prefilter           # 关闭提取前置过滤（全部调 LLM）
    python extract.py --concurrency 5          # 并发提取（默认取 config.extract.concurrency=3）
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

# Windows 控制台默认 cp1252 无法打印中文，统一用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config.store import ConfigStore
from core.extractor import NoticeExtractor
from core.models import NoticeExtraction
from services.notice_service import prefilter_notice
from storage.db import (
    close_task_connection,
    count_notices_by_status,
    get_connection,
    get_notices_by_status,
    get_task_connection,
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
    concurrency: int = 3,
) -> dict:
    """批量提取（Semaphore 限流并发，默认 3）。extractor 可注入（测试用）。

    conn 仅用于签名兼容与调用方统计；并发写库走任务级专属连接
    （get_task_connection），测试场景 DB_PATH 全局替换后落在同一库。
    """
    extractor = extractor or NoticeExtractor()
    counter = {"成功(extracted)": 0, "部分(partial)": 0, "失败(failed)": 0}
    samples: list[dict] = []
    sem = asyncio.Semaphore(max(1, min(8, concurrency)))

    async def _one(n: dict) -> dict:
        try:
            outcome = await extractor.extract_one(
                title=n["title"],
                content=n["raw_content"] or "",
                published_at=n["published_at"],
                crawled_at=n["crawled_at"],
                notice_id=n["id"],
            )
            if not dry_run:
                conn2 = get_task_connection()
                if outcome.status == "failed":
                    mark_failed(conn2, n["id"], outcome.error or "")
                elif outcome.extraction is not None:
                    update_extraction(
                        conn2,
                        n["id"],
                        outcome.extraction.model_dump(),
                        outcome.status,
                    )
            return {
                "id": n["id"],
                "title": n["title"],
                "status": outcome.status,
                "notice_type": outcome.extraction.notice_type if outcome.extraction else None,
                "deadline": outcome.extraction.deadline if outcome.extraction else None,
                "error": outcome.error,
            }
        finally:
            close_task_connection()

    async def _guarded(n: dict) -> dict:
        async with sem:
            return await _one(n)

    batch = notices[:limit]
    tasks = [asyncio.create_task(_guarded(n)) for n in batch]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        # 中断语义（kill/进程崩溃模拟）：整体中断，取消其余未完成任务
        for t in tasks:
            if not t.done():
                t.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            pass
        raise

    for s in results:
        key = {"extracted": "成功(extracted)", "partial": "部分(partial)", "failed": "失败(failed)"}[
            s["status"]
        ]
        counter[key] += 1
        samples.append(s)
        if s["error"]:
            logger.warning("  注意: %s", s["error"])

    return {"明细": samples, **counter}


def main():
    parser = argparse.ArgumentParser(description="校园通知结构化提取")
    parser.add_argument("--limit", type=int, default=50, help="最多提取条数")
    parser.add_argument("--status", type=str, default="raw", help="处理的初始状态(raw/failed)")
    parser.add_argument("--source", type=str, default=None, help="只处理指定来源")
    parser.add_argument("--no-prefilter", action="store_true", help="关闭提取前置过滤（全部调 LLM）")
    parser.add_argument("--concurrency", type=int, default=None, help="并发提取数（默认取 config.extract.concurrency）")
    parser.add_argument("--dry-run", action="store_true", help="只跑不写库")
    args = parser.parse_args()

    conn = get_connection()

    try:
        before = count_notices_by_status(conn)
        logger.info("当前各状态数量: %s", before)

        prefilter = not args.no_prefilter
        cfg = None
        if prefilter:
            from config.schema import ExtractConfig

            cfg = ConfigStore.get_instance().get_extract()

        concurrency = args.concurrency or (cfg.concurrency if cfg else 3)
        logger.info("并发数: %d", concurrency)

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

        result = asyncio.run(
            run_batch(
                conn,
                notices,
                dry_run=args.dry_run,
                limit=args.limit,
                concurrency=concurrency,
            )
        )

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
