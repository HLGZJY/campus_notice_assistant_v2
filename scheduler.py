"""W1 模块 1.1：调度器（APScheduler）。

阶段 6 起由 FastAPI 后端进程常驻运行（单进程，符合 §5.8 app.yaml 写入权唯一）；
CLI 保留用于运维/调试（crash_drill.py 依赖其子进程调用方式）。

API 集成：`start_scheduler(config)` 由 api/main.py lifespan 拉起，stop 由 lifespan 统一处理；
        `scheduler.enabled=false` 或 APP_ENV=test 时 API 不启动调度器。
CLI 用法：
    python scheduler.py                # 前台运行三个定时 job
    python scheduler.py --once         # 只跑一轮完整闭环（抓取→提取→每日体检）后退出
    python scheduler.py --interval 1   # 覆盖抓取间隔（分钟），便于快速验证
    python scheduler.py --no-daily     # 跳过每日清理 job（只保留抓取+提取）
    python scheduler.py --no-extract   # 跳过提取 job（只抓取，避免消耗 LLM 配额）
    python scheduler.py --no-reminder  # 跳过每日截止提醒扫描 job
    python scheduler.py --no-health    # 跳过每日体检（模块 4.2）
    python scheduler.py --log logs/x.log  # 自定义日志文件路径

--no-* 开关在阶段 6 起映射为 config/app.yaml 的 scheduler 段配置项：配置项是默认值，
CLI 开关只能再关不能开（enable_daily = config.enable_daily and not --no-daily）。

四个业务 job + 一个内部 job：
  crawl       : 定时抓取。间隔读取 config/app.yaml -> crawl.interval_minutes，
                运行中修改配置会自动重排下一次触发时间。
  extract     : 抓取完成后触发提取（同一周期，晚于抓取 EXTRACT_DELAY 秒）。
  daily       : 每日 03:00 过期清理 + 向量一致性检查 + 每日体检（模块 4.2）。
                过期清理默认只报告不删除（cleanup_enabled=false，分类/时间线归档见 issue #3）；
                向量一致性检查会自动清理幽灵向量（SQLite 已删但 Chroma 残留）；
                每日体检只读计算抓取成功率/提取失败率/token 消耗/异常日志并落盘
                data/health/daily/（7 天自运行终检的证据，--no-health 可关）。
  reminder    : 每日 03:00 截止提醒扫描（模块 3.2）：对截止前 3 天 / 1 天的通知
                 生成提醒，幂等（同一天同一对象不重复）；后端进程生成，UI 只读。
  config-watch: 每 60s 检查一次配置，让 crawl.interval_minutes 的修改在 1 分钟内生效。

失败语义：job 抛出的异常不吞掉——写日志 + 写入 scheduler_log 表（含连续失败计数），
          下一周期自动重跑（interval 触发天然保证）。
崩溃恢复：每次 job 运行都持久化到 scheduler_log / crawl_log，重启后从库中恢复运行状态；
          已抓 URL 不重复抓取由 notices.url UNIQUE + WebCrawler 的 skip 逻辑保证。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

# 确保包能正确导入（与 crawl.py / extract.py 保持一致）
sys.path.insert(0, str(Path(__file__).parent))

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.schema import SchedulerConfig
from config.store import ConfigStore
from services.notice_service import crawl_all_sources, extract_batch
from services.reminder_service import scan_reminders
from storage.db import (
    delete_notice,
    get_connection,
    get_recent_scheduler_log,
    log_scheduler_run,
)
from utils.app_paths import get_app_root

logger = logging.getLogger(__name__)

DEFAULT_LOG_FILE = get_app_root() / "data" / "logs" / "scheduler.log"

# 提取 job 晚于抓取 job 的秒数（同一周期内"抓取完成后触发提取"）
EXTRACT_DELAY_SECONDS = 20
# 每轮最多提取条数（防止单轮 LLM 调用过久；剩余的下周期继续）
EXTRACT_BATCH_LIMIT = 50
# 配置监听间隔（秒）：让 crawl.interval_minutes 的修改在 1 分钟内生效
CONFIG_WATCH_SECONDS = 60
# 每日体检的固定时间
DAILY_CRON = {"hour": 3, "minute": 0}


def _resolve_log_path(log_file: Optional[str]) -> Path:
    """日志路径解析：相对路径按应用根目录（而非 cwd）定位。

    打包后用户可能从任意工作目录启动（快捷方式 cwd 不定），相对路径
    data/logs/scheduler.log 必须锚定到 exe 同级目录。
    """
    path = Path(log_file) if log_file else DEFAULT_LOG_FILE
    if not path.is_absolute():
        path = get_app_root() / path
    return path


def setup_logging(log_file: Optional[str]) -> Path:
    """控制台 + 滚动日志文件双输出。"""
    log_path = _resolve_log_path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(str(log_path), maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    return log_path


def setup_api_logging(log_file: Optional[str]) -> Path:
    """API 集成模式的日志：只给 scheduler/apscheduler 挂文件句柄，不接管 root。

    阶段 6：调度器并入后端进程后，不能再像 CLI 那样 basicConfig 接管 root，
    否则 uvicorn / FastAPI 日志会混入 scheduler.log 且根 logger 行为被改变。
    """
    log_path = _resolve_log_path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        str(log_path), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.set_name("scheduler-api-file")
    console_handler = logging.StreamHandler()
    console_handler.set_name("scheduler-api-console")
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s %(message)s")
    for h in (file_handler, console_handler):
        h.setFormatter(fmt)
    for name in ("scheduler", "apscheduler"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        if not any(h.get_name() == "scheduler-api-file" for h in lg.handlers):
            lg.addHandler(file_handler)
            lg.addHandler(console_handler)
        lg.propagate = False  # 避免冒泡到 root 重复输出
    return log_path


class NoticeScheduler:
    """APScheduler 封装：四个 job + 失败计数 + 间隔热更新。"""

    def __init__(
        self,
        interval_override: Optional[int] = None,
        enable_daily: bool = True,
        enable_extract: bool = True,
        enable_reminder: bool = True,
        enable_health: bool = True,
    ):
        self._store = ConfigStore.get_instance()
        self._interval_override = interval_override
        self._enable_daily = enable_daily
        self._enable_extract = enable_extract
        self._enable_reminder = enable_reminder
        self._enable_health = enable_health
        self._current_interval: Optional[int] = None
        # job_name -> 连续失败次数（进程内维护；每次运行随 scheduler_log 落库）
        self._consecutive_failures: dict[str, int] = {}
        # 抓取轮次计数（阶段 7：deep_check_interval_cycles 定期深度变更检测）
        self._crawl_cycles = 0
        self._scheduler = BackgroundScheduler()

    # ---------- 对外生命周期 ----------

    def start(self) -> None:
        """调度全部 job 并启动。首个周期立即执行（抓取立即、提取晚 20s）。"""
        interval = self._interval_override or self._store.get_crawl().interval_minutes
        self._current_interval = interval
        now = datetime.now()

        self._scheduler.add_job(
            lambda: self._record_run("crawl", self._crawl_job),
            IntervalTrigger(minutes=interval, start_date=now),
            id="crawl",
            name="定时抓取",
            next_run_time=now,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=interval * 60,
        )
        if self._enable_extract:
            self._scheduler.add_job(
                lambda: self._record_run("extract", self._extract_job),
                IntervalTrigger(minutes=interval, start_date=now),
                id="extract",
                name="抓取后提取",
                next_run_time=now + timedelta(seconds=EXTRACT_DELAY_SECONDS),
                max_instances=1,
                coalesce=True,
                misfire_grace_time=interval * 60,
            )
        if self._enable_daily:
            self._scheduler.add_job(
                lambda: self._record_run("daily", self._daily_job),
                CronTrigger(**DAILY_CRON),
                id="daily",
                name="每日过期清理+向量一致性检查",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
        if self._enable_reminder:
            self._scheduler.add_job(
                lambda: self._record_run("reminder", self._reminder_job),
                CronTrigger(**DAILY_CRON),
                id="reminder",
                name="每日截止提醒扫描",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
        # 配置监听：让 crawl.interval_minutes 的修改在 1 分钟内生效
        self._scheduler.add_job(
            self._config_watch_job,
            IntervalTrigger(seconds=CONFIG_WATCH_SECONDS, start_date=now),
            id="config-watch",
            name="配置监听（间隔热更新）",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

        self._scheduler.start()
        extract_note = "提取紧随抓取" if self._enable_extract else "提取已禁用(--no-extract)"
        reminder_note = "提醒扫描已启用" if self._enable_reminder else "提醒扫描已禁用(--no-reminder)"
        logger.info(
            "抓取间隔: %d 分钟 | %s | 每日 %02d:%02d 体检+%s | 配置每 %d 秒监听",
            interval,
            extract_note,
            DAILY_CRON["hour"],
            DAILY_CRON["minute"],
            reminder_note,
            CONFIG_WATCH_SECONDS,
        )

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("调度器已停止")

    def run_once(self) -> None:
        """--once：按顺序跑一轮完整闭环，结果全部落库后退出。"""
        logger.info("== 单轮全闭环（--once）==")
        jobs = [("crawl", self._crawl_job)]
        if self._enable_extract:
            jobs.append(("extract", self._extract_job))
        if self._enable_daily:
            jobs.append(("daily", self._daily_job))
        if self._enable_reminder:
            jobs.append(("reminder", self._reminder_job))
        for job_name, fn in jobs:
            self._record_run(job_name, fn)

    def print_recovery_info(self) -> None:
        """重启时打印最近运行记录，证明运行状态可从库中恢复。"""
        conn = get_connection()
        try:
            rows = get_recent_scheduler_log(conn, limit=10)
        finally:
            conn.close()
        if not rows:
            logger.info("首次启动调度器（scheduler_log 为空）")
            return
        logger.info("重启恢复：最近 %d 次调度运行记录（scheduler_log）：", len(rows))
        for r in rows:
            logger.info(
                "  #%d job=%-8s status=%-7s %s 连续失败=%d %s",
                r["id"],
                r["job_name"],
                r["status"],
                r["finished_at"] or r["started_at"],
                r["failure_count"],
                (r["message"] or "")[:80],
            )
        logger.info(
            "已抓 URL 不会重复抓取（notices.url UNIQUE 去重），崩溃重启安全"
        )

    def get_status(self) -> dict:
        """只读状态（GET /api/v1/scheduler/status 用）：运行标记 + 已注册 job + 当前间隔。"""
        jobs = []
        if self._scheduler.running:
            for job in self._scheduler.get_jobs():
                jobs.append(
                    {
                        "id": job.id,
                        "name": job.name,
                        "next_run_time": job.next_run_time.isoformat()
                        if job.next_run_time
                        else None,
                    }
                )
        return {
            "running": self._scheduler.running,
            "interval_minutes": self._current_interval,
            "jobs": jobs,
        }

    # ---------- 内部：job 包装 ----------

    def _record_run(self, job_name: str, fn: Callable[[], dict]) -> dict:
        """执行 job：统一捕获异常、写日志、落 scheduler_log（含失败计数）。"""
        started = datetime.now()
        status = "success"
        message = ""
        details: dict = {}
        try:
            details = fn() or {}
            self._consecutive_failures[job_name] = 0
        except Exception as e:  # noqa: BLE001 —— 不能吞异常：记录后下周期重跑
            status = "failed"
            fails = self._consecutive_failures.get(job_name, 0) + 1
            self._consecutive_failures[job_name] = fails
            message = f"{type(e).__name__}: {e}"
            details = {"error": message}
            logger.exception("job '%s' 失败（连续第 %d 次，下周期自动重跑）", job_name, fails)

        finished = datetime.now()
        duration_ms = int((finished - started).total_seconds() * 1000)
        conn = get_connection()
        try:
            log_scheduler_run(
                conn,
                job_name,
                status,
                started.isoformat(),
                finished.isoformat(),
                duration_ms,
                self._consecutive_failures.get(job_name, 0),
                message,
                details,
            )
        except Exception as e:  # 落库失败也要让日志可查
            logger.exception("写入 scheduler_log 失败: %s", e)
        finally:
            conn.close()

        logger.info("job '%s' 结束: status=%s 耗时=%dms", job_name, status, duration_ms)
        return details

    # ---------- 内部：三个 job ----------

    def _crawl_job(self) -> dict:
        """job 1：定时抓取所有数据源（间隔可热更新）。

        阶段 7：每 deep_check_interval_cycles 轮自动做一次全来源深度变更检测
        （重抓已入库详情页比对内容指纹），默认 24 轮（约每日一次）。
        """
        self._reschedule_interval()
        self._crawl_cycles += 1
        deep_check = False
        try:
            interval_cycles = self._store.get_crawl().deep_check_interval_cycles
            deep_check = interval_cycles > 0 and self._crawl_cycles % interval_cycles == 0
        except Exception as e:
            logger.warning("读取 crawl.deep_check_interval_cycles 失败: %s", e)
        if deep_check:
            logger.info("本轮为深度变更检测轮（第 %d 轮，每 %d 轮一次）", self._crawl_cycles, interval_cycles)

        results = crawl_all_sources(deep_check=deep_check)
        summary = {
            "sources": len(results),
            "discovered": sum(r.get("discovered", 0) for r in results.values()),
            "new": sum(r.get("new", 0) for r in results.values()),
            "skipped": sum(r.get("skipped", 0) for r in results.values()),
            "changed": sum(r.get("changed", 0) for r in results.values()),
            "failed": sum(r.get("failed", 0) for r in results.values()),
        }
        logger.info(
            "抓取汇总: 来源=%d 发现=%d 新增=%d 跳过=%d 变更=%d 失败=%d%s",
            summary["sources"],
            summary["discovered"],
            summary["new"],
            summary["skipped"],
            summary["changed"],
            summary["failed"],
            "（深度检测轮）" if deep_check else "",
        )
        for name, r in results.items():
            if r.get("errors"):
                logger.warning("来源 %s 错误 %d 条: %s", name, len(r["errors"]), r["errors"][:2])
        return {"summary": summary, "per_source": results, "deep_check": deep_check}

    def _extract_job(self) -> dict:
        """job 2：抓取完成后触发提取（处理 status=raw，预筛后增量索引）。"""
        result = extract_batch(limit=EXTRACT_BATCH_LIMIT, auto_index=True)
        processed = result.get("processed", 0)
        prefiltered = result.get("prefiltered", 0)
        summary = result.get("summary", {})
        if processed:
            logger.info(
                "提取完成: processed=%d prefiltered=%d extracted=%d partial=%d failed=%d",
                processed,
                prefiltered,
                summary.get("extracted", 0),
                summary.get("partial", 0),
                summary.get("failed", 0),
            )
            for d in summary.get("details", []):
                if d["status"] == "failed":
                    logger.warning("提取失败 notice_id=%s: %s", d["id"], d.get("error"))
        else:
            logger.info(
                "没有待提取的通知（%s）",
                f"预筛跳过 {prefiltered} 条" if prefiltered else "status=raw 为空或全部已预筛",
            )
        return result

    def _daily_job(self) -> dict:
        """job 3：每日过期清理 + 向量一致性检查 + 每日体检（模块 4.2）。"""
        cleanup = self._cleanup_expired()
        consistency = self._check_vector_consistency()
        result: dict = {"cleanup": cleanup, "consistency": consistency}
        if self._enable_health:
            result["health"] = self._run_health_check()
        return result

    def _run_health_check(self) -> dict:
        """每日体检（模块 4.2）：只读计算健康指标并落盘；失败不拖垮 daily job。"""
        from services.health_service import run_daily_health_check

        try:
            report = run_daily_health_check()
            return {"report_date": report["report_date"], "overall_pass": report["overall_pass"]}
        except Exception as e:  # noqa: BLE001 —— 体检非关键路径，失败仅记录
            logger.warning("每日体检失败: %s", e)
            return {"error": f"{type(e).__name__}: {e}"}

    def _reminder_job(self) -> dict:
        """job 4：每日截止提醒扫描（模块 3.2），幂等生成截止前 3 天 / 1 天的提醒。"""
        return scan_reminders()

    def _config_watch_job(self) -> dict:
        """内部 job：周期性检查配置，间隔变更时热更新抓取/提取 job。"""
        self._reschedule_interval()
        return {"interval_minutes": self._current_interval}

    # ---------- 内部：辅助 ----------

    def _reschedule_interval(self) -> None:
        """每轮抓取前读取配置，间隔变更时热更新两个 interval job。"""
        if self._interval_override is not None:
            return
        try:
            interval = self._store.get_crawl().interval_minutes
        except Exception as e:
            logger.warning("读取 crawl.interval_minutes 失败: %s", e)
            return
        if interval == self._current_interval:
            return
        now = datetime.now()
        # --once 模式直接调 job 函数、未注册 APScheduler job，需要跳过重排
        crawl_job = self._scheduler.get_job("crawl")
        if crawl_job is not None:
            crawl_job.reschedule(
                trigger=IntervalTrigger(minutes=interval, start_date=now)
            )
        extract_job = self._scheduler.get_job("extract")
        if extract_job is not None:
            extract_job.reschedule(
                trigger=IntervalTrigger(
                    minutes=interval, start_date=now + timedelta(seconds=EXTRACT_DELAY_SECONDS)
                )
            )
        if crawl_job is None and extract_job is None:
            logger.info("job 尚未注册（--once 直接执行），跳过间隔热更新")
            self._current_interval = interval
            return
        self._current_interval = interval
        logger.info("检测到配置变更，抓取间隔调整为 %d 分钟", interval)

    def _cleanup_expired(self) -> dict:
        """过期清理：有 deadline 且已过期的删除；无 deadline 的按发布日 + expire_days 兜底。

        cleanup_enabled=False 时只统计不删除。
        """
        crawl = self._store.get_crawl()
        enabled = crawl.cleanup_enabled
        expire_days = crawl.expire_days
        today = datetime.now().date().isoformat()
        cutoff = (datetime.now().date() - timedelta(days=expire_days)).isoformat()

        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT id, title, deadline, published_at FROM notices
                   WHERE (deadline IS NOT NULL AND deadline != '' AND deadline < ?)
                      OR ((deadline IS NULL OR deadline = '') AND published_at IS NOT NULL
                          AND published_at != '' AND published_at < ?)""",
                (today, cutoff),
            ).fetchall()
        finally:
            conn.close()
        expired = [dict(r) for r in rows]

        if not expired:
            logger.info("过期清理: 无过期通知")
            return {"enabled": enabled, "expire_days": expire_days, "expired": 0, "deleted": 0}

        if not enabled:
            logger.warning(
                "过期清理已禁用（crawl.cleanup_enabled=false），本轮发现过期 %d 条，仅报告不删除",
                len(expired),
            )
            return {"enabled": False, "expired": len(expired), "deleted": 0}

        deleted = 0
        conn = get_connection()
        try:
            for n in expired:
                try:
                    delete_notice(conn, n["id"])  # 连带删除待办
                    self._remove_notice_vectors(n["id"])
                    deleted += 1
                except Exception as e:
                    logger.exception("删除过期通知失败 id=%s: %s", n["id"], e)
        finally:
            conn.close()
        logger.info(
            "过期清理完成: 过期 %d 条，删除 %d 条（含待办与向量 chunk）", len(expired), deleted
        )
        return {"enabled": True, "expire_days": expire_days, "expired": len(expired), "deleted": deleted}

    def _check_vector_consistency(self, fix_ghosts: bool = True) -> dict:
        """向量一致性检查：对比 Chroma 与 SQLite 的通知 ID 集合。

        幽灵向量（SQLite 已删除但 Chroma 残留）默认自动清理，防止 RAG 污染；
        缺失向量（已提取但未索引）只报告，由提取链路的增量索引补充。
        判定实现统一在 storage.vectorstore.check_consistency（模块 2.5），
        幽灵基准取 SQLite 全量通知 ID，避免把 raw/failed 通知的有效向量误删。
        """
        from storage.vectorstore import check_consistency

        result = check_consistency(fix_ghosts=fix_ghosts)
        if "error" in result:
            logger.warning("读取向量库失败: %s", result["error"])
            return result

        logger.info(
            "向量一致性: sqlite=%d chroma=含%d条通知 幽灵=%d 清理=%d 缺失=%d",
            result["sqlite_notices"],
            result["chroma_notices"],
            len(result.get("ghosts_found") or []),
            result["ghosts_removed"],
            len(result["missing"]),
        )
        if result.get("ghosts_found"):
            logger.warning(
                "向量一致性: 发现 %d 个幽灵向量（已清理）: %s",
                len(result["ghosts_found"]),
                result["ghosts_found"],
            )
        if result["missing"]:
            logger.warning(
                "向量一致性: %d 条已提取通知缺少向量（待增量索引补齐）: %s",
                len(result["missing"]),
                result["missing"][:20],
            )
        return result

    @staticmethod
    def _remove_notice_vectors(notice_id: int) -> None:
        """删除某通知的向量 chunk（延迟导入 VectorIndex）。"""
        from storage.vectorstore import get_vector_index

        try:
            get_vector_index().remove_notice(notice_id)
        except Exception as e:
            logger.warning("删除向量 chunk 失败 notice_id=%s: %s", notice_id, e)


