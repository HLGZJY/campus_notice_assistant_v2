"""日志落盘与崩溃兜底（B05 / DESKTOP-UPGRADE.md §6 K3）。

目标：让应用在「没有控制台」（console=False）的情况下仍可排障。这是 B09
（打包无控制台）的硬前置，对应最高风险 R1：关闭控制台后 sys.stdout 为 None，
任何残留的 ``print()`` / ``basicConfig()`` 到 stdout 都会导致「启动即退」。

本模块提供的接口（被 test_desktop_logging.py 契约引用，勿随意改名）：
  - ``LOG_PATH``            模块级常量：app 日志落点 data/logs/app.log
  - ``setup_logging()``     root logger 加 RotatingFileHandler（5MB×3），
                            并处理 stdout/stderr 为 None 时的重定向
  - ``build_uvicorn_log_config()``  返回给 uvicorn.Config(log_config=...) 的
                            dictConfig，把 uvicorn* logger 指向与 root 共享的
                            同一底层文件 handler 且 propagate=False（K3）
  - ``install_excepthooks()``  sys.excepthook + threading.excepthook
                            → 落盘完整 traceback + 弹窗（含「导出日志」按钮）
  - ``export_logs()``       一键导出日志到目标目录

关键设计（避免 uvicorn 与 app 日志互相覆盖，D-14）：
uvicorn 的 ``log_config`` 是独立 dictConfig，无法直接引用 root 上已有的
handler 对象。为避免两个独立 RotatingFileHandler 写同一文件造成的滚动竞争，
这里用一个 ``_ReuseFileHandler``（委托到模块级共享的 RotatingFileHandler 实例），
让 uvicorn logger 与 root logger 共享**同一个底层文件 handler**，配合
``propagate=False`` 做到：uvicorn 日志与 app 日志写同一文件、不重复、不互相覆盖。

回滚点：本模块为独立文件，删除即回到 v0.1.0 的 stdout 日志形态。
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from utils.app_paths import get_data_dir

# 日志目录与文件
LOG_DIR = get_data_dir() / "logs"
LOG_PATH = LOG_DIR / "app.log"
# B21.T3：独立崩溃转储目录（不随 app.log 轮转被覆盖）
CRASH_DIR = LOG_DIR / "crash"

# 复用 scheduler.py:101 已验证的滚动参数
_MAX_BYTES = 5_000_000  # 5 MB
_BACKUP_COUNT = 3

# 模块级共享的文件 handler（setup_logging 创建，uvicorn 复用同一实例）
_SHARED_FILE_HANDLER: logging.handlers.RotatingFileHandler | None = None

_LOGGER_NAME = "desktop.logging_setup"
logger = logging.getLogger(_LOGGER_NAME)


# ---------------------------------------------------------------------------
# 共享文件 handler
# ---------------------------------------------------------------------------
def _get_shared_handler(
    log_path: Path | None = None,
) -> logging.handlers.RotatingFileHandler:
    """返回（并缓存）唯一的 RotatingFileHandler 实例。

    setup_logging 与 uvicorn dictConfig 都通过它拿同一个 handler 实例，
    从而保证 app 日志与 uvicorn 日志写同一文件、滚动行为完全一致。
    """
    global _SHARED_FILE_HANDLER
    if _SHARED_FILE_HANDLER is None:
        path = log_path or LOG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            str(path),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.set_name("desktop-app-file")
        _SHARED_FILE_HANDLER = handler
    return _SHARED_FILE_HANDLER


def _reset_shared_handler() -> None:
    """清空共享 handler 缓存（测试/重配时使用），并关闭旧句柄。"""
    global _SHARED_FILE_HANDLER
    if _SHARED_FILE_HANDLER is not None:
        try:
            _SHARED_FILE_HANDLER.close()
        except Exception:  # noqa: BLE001 - 关闭失败不阻断
            pass
        _SHARED_FILE_HANDLER = None


class _ReuseFileHandler(logging.Handler):
    """委托到共享 RotatingFileHandler 的薄包装。

    供 uvicorn 的 dictConfig 使用：dictConfig 无法直接引用既有 handler 对象，
    但可以实例化一个 ``logging.Handler`` 子类。本类把 emit 委托给模块级共享
    实例，从而让 uvicorn logger 复用 root 的底层文件句柄。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._shared = _get_shared_handler()

    def emit(self, record: logging.LogRecord) -> None:
        self._shared.emit(record)

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - logging 接口
        self._shared.handleError(record)

    def setFormatter(self, fmt: logging.Formatter) -> None:  # noqa: A003
        super().setFormatter(fmt)
        self._shared.setFormatter(fmt)

    def flush(self) -> None:
        self._shared.flush()

    def close(self) -> None:
        # 不关闭共享实例（root 仍在使用），仅标记本包装关闭
        super().close()


