"""壳主控：编排启动 / health 就绪等待 / 窗口创建 / 退出（B03）。

启动序列（DESKTOP-UPGRADE.md §4.1 生命周期）：
  单实例锁 → 端口粘性 → 起后端 → 等 /api/v1/health 200 → 显示窗口

B03 阶段实现「起后端 → 等 health → 建窗口 → 关闭即退出」的最小闭环；
单实例（B07）、托盘（B06）、看门狗（B04）、优雅退出完整编排（B08）在此之上填充。

退出路径（B03 基础版，K5 完整版在 B08）：
  server.should_exit=True → join(timeout=10) → sys.exit(0)；
  预留 scheduler.stop() 与托盘停止位（B06/B08 填充）。
"""
from __future__ import annotations

import logging
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from desktop.config import ShellSettings
from desktop.server import ServerManager

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 30.0  # 等待后端就绪的秒数
HEALTH_PATH = "/api/v1/health"

# 默认窗口几何（首次启动 / 无历史记录时使用）
DEFAULT_WINDOW = {"width": 1280, "height": 800}


def has_running_tasks() -> bool:
    """是否有进行中的异步任务（B08 用于退出二次确认）。

    骨架阶段占位：直接返回 False（B08 接入 TaskManager 后改为真实判断）。
    """
    # TODO(B08): 从 api.tasks.manager 查询 running 任务数
    return False


def wait_for_health(
    base_url: str,
    timeout: float = HEALTH_TIMEOUT,
    interval: float = 0.3,
) -> bool:
    """轮询 /api/v1/health 直到返回 200。

    返回 True 表示就绪；超时返回 False（由调用方决定是否降级/报错）。
    """
    url = f"{base_url}{HEALTH_PATH}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - 服务未就绪期间持续重试
            pass
        time.sleep(interval)
    return False