def start_scheduler(config: Optional[SchedulerConfig] = None) -> Optional[NoticeScheduler]:
    """API 进程集成入口（阶段 6）：创建并启动调度器，返回实例供 lifespan 关闭时 stop()。

    与 CLI 的差异：
      - 间隔不覆盖（interval_override=None），由 config-watch job 热更新 crawl.interval_minutes；
      - 日志用专用 logger（setup_api_logging），不接管 root，避免污染 uvicorn / API 日志；
      - config.enabled=false 时记日志并返回 None（不启动）。

    Args:
        config: SchedulerConfig；未传时从 ConfigStore 单例读取（app.yaml scheduler 段）。
    """
    if config is None:
        config = ConfigStore.get_instance().get_scheduler()
    if not config.enabled:
        logger.info("调度器已禁用（scheduler.enabled=false），跳过启动")
        return None

    log_path = setup_api_logging(config.log_file)
    logger.info("=" * 60)
    logger.info("调度器并入后端进程启动（日志: %s）", log_path)
    logger.info("=" * 60)

    scheduler = NoticeScheduler(
        enable_daily=config.enable_daily,
        enable_extract=config.enable_extract,
        enable_reminder=config.enable_reminder,
        enable_health=config.enable_health,
    )
    scheduler.print_recovery_info()
    scheduler.start()
    logger.info("调度器已并入后端进程（stop 由 API lifespan 统一处理）")
    return scheduler