# ---------------------------------------------------------------------------
# stdout / stderr 重定向（console=False 兼容）
# ---------------------------------------------------------------------------
def redirect_stdio_if_none(log_dir: Path | None = None) -> bool:
    """当 sys.stdout / sys.stderr 为 None 时，重定向到 data/logs/stdout.log。

    console=False 时 sys.stdout / sys.stderr 可能为 None，残留的 ``print()``
    或 ``logging.StreamHandler(sys.stdout)`` 都会抛 ``AttributeError``
    （R1「启动即退」的主因）。把它们重定向到一个追加打开的文件流，使任何
    直写 stdout 的代码都不会崩溃，且日志可追踪。

    Returns:
        bool：是否发生了重定向（stdout 原本为 None）。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return False
    out_dir = log_dir or LOG_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stream_path = out_dir / "stdout.log"
    if sys.stdout is None:
        sys.stdout = open(stream_path, "a", encoding="utf-8")  # noqa: SIM115 - 全程存活
    if sys.stderr is None:
        sys.stderr = open(stream_path, "a", encoding="utf-8")  # noqa: SIM115 - 全程存活
    logging.getLogger(__name__).info(
        "sys.stdout/stderr 为 None，已重定向到 %s", stream_path
    )
    return True


# ---------------------------------------------------------------------------
# root logger 落盘
# ---------------------------------------------------------------------------
def setup_logging(
    log_path: Path | None = None, *, console: bool = True
) -> Path:
    """配置 root logger：旋转文件落盘（5MB×3）+ 可选控制台。

    - 默认写到 data/logs/app.log（log_path 可覆盖，测试用临时目录）。
    - 清掉 root 上既有的 handlers（如 __main__.py 的 basicConfig StreamHandler），
      避免重复输出。
    - stdout/stderr 为 None 时重定向（console=False 兼容），且此时**不挂**
      StreamHandler，只挂文件 handler（测试契约：handlers 中不含 StreamHandler）。
    - console=False 时即使 stdout 可用也不挂控制台输出（测试/静默场景）。
    - 返回实际使用的日志文件路径。
    """
    global _SHARED_FILE_HANDLER
    path = (log_path or LOG_PATH).resolve()

    # 清掉旧的共享 handler 缓存（重配置时）
    if _SHARED_FILE_HANDLER is not None:
        _reset_shared_handler()

    # console=False 兼容：stdout 为 None 时重定向，避免任何 print 崩溃
    stdout_was_none = redirect_stdio_if_none(path.parent)

    file_handler = _get_shared_handler(path)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s %(message)s")
    file_handler.setFormatter(fmt)

    handlers: list[logging.Handler] = [file_handler]
    # 仅当显式要求控制台、且 stdout 真实可用且未发生重定向时才挂控制台输出
    if (
        console
        and not stdout_was_none
        and sys.stdout is not None
    ):
        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.setFormatter(fmt)
        console_handler.set_name("desktop-console")
        handlers.append(console_handler)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.INFO)
    for h in handlers:
        root.addHandler(h)

    # 收敛第三方日志的噪音
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    return path


# ---------------------------------------------------------------------------
# uvicorn log_config 接管（K3）
# ---------------------------------------------------------------------------
def build_uvicorn_log_config(log_path: Path | None = None) -> dict[str, Any]:
    """构造传给 ``uvicorn.Config(log_config=...)`` 的 dictConfig。

    作用：
      - ``disable_existing_loggers=False`` 防止默认 dictConfig 覆盖 root；
      - ``uvicorn`` / ``uvicorn.access`` / ``uvicorn.error`` 复用共享文件 handler
        且 ``propagate=False``——uvicorn 日志与 app 日志写同一文件、不重复、
        不互相覆盖（D-14）。
    """
    # 确保共享 handler 已就绪（若未先调 setup_logging 也能工作）
    _get_shared_handler(log_path or LOG_PATH)
    module_name = __name__

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s [%(levelname)s] %(name)s %(message)s",
            },
        },
        "handlers": {
            # 自定义 Handler 子类：委托到共享的 RotatingFileHandler
            "desktop_file": {
                "class": f"{module_name}._ReuseFileHandler",
                "formatter": "default",
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["desktop_file"],
                "propagate": False,
                "level": "INFO",
            },
            "uvicorn.error": {
                "handlers": ["desktop_file"],
                "propagate": False,
                "level": "INFO",
            },
            "uvicorn.access": {
                "handlers": ["desktop_file"],
                "propagate": False,
                "level": "INFO",
            },
        },
        # 保持 root 原有 handlers（desktop-app-file / desktop-console）不动
        "root": {},
    }


# ---------------------------------------------------------------------------
# 未捕获异常钩子 + 日志导出
# ---------------------------------------------------------------------------
def _notify_user(message: str, log_path: Path) -> None:
    """未捕获异常时的用户弹窗（含「导出日志」按钮）。

    GUI 弹窗用标准库 tkinter（无额外依赖）。无 GUI / 无 display 环境（测试、
    纯后端回归）下静默降级为仅记录日志，不阻塞、不弹窗。
    """
    try:
        import tkinter as tk
        from tkinter import messagebox, filedialog

        root = tk.Tk()
        root.withdraw()
        root.after(50, root.deiconify)
        # 非阻塞式弹窗：导出日志按钮
        def _do_export() -> None:
            dest = filedialog.askdirectory(title="选择导出日志的目录")
            if dest:
                dest_path = Path(dest)
                exported = export_logs(dest_path)
                messagebox.showinfo(
                    "日志导出",
                    f"日志已导出到：\n{exported}\n（共 {len(list(exported.glob('app.log*')))} 个文件）",
                )
            else:
                messagebox.showinfo("日志导出", "已取消导出")

        answer = messagebox.askquestion(
            "南湖窗 · 发生错误",
            f"程序发生未捕获异常：\n\n{message}\n\n"
            f"日志已记录到：\n{log_path}\n\n是否立即导出日志以便排查？",
            icon="error",
        )
        if answer == "yes":
            _do_export()
        root.destroy()
    except Exception:  # noqa: BLE001 - 弹窗失败不阻断，日志已落盘
        logging.getLogger(_LOGGER_NAME).debug("用户弹窗不可用，已降级为仅日志")


def _install_hook(name: str, notify: bool = True) -> Callable[..., Any]:
    """安装 sys/threading 级未捕获异常钩子，落盘完整 traceback。"""

    def _handle(exc_type: Any, exc: BaseException, tb: Any) -> None:
        # 键盘中断不弹窗（正常退出路径）
        if issubclass(exc_type, KeyboardInterrupt):
            logging.getLogger(_LOGGER_NAME).info("收到 KeyboardInterrupt，忽略")
            return
        lines = "".join(
            traceback.format_exception(exc_type, exc, tb)
        ).rstrip()
        # 用 logger.error 走共享文件 handler 落盘完整 traceback
        logging.getLogger("desktop.excepthook").error(
            "未捕获异常（%s）：\n%s", name, lines
        )
        # B21.T3：写独立崩溃转储（含系统信息/版本/环境），不随日志轮转丢失
        try:
            write_crash_report(
                title=f"{name} 未捕获异常",
                traceback_text=lines,
                exc=exc,
            )
        except Exception:  # noqa: BLE001 - 崩溃转储失败不阻断
            logging.getLogger(_LOGGER_NAME).debug("崩溃转储写入失败", exc_info=True)
        if notify:
            _notify_user(f"{name} 未捕获异常：{exc}", LOG_PATH)

    return _handle


def write_crash_report(
    title: str,
    traceback_text: str,
    exc: BaseException | None = None,
) -> Path:
    """写入独立崩溃转储文件（B21.T3）。

    崩溃 traceback 只写 app.log 会被 RotatingFileHandler（5MB×3）轮转覆盖，
    多崩溃/事后排查场景信息丢失。本函数把完整上下文（时间 / 版本 / 平台 /
    关键环境变量 / traceback / 异常摘要）落盘到 ``data/logs/crash/crash_<ts>.txt``，
    独立于日志轮转，供离线排查与崩溃上报。

    Args:
        title: 崩溃来源标题（如「主线程未捕获异常」）。
        traceback_text: 完整 traceback 文本。
        exc: 可选异常对象，用于提取摘要。

    Returns:
        实际写入的崩溃转储文件路径。
    """
    import datetime as _dt
    import platform
    import socket

    CRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    crash_path = CRASH_DIR / f"crash_{stamp}.txt"

    try:
        from utils.app_paths import get_version
    except Exception:  # noqa: BLE001 - 版本取不到不阻断
        get_version = lambda: "unknown"  # noqa: E731

    env_snapshot = {
        k: v
        for k, v in os.environ.items()
        if k
        in {
            "CNA_FLAVOR",
            "CNA_DESKTOP_TOKEN_SET",
            "APP_ENV",
            "HF_ENDPOINT",
            "PYTHONPATH",
        }
    }
    # 令牌值脱敏：只记录「是否已注入」，不记录明文
    if "CNA_DESKTOP_TOKEN_SET" in env_snapshot:
        env_snapshot["CNA_DESKTOP_TOKEN_SET"] = "yes"

    sections = [
        "=" * 60,
        f"南湖窗 · 崩溃转储",
        f"时间: {_dt.datetime.now().isoformat()}",
        f"标题: {title}",
        "-" * 60,
        f"版本: {get_version()}",
        f"平台: {platform.platform()}",
        f"Python: {platform.python_version()}",
        f"主机: {socket.gethostname()}",
        f"工作目录: {Path.cwd()}",
        "-" * 60,
        "关键环境变量:",
        *[f"  {k} = {v}" for k, v in env_snapshot.items()],
        "-" * 60,
        f"异常摘要: {exc!r}" if exc is not None else "异常摘要: (无)",
        "-" * 60,
        "Traceback:",
        traceback_text,
        "=" * 60,
    ]
    crash_path.write_text("\n".join(sections), encoding="utf-8")
    logger.info("崩溃转储已写入：%s", crash_path)
    return crash_path


def install_excepthooks(notify: bool = True) -> None:
    """安装 sys.excepthook + threading.excepthook（崩溃兜底，B05.T4）。

    Args:
        notify: 是否弹用户弹窗（含「导出日志」按钮）。测试/无 GUI 环境传
            False 仅落盘，避免弹窗打断自动化。
    """
    sys.excepthook = _install_hook("主线程", notify=notify)
    threading.excepthook = _install_hook("线程", notify=notify)  # type: ignore[assignment]


def export_logs(dest_dir: Path | None = None) -> Path:
    """一键导出日志：把 data/logs/ 下的关键日志复制到目标目录。

    B21.T3 增强：除 ``app.log*`` 外，同时导出：
      - ``crash/`` 崩溃转储目录（独立于日志轮转）
      - ``scheduler*.log*`` 调度日志（若存在）

    Args:
        dest_dir: 目标目录；None 时用 data/logs/export_<时间戳>。

    Returns:
        实际写入的导出目录（含 app.log* / crash / 调度日志）。
    """
    import datetime as _dt

    source = LOG_DIR
    if dest_dir is None:
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_dir = LOG_DIR / f"export_{stamp}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    # 1. 应用日志（app.log + 轮转备份）
    for f in sorted(source.glob("app.log*")):
        if f.is_file():
            shutil.copy2(f, dest_dir / f.name)
            count += 1
    # 2. 调度日志（scheduler*.log*，若存在）
    for f in sorted(source.glob("scheduler*.log*")):
        if f.is_file():
            shutil.copy2(f, dest_dir / f.name)
            count += 1
    # 3. 崩溃转储目录（B21.T3）
    crash_src = source / "crash"
    if crash_src.is_dir():
        crash_dst = dest_dir / "crash"
        crash_dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(crash_src.glob("*.txt")):
            if f.is_file():
                shutil.copy2(f, crash_dst / f.name)
                count += 1
    logger.info("日志导出完成：%s（%d 个文件）", dest_dir, count)
    return dest_dir