class DesktopApp:
    """桌面壳主控：组装后端托管与窗口，管理生命周期。

    B03 提供：start() 起后端+等健康+建窗口；quit() 关闭窗口后退出。
    B06/B07/B08 将在此扩展托盘、单实例、优雅退出完整序列。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
        title: str = "校园通知智能助手",
        enable_tray: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.title = title
        self.enable_tray = enable_tray  # --no-tray 可整体禁用托盘（回滚点）
        self.settings = ShellSettings().load()
        self.server = ServerManager(host=host, port=port)
        self._window: Any | None = None
        self.tray: Any = None
        self._exiting = False  # 托盘「退出」触发后置位，closing 放行
        self._tray_available = False  # 托盘是否成功启动（影响关闭语义）

    # ---------- 主入口 ----------
    def run(self) -> None:
        """启动后端 → 等 health → 创建窗口并进入 UI 事件循环。

        窗口关闭后 webview.start() 返回，随后走退出路径。
        """
        # B05：确保日志落盘 + 崩溃钩子已就绪（幂等，重复调用无害）
        from desktop.logging_setup import install_excepthooks, setup_logging

        setup_logging()
        install_excepthooks()

        self.start_backend()
        if not self._await_ready():
            logger.error("后端在 %.0fs 内未就绪，退出", HEALTH_TIMEOUT)
            sys.exit(1)
        self._create_window()

    # ---------- 后端 ----------
    def start_backend(self) -> None:
        """起 uvicorn daemon 线程。"""
        self.server.start()

    def _await_ready(self) -> bool:
        logger.info("等待后端就绪：%s", self.server.url)
        ready = wait_for_health(self.server.url)
        if ready:
            logger.info("后端就绪（/api/v1/health 200）")
        return ready

    # ---------- 窗口 ----------
    def _create_window(self) -> None:
        """创建 pywebview 窗口并加载后端地址（webview 延迟导入）。

        B06 起：挂 closing 事件（K5：托盘可用时点 X → hide，进程常驻；
        无托盘时放行关闭，关闭即退出），随后创建托盘。

        窗口销毁后 webview.start() 返回 → finally 收尾退出。
        """
        import webview  # 延迟导入：无 webview 环境（回归/CI）不阻塞模块加载

        url = self.server.url
        # B06.T3 会在此替换为 settings 恢复的几何；当前用默认几何
        width, height = DEFAULT_WINDOW["width"], DEFAULT_WINDOW["height"]
        self._window = webview.create_window(
            self.title,
            url,
            width=width,
            height=height,
            # B04/B10 处理 localStorage 持久化：此处保持默认 private_mode，
            # 避免在未定案前引入行为变更。
        )
        # K5：点 X 是否最小化到托盘，由 closing handler 决定（见 _on_closing）
        self._window.events.closing += self._on_closing
        # 创建托盘（失败则 _tray_available=False，退回「关闭即退出」）
        self._setup_tray()
        try:
            webview.start()
        except Exception:  # noqa: BLE001
            logger.exception("窗口事件循环异常")
        finally:
            self._shutdown()

    def _on_closing(self, *_: Any) -> bool:
        """pywebview closing 事件（K5）。

        返回 True → 放行关闭；返回 False → 取消关闭（最小化到托盘）。
        - 托盘「退出」触发后（self._exiting）→ 返回 True 放行，让 webview.start()
          返回、走退出收尾；
        - 托盘可用（self._tray_available）→ window.hide() + 返回 False，进程常驻；
        - 无托盘 → 返回 True（关闭即退出，回滚点语义）。
        """
        if self._exiting:
            return True  # 已进入退出流程，放行关闭
        if self._tray_available:
            try:
                self._window.hide()
            except Exception:  # noqa: BLE001
                logger.exception("隐藏窗口异常")
            logger.info("窗口隐藏到托盘，进程常驻（托盘「退出」可退出）")
            return False  # 取消关闭
        return True  # 无托盘，关闭即退出

    # ---------- 托盘 ----------
    def _setup_tray(self) -> None:
        """创建并启动托盘；失败（无 GUI/CI/--no-tray）则 _tray_available=False。

        回滚点：--no-tray 禁用托盘后，closing 走「关闭即退出」分支。
        """
        if not self.enable_tray:
            logger.info("托盘已禁用（--no-tray），关闭即退出")
            self._tray_available = False
            return
        try:
            from desktop.tray import DesktopTray

            self.tray = DesktopTray(
                title=self.title,
                callbacks={
                    "on_open": self._tray_open,
                    "on_quit": self.quit,
                    "on_open_log_dir": self._open_log_dir,
                    # on_toggle_pause / on_check_update：B06 不注入，走 tray 内置占位通知
                },
            )
            if self.tray.start():
                self._tray_available = True
        except Exception:  # noqa: BLE001 - 无托盘环境静默降级
            logger.debug("托盘创建失败，降级为无托盘模式", exc_info=True)
            self.tray = None
            self._tray_available = False

    def _tray_open(self) -> None:
        """托盘「打开主界面」：恢复并显示窗口（B07 唤起主窗口复用此路径）。"""
        try:
            if self._window is None:
                return
            self._window.restore()
            self._window.show()
            logger.info("托盘「打开主界面」：窗口已恢复并显示")
        except Exception:  # noqa: BLE001
            logger.exception("托盘打开主界面异常")

    def _open_log_dir(self) -> None:
        """托盘「打开日志目录」：用系统文件管理器打开 data/logs。"""
        try:
            import os

            from utils.app_paths import get_data_dir

            log_dir = get_data_dir() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(log_dir))  # type: ignore[attr-defined]  # Windows
            logger.info("托盘「打开日志目录」：%s", log_dir)
        except Exception:  # noqa: BLE001
            logger.exception("打开日志目录失败")

    # ---------- 退出 ----------
    def quit(self) -> None:
        """触发退出（B06 托盘「退出」入口；幂等，可多线程调用）。

        B06 阶段：置退出标志 → 停托盘 → 销毁窗口（closing 放行，
        webview.start() 返回）→ 由主线程 finally 的 _shutdown() 收尾停后端。
        完整退出编排（写几何 → server → scheduler → tray → 锁）在 B08 补全。
        """
        if self._exiting:
            return  # 已在退出流程中（幂等）
        self._exiting = True
        logger.info("开始退出……")
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                logger.debug("托盘停止异常（忽略）", exc_info=True)
        if self._window is not None:
            try:
                self._window.destroy()  # closing 放行 → webview.start() 返回
            except Exception:  # noqa: BLE001
                logger.exception("销毁窗口异常")

    def _shutdown(self) -> None:
        """主线程收尾（webview.start() 返回后执行）：停后端并退出进程。

        预留：scheduler.stop() 与单实例锁释放（B08 在 quit() 完整序列中补齐）。
        """
        self.server.stop(timeout=10)
        logger.info("退出完成")
        sys.exit(0)
