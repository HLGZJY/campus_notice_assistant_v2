"""B05 日志落盘与崩溃兜底验收（离线）。

对应 docs/DESKTOP-ACCEPTANCE.md §4：
  1. sys.stdout = None 时启动流程不抛异常
  2. root logger 写入 data/logs/app.log
  3. uvicorn logger 不覆盖 root（propagate=False 生效）
  4. 文件轮转在 5MB 边界生效，保留 3 份
  5. 未捕获异常经 excepthook 落盘完整 traceback

设计依据见 docs/DESKTOP-UPGRADE.md §6 K3：
console=False 时 sys.stdout 为 None，任何残留 print()/basicConfig() 到 stdout
都会导致「启动即退」——这是关闭控制台后最高频的崩溃原因（R1）。

注意：所有 case 使用 tempfile 临时日志目录，**绝不触碰生产 data/logs/app.log**。
setup_logging(log_path=...) 每次会重建共享 handler，天然隔离各 case。

依赖：desktop.logging_setup（B05 实现后自动激活；当前未实现则整脚本 SKIP）

用法：python test_desktop_logging.py
"""
from __future__ import annotations

import logging
import logging.config
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B05", module="desktop.logging_setup", title="日志落盘与崩溃兜底（K3）")


def _tmp_log() -> Path:
    """为单个 case 生成独立临时日志路径。"""
    return Path(tempfile.mkdtemp(prefix="b05_")) / "app.log"


def _setup(ls, console: bool = False) -> Path:
    """配置临时日志目录下的 root logger（默认静默，避免测试输出噪音）。"""
    setup_logging = suite.require(ls, "setup_logging")
    log_path = _tmp_log()
    setup_logging(log_path=log_path, console=console)
    return log_path


