"""阶段 6 调度器并入验收（离线直测 start_scheduler，不经过 FastAPI lifespan）。

覆盖验收信号：
  1. start_scheduler(config) 创建并启动调度器，首周期立即跑 crawl →
     scheduler_log 出现 crawl success 行（「起服务观察 scheduler_log 写入」的离线等价）；
  2. config.enabled=false → 返回 None（API 集成开关生效）；
  3. NoticeScheduler.get_status() 运行中返回 jobs 清单（状态端点数据源）；
  4. CLI 兼容性由原有 test_*.py / crash_drill 保证，本测试不重复。

隔离：临时 SQLite + 临时配置目录 + patch crawl_all_sources/extract_batch（离线不碰网络）。
用法：python test_scheduler_integration.py
"""
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

import storage.db
from config.schema import SchedulerConfig
from config.store import ConfigStore
from storage.db import get_connection
from scheduler import NoticeScheduler, start_scheduler

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def _write_temp_config(config_dir: Path) -> None:
    """最小配置：interval=1 分钟，调度器全开（供 start_scheduler 读取）。"""
    schools = config_dir / "schools"
    schools.mkdir(parents=True, exist_ok=True)
    (config_dir / "app.yaml").write_text(
        """active_school: scuec
models:
  extraction:
    provider: opencode-zen
    model: model-a
  qa:
    provider: opencode-zen
    model: model-a
  todo:
    provider: opencode-zen
    model: model-a
  embedding:
    provider: local
    model: emb-model
providers:
  opencode-zen:
    name: opencode-zen
    base_url: https://example.com/v1
    api_key_env: OPENCODE_API_KEY
  local:
    name: local
    base_url: ""
    api_key_env: ""
crawl:
  interval_minutes: 1
scheduler:
  enabled: true
  enable_daily: false
  enable_extract: false
  enable_reminder: false
  enable_health: false
  log_file: data/logs/scheduler.log
""",
        encoding="utf-8",
    )
    (schools / "scuec.yaml").write_text(
        """name: 测试学校
code: scuec
sources:
- name: 测试来源
  type: web
  list_url: http://example.com/1.htm
  max_pages: 1
""",
        encoding="utf-8",
    )


def _patch_jobs():
    """把 scheduler 模块的抓取/提取替换为离线桩（返回空，不碰网络/LLM）。"""
    import scheduler as sched_mod

    originals = (sched_mod.crawl_all_sources, sched_mod.extract_batch)
    sched_mod.crawl_all_sources = lambda **kwargs: {}
    sched_mod.extract_batch = lambda **kwargs: {"processed": 0, "summary": {}}
    return originals


def _restore_jobs(originals) -> None:
    import scheduler as sched_mod

    sched_mod.crawl_all_sources, sched_mod.extract_batch = originals


def _wait_for_crawl_row(conn, timeout: float = 8.0) -> dict | None:
    """轮询 scheduler_log 直到出现 job_name='crawl' 的 success 行。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = conn.execute(
            """SELECT * FROM scheduler_log
               WHERE job_name = 'crawl' ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if row is not None and row["status"] == "success":
            return dict(row)
        time.sleep(0.2)
    return None


def run() -> None:
    tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db_path = Path(tmpdir.name) / "test_scheduler_integration.db"
    config_dir = Path(tmpdir.name) / "config"
    log_path = Path(tmpdir.name) / "scheduler.log"

    storage.db.DB_PATH = db_path
    _write_temp_config(config_dir)
    ConfigStore.reset_instance()
    ConfigStore.get_instance(config_dir)
    originals = _patch_jobs()

    scheduler: NoticeScheduler | None = None
    try:
        print("== 1. start_scheduler 启动并写入 scheduler_log ==")
        scheduler = start_scheduler(
            SchedulerConfig(
                enabled=True,
                enable_daily=False,
                enable_extract=False,
                enable_reminder=False,
                enable_health=False,
                log_file=str(log_path),
            )
        )
        check("start_scheduler 返回实例", scheduler is not None)
        check("get_status running=True", scheduler is not None and scheduler.get_status()["running"] is True)

        conn = get_connection()
        try:
            run_row = _wait_for_crawl_row(conn)
        finally:
            conn.close()
        check("crawl job 写入 scheduler_log success", run_row is not None, f"{run_row}")
        if run_row:
            check("crawl 行字段齐全", run_row["job_name"] == "crawl" and run_row["failure_count"] == 0)

        print("== 2. get_status 返回已注册 job 清单 ==")
        if scheduler is not None:
            status = scheduler.get_status()
            ids = {j["id"] for j in status["jobs"]}
            check("crawl job 已注册", "crawl" in ids, f"ids={ids}")
            check("config-watch job 已注册", "config-watch" in ids, f"ids={ids}")
            check("daily/reminder 未注册（enable=false）", not ({"daily", "reminder"} & ids), f"ids={ids}")
            check("interval_minutes 来自配置", status["interval_minutes"] == 1, f"{status}")

        print("== 3. enabled=false → 不启动 ==")
        none_sched = start_scheduler(
            SchedulerConfig(
                enabled=False,
                enable_daily=True,
                enable_extract=True,
                enable_reminder=True,
                enable_health=True,
            )
        )
        check("enabled=false 返回 None", none_sched is None)
    finally:
        if scheduler is not None:
            scheduler.stop()
        _restore_jobs(originals)
        ConfigStore.reset_instance()
        tmpdir.cleanup()

    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()
