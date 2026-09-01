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
from typing import Callable

from desktop.config import ShellSettings
from desktop.server import ServerManager

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 30.0  # 等待后端就绪的秒数
HEALTH_PATH = "/api/v1/health"


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
    ) -> None:
        self.host = host
        self.port = port
        self.title = title
        self.settings = ShellSettings().load()
        self.server = ServerManager(host=host, port=port)
        self._window: Any | None = None  # noqa: F841 - B06 起使用

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

        关闭窗口后 webview.start() 返回 → 触发退出路径（B03 无托盘，关闭即退出）。
        """
        import webview  # 延迟导入：无 webview 环境（回归/CI）不阻塞模块加载

        url = self.server.url
        self._window = webview.create_window(
            self.title,
            url,
            width=1280,
            height=800,
            # B04/B10 处理 localStorage 持久化：此处保持默认 private_mode，
            # 避免在未定案前引入行为变更。
        )
        try:
            webview.start()
        except Exception:  # noqa: BLE001
            logger.exception("窗口事件循环异常")
        finally:
            self.quit()

    # ---------- 退出 ----------
    def quit(self) -> None:
        """退出编排（B03 基础版）。

        顺序：停 uvicorn（should_exit + join）→ 预留 scheduler.stop 与
        托盘停止位（B06/B08 填充）→ sys.exit(0)。
        """
        logger.info("开始退出……")
        # 预留：self.scheduler.stop()（B08 从 app.state 取调度器）
        # 预留：托盘停止（B06/B08）
        self.server.stop(timeout=10)
        logger.info("退出完成")
        sys.exit(0)