@suite.case("1. sys.stdout = None 时启动流程不抛异常")
def _(ls):
    setup_logging = suite.require(ls, "setup_logging")
    log_path = _tmp_log()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        # console=False 模拟：stdout/stderr 为 None
        sys.stdout = None
        sys.stderr = None
        setup_logging(log_path=log_path)  # 不应抛异常
        root_handlers = logging.getLogger().handlers
        # 只允许文件 handler（RotatingFileHandler 也是 StreamHandler 子类），
        # 不得存在真正的控制台 StreamHandler（非 FileHandler）
        console_handlers = [
            h
            for h in root_handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert not console_handlers, "stdout=None 时 root 不应含控制台 StreamHandler"
        # 残留 print() 到重定向后的 stdout 不应崩溃
        print("残留 print 到 stdout")
    finally:
        sys.stdout = old_out
        sys.stderr = old_err


@suite.case("2. root logger 写入 data/logs/app.log")
def _(ls):
    log_path = _setup(ls)
    logging.getLogger("desktop.test").info("B05 写入测试消息 12345")
    for h in logging.getLogger().handlers:
        h.flush()
    assert log_path.exists(), "app.log 应被创建"
    content = log_path.read_text(encoding="utf-8")
    assert "B05 写入测试消息 12345" in content, "日志内容应包含写入的消息"


@suite.case("3. uvicorn logger 不覆盖 root（propagate=False 生效）")
def _(ls):
    build_uvicorn_log_config = suite.require(ls, "build_uvicorn_log_config")
    log_path = _setup(ls)

    cfg = build_uvicorn_log_config(log_path=log_path)
    logging.config.dictConfig(cfg)

    uvicorn_logger = logging.getLogger("uvicorn")
    assert uvicorn_logger.propagate is False, "uvicorn 应 propagate=False"

    uvicorn_logger.info("uvicorn 测试消息 888")
    logging.getLogger("desktop.app").info("app 测试消息 777")
    for h in logging.getLogger().handlers:
        h.flush()

    content = log_path.read_text(encoding="utf-8")
    assert "uvicorn 测试消息 888" in content, "uvicorn 日志应写入共享文件"
    assert "app 测试消息 777" in content, "app 日志应写入同一文件"
    # propagate=False → uvicorn 消息不冒泡到 root 再写一份
    assert content.count("uvicorn 测试消息 888") == 1, "uvicorn 日志不应重复"


@suite.case("4. 文件轮转在 5MB 边界生效，保留 3 份")
def _(ls):
    log_path = _setup(ls)
    lg = logging.getLogger("desktop.rotation")
    chunk = "X" * 8192
    # 写入远超 5MB（~32MB），应触发多次滚动
    for _ in range(4000):
        lg.info("rot %s", chunk)
    for h in logging.getLogger().handlers:
        h.flush()
    assert (log_path.parent / "app.log.1").exists(), "应出现 app.log.1"
    assert (log_path.parent / "app.log.2").exists(), "应出现 app.log.2"
    assert (log_path.parent / "app.log.3").exists(), "应出现 app.log.3"
    # backupCount=3 → 最多保留 3 份，.4 不存在
    assert not (log_path.parent / "app.log.4").exists(), "不应出现 app.log.4"


@suite.case("5. 未捕获异常经 excepthook 落盘完整 traceback")
def _(ls):
    install_excepthooks = suite.require(ls, "install_excepthooks")
    log_path = _setup(ls)
    # notify=False：测试环境不弹窗，仅验证落盘
    install_excepthooks(notify=False)

    def boom():
        raise ValueError("B05 制造的未捕获异常")

    try:
        boom()
    except ValueError:
        sys.excepthook(*sys.exc_info())

    for h in logging.getLogger().handlers:
        h.flush()
    content = log_path.read_text(encoding="utf-8")
    assert "Traceback" in content, "应含 Traceback"
    assert "ValueError" in content, "应含异常类型"
    assert "B05 制造的未捕获异常" in content, "应含异常消息"


@suite.case("6. 未捕获异常写独立崩溃转储（B21.T3）")
def _(ls):
    write_crash_report = suite.require(ls, "write_crash_report")
    import desktop.logging_setup as ls_mod

    # 用临时日志目录隔离，避免写生产 data/logs/crash
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="b21_crash_"))
    old_crash_dir = ls_mod.CRASH_DIR
    ls_mod.CRASH_DIR = tmp / "crash"
    try:
        path = write_crash_report(
            title="主线程未捕获异常",
            traceback_text="Traceback (most recent call last):\n  ValueError: test",
            exc=ValueError("test"),
        )
        assert path.exists(), "崩溃转储文件应创建"
        content = path.read_text(encoding="utf-8")
        assert "崩溃转储" in content, "应含标题头"
        assert "Traceback" in content, "应含 traceback"
        assert "版本" in content, "应含版本信息"
        assert "ValueError('test')" in content, "应含异常摘要"
        assert path.suffix == ".txt", "应为 .txt"
    finally:
        ls_mod.CRASH_DIR = old_crash_dir
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


@suite.case("7. export_logs 导出 crash 与调度日志（B21.T3）")
def _(ls):
    export_logs = suite.require(ls, "export_logs")
    import desktop.logging_setup as ls_mod

    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="b21_export_"))
    source_dir = tmp / "logs"
    source_dir.mkdir(parents=True)
    # 模拟 app.log、轮转备份、调度日志、崩溃转储
    (source_dir / "app.log").write_text("app", encoding="utf-8")
    (source_dir / "app.log.1").write_text("app1", encoding="utf-8")
    (source_dir / "scheduler.log").write_text("sched", encoding="utf-8")
    crash_dir = source_dir / "crash"
    crash_dir.mkdir()
    (crash_dir / "crash_20260901.txt").write_text("crash", encoding="utf-8")

    old_log_dir = ls_mod.LOG_DIR
    ls_mod.LOG_DIR = source_dir
    dest_dir = tmp / "exported"
    try:
        out = export_logs(dest_dir)
        assert (out / "app.log").exists(), "应导出 app.log"
        assert (out / "app.log.1").exists(), "应导出轮转备份"
        assert (out / "scheduler.log").exists(), "应导出调度日志"
        assert (out / "crash" / "crash_20260901.txt").exists(), "应导出崩溃转储"
    finally:
        ls_mod.LOG_DIR = old_log_dir
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(suite.run())