def main():
    parser = argparse.ArgumentParser(description="校园通知定时调度器（W1 模块 1.1）")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只跑一轮完整闭环（抓取→提取→每日体检）后退出",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="覆盖抓取间隔（分钟），便于快速验证；默认读 config/app.yaml 的 crawl.interval_minutes",
    )
    parser.add_argument(
        "--no-daily",
        action="store_true",
        help="跳过每日过期清理 + 向量一致性检查 job",
    )
    parser.add_argument(
        "--no-extract",
        action="store_true",
        help="跳过提取 job（抓取完成后不触发 LLM 提取，避免消耗配额）",
    )
    parser.add_argument(
        "--no-reminder",
        action="store_true",
        help="跳过每日截止提醒扫描 job（模块 3.2）",
    )
    parser.add_argument(
        "--no-health",
        action="store_true",
        help="跳过每日体检（模块 4.2）",
    )
    parser.add_argument(
        "--log",
        type=str,
        default=None,
        help="日志文件路径（默认 data/logs/scheduler.log）",
    )
    args = parser.parse_args()

    # --no-* 开关映射为配置项（阶段 6）：app.yaml scheduler 段是默认值，CLI 开关只能再关不能开
    scfg = ConfigStore.get_instance().get_scheduler()
    enable_daily = scfg.enable_daily and not args.no_daily
    enable_extract = scfg.enable_extract and not args.no_extract
    enable_reminder = scfg.enable_reminder and not args.no_reminder
    enable_health = scfg.enable_health and not args.no_health
    log_file = args.log or scfg.log_file

    log_path = setup_logging(log_file)
    logger.info("=" * 60)
    logger.info("校园通知调度器启动（日志: %s）", log_path)
    logger.info("=" * 60)

    scheduler = NoticeScheduler(
        interval_override=args.interval,
        enable_daily=enable_daily,
        enable_extract=enable_extract,
        enable_reminder=enable_reminder,
        enable_health=enable_health,
    )
    scheduler.print_recovery_info()

    if args.once:
        scheduler.run_once()
        logger.info("--once 模式执行完毕，退出")
        return

    scheduler.start()
    logger.info("调度器运行中。Ctrl+C 停止；停止/崩溃后重启会自动恢复并继续（已抓 URL 不重复）。")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("收到停止信号，调度器正在退出...")
    finally:
        scheduler.stop()


if __name__ == "__main__":
    main()
