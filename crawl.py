"""M1 入口：抓取学校通知，存入 SQLite。

用法：
    python crawl.py                          # 用默认配置抓取
    python crawl.py --list-url <URL>         # 抓取指定列表页
    python crawl.py --source <来源名>        # 只抓某个来源
"""
import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

from config.schema import SourceConfig
from config.store import ConfigStore
from crawler import ListPageConfig, WebCrawler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def get_school_config():
    """从 ConfigStore 获取当前活跃学校的数据源配置。"""
    return ConfigStore.get_instance().get_school()


def crawl_source(source_cfg: SourceConfig) -> None:
    """抓取单个来源。"""
    config = ListPageConfig(
        list_url=source_cfg.list_url,
        source_name=source_cfg.name or source_cfg.list_url,
        url_pattern=source_cfg.url_pattern,
        max_pages=source_cfg.max_pages,
    )

    logger.info(f"开始抓取: {config.source_name}")
    logger.info(f"  列表页: {config.list_url}")

    crawler = WebCrawler(config)
    result = crawler.crawl()

    logger.info(f"抓取完成: {config.source_name}")
    logger.info(f"  发现通知: {result.total_discovered}")
    logger.info(f"  新增: {result.total_new}")
    logger.info(f"  跳过(已存在): {result.total_skipped}")
    logger.info(f"  失败: {result.total_failed}")
    if result.errors:
        logger.warning(f"  错误: {len(result.errors)} 条")
        for err in result.errors[:5]:
            logger.warning(f"    - {err}")


def main():
    parser = argparse.ArgumentParser(description="校园通知抓取")
    parser.add_argument(
        "--list-url",
        type=str,
        help="直接指定列表页 URL（跳过配置文件）",
    )
    parser.add_argument(
        "--source",
        type=str,
        help="只抓取指定名称的来源",
    )
    args = parser.parse_args()

    if args.list_url:
        # 直接抓取指定 URL
        crawl_source(
            SourceConfig(
                name=args.list_url,
                list_url=args.list_url,
                max_pages=20,
            )
        )
        return

    # 从 ConfigStore 加载当前学校配置
    school_config = get_school_config()

    for source_cfg in school_config.sources:
        if args.source and source_cfg.name != args.source:
            continue
        crawl_source(source_cfg)


if __name__ == "__main__":
    main()