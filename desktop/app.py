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


def has_running_tasks() -> bool:
    """是否有进行中的异步任务（B08 用于退出二次确认）。

    判断标准：tasks 表中存在 ``queued`` 或 ``running`` 状态的任务
    （status ∈ {queued, running}，见 storage/db.py tasks 表）。
    队列里仍有排队任务同样视为「进行中」——退出会中断它们，需提示用户。

    DB 不可用 / 未初始化时返回 False（宁可放行退出，也不因查询异常阻塞，
    与 B05「退出不被打断」原则一致）。
    """
    try:
        from storage.db import get_connection

        conn = get_connection()
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status IN ('queued', 'running')"
            )
            return int(cur.fetchone()[0]) > 0
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - DB 不可用视为无进行中任务
        logger.debug("has_running_tasks 查询异常（视为无任务）", exc_info=True)
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
        enable_single_instance: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.title = title
        self.enable_tray = enable_tray  # --no-tray 可整体禁用托盘（回滚点）
        # B07：单实例锁开关。False 则跳过加锁（可双开，R7 风险回归，仅排障用）
        self.enable_single_instance = enable_single_instance
        self.settings = ShellSettings().load()
        self.server = ServerManager(host=host, port=port)
        self._window: Any | None = None
        self.tray: Any = None
        self._exiting = False  # 托盘「退出」触发后置位，closing 放行
        self._tray_available = False  # 托盘是否成功启动（影响关闭语义）
        self._shutdown_done = False  # B08：退出序列是否已执行（幂等）
        self.shutdown_hook: Any = None  # B08：WM_QUERYENDSESSION 关机钩子
        self.single_instance: Any = None  # B07：单实例锁（见 run()）

    # ---------- 主入口 ----------
    def run(self) -> None:
        """启动后端 → 等 health → 创建窗口并进入 UI 事件循环。

        窗口关闭后 webview.start() 返回，随后走退出路径。
        """
        # B05：确保日志落盘 + 崩溃钩子已就绪（幂等，重复调用无害）
        from desktop.logging_setup import install_excepthooks, setup_logging

        setup_logging()
        install_excepthooks()

        # B07：单实例锁。已有实例 → 已尝试唤起它 → 本实例退出（不双开，R7）
        if self.enable_single_instance and not self._acquire_single_instance():
            logger.warning("检测到已有实例，已发送唤起指令，本实例退出")
            sys.exit(0)

        self.start_backend()
        if not self._await_ready():
            logger.error("后端在 %.0fs 内未就绪，退出", HEALTH_TIMEOUT)
            sys.exit(1)
        self._create_window()

    # ---------- 单实例（B07） ----------
    def _acquire_single_instance(self) -> bool:
        """取得单实例锁；已是主实例则注册唤起回调并返回 True，否则返回 False。

        唤起回调复用托盘「打开主界面」的恢复逻辑（restore + show + focus）。
        """
        from desktop.single_instance import SingleInstanceLock

        lock = SingleInstanceLock()
        if not lock.acquire(on_activate=self._on_activate_request):
            return False
        self.single_instance = lock
        logger.info("已取得单实例锁（socket 端口 %d）", lock.port)
        return True

    def _on_activate_request(self) -> None:
        """第二实例发起唤起：恢复并显示主窗口（restore + show + focus）。

        由单实例监听线程触发；与托盘「打开主界面」复用同一恢复逻辑。
        窗口尚未创建时忽略（此时应用仍在启动早期，随后自会显示）。
        """
        self._tray_open()

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
        # B06.T3：从 settings 恢复窗口几何（首次启动用默认）
        geom = self.settings.get_window_geometry()
        width, height = geom["width"], geom["height"]
        x, y = geom.get("x"), geom.get("y")
        self._window = webview.create_window(
            self.title,
            url,
            width=width,
            height=height,
            x=x,
            y=y,
            maximized=bool(geom.get("maximized")),
            # B04/B10 处理 localStorage 持久化：此处保持默认 private_mode，
            # 避免在未定案前引入行为变更。
        )
        # K5：点 X 是否最小化到托盘，由 closing handler 决定（见 _on_closing）
        self._window.events.closing += self._on_closing
        # B08.T2：注册 Windows 关机/注销信号（WM_QUERYENDSESSION 走同一退出路径）。
        # 在 webview.start() 前后皆可调用；非 Windows/无 GUI 环境静默降级。
        self._setup_shutdown_hook()
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

    # ---------- 关机信号（B08.T2） ----------
    def _setup_shutdown_hook(self) -> None:
        """注册 Windows WM_QUERYENDSESSION 关机/注销钩子（K5）。

        非 Windows / 无 GUI / 创建失败 → 静默降级（self.shutdown_hook=None），
        不阻断启动。回调 ``_on_system_shutdown`` 在钩子消息泵线程被调用，
        需线程安全。
        """
        try:
            from desktop.shutdown_hook import ShutdownHook

            hook = ShutdownHook(
                on_shutdown=self._on_system_shutdown,
                on_endsession=self._on_system_shutdown,
            )
            if hook.start():
                self.shutdown_hook = hook
            else:
                self.shutdown_hook = None
        except Exception:  # noqa: BLE001 - 关机钩子失败不影响启动
            logger.debug("关机钩子创建失败（降级，无关机拦截）", exc_info=True)
            self.shutdown_hook = None

    def _on_system_shutdown(self) -> None:
        """系统关机/注销触发：走统一退出路径（不弹确认对话框）。

        系统关机不应被用户对话框阻塞（用户多半看不到也来不及点），
        这里直接进入完整退出序列：写几何 → 停后端 → 停调度 → 停托盘 →
        释放锁，并调用 sys.exit。若系统随后强制结束进程，SQLite 数据已在
        优雅停止中 flush（无 -journal 残留），满足 Gate B08 完整性要求。
        """
        logger.info("收到系统关机/注销信号（WM_QUERYENDSESSION），进入退出序列")
        try:
            self._exiting = True  # closing 放行
            self._do_exit()
        except Exception:  # noqa: BLE001 - 关机路径异常不得阻塞系统关机
            logger.exception("关机退出序列异常（放行系统关机）")

    # ---------- 退出 ----------
    def quit(self) -> None:
        """触发退出（托盘「退出」入口；幂等，可多线程调用）。

        B08 完整编排：先做「有进行中任务」的二次确认（B08.T3），通过后才
        置退出标志 → 停托盘 → 写几何 → 销毁窗口（closing 放行，
        webview.start() 返回）→ 由主线程 finally 的 _shutdown() 执行完整序列。
        """
        if self._exiting:
            return  # 已在退出流程中（幂等）
        # (B08.T3 二次确认将在此插入)
        self._exiting = True
        logger.info("开始退出……")
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                logger.debug("托盘停止异常（忽略）", exc_info=True)
        self._save_window_geometry()  # B06.T3：退出前记忆窗口几何
        if self._window is not None:
            try:
                self._window.destroy()  # closing 放行 → webview.start() 返回
            except Exception:  # noqa: BLE001
                logger.exception("销毁窗口异常")

    # (B08.T3 二次确认方法 _confirm_exit 将在下一原子提交加入)
    def _save_window_geometry(self) -> None:
        """退出前把当前窗口位置/大小写入 settings.json（B06.T3）。

        读 window.x/y/width/height 属性；窗口不可用时（异常路径）跳过，
        不打断退出。maximized 暂不判断（pywebview 无稳定窗口态 API），
        保留 settings 字段默认 False，后续批次可完善。
        """
        w = self._window
        if w is None:
            return
        try:
            ok = self.settings.set_window_geometry(
                x=w.x, y=w.y, width=w.width, height=w.height,
            )
            if ok:
                logger.info("窗口几何已记忆：x=%s y=%s %sx%s", w.x, w.y, w.width, w.height)
        except Exception:  # noqa: BLE001 - 几何保存失败不打断退出
            logger.debug("窗口几何保存失败（忽略）", exc_info=True)

    def _stop_scheduler(self) -> None:
        """显式停止调度器（K5 退出序列第 3 步，幂等）。

        uvicorn 优雅关闭时 lifespan 会停调度器（api.main.py），此处为壳层
        显式兜底（在 server 线程 join 后调用，确保任一退出路径都不残留调度线程）。
        无调度器（test 模式 / 未启用）时静默跳过。
        """
        try:
            from api.main import app

            sch = getattr(app.state, "scheduler", None)
            if sch is not None:
                sch.stop()
                logger.info("调度器已停止（壳层显式）")
        except Exception:  # noqa: BLE001 - 停调度失败不阻塞退出
            logger.debug("壳层显式停调度器失败（由 uvicorn lifespan 兜底）", exc_info=True)

    def _do_exit(self) -> None:
        """K5 完整退出序列（幂等；主线程/关机钩子线程均可调用）。

        顺序（DESKTOP-UPGRADE.md §6 K5）：
          写窗口几何 → server.should_exit=True+join → scheduler.stop()
          → pystray.stop() → 释放单实例锁 → sys.exit(0)
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        # 1. 写窗口几何
        self._save_window_geometry()
        # 2. 停后端（should_exit=True → join(timeout)，uvicorn lifespan 顺带停调度+任务管理）
        self.server.stop(timeout=10)
        # 3. 显式停调度器（兜底幂等）
        self._stop_scheduler()
        # 4. 停托盘
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001 - 托盘停止失败不阻塞退出
                logger.debug("托盘停止异常（忽略）", exc_info=True)
        # 5. 释放单实例锁
        if self.single_instance is not None:
            try:
                self.single_instance.release()
                logger.info("单实例锁已释放")
            except Exception:  # noqa: BLE001 - 释放失败不阻塞退出
                logger.debug("单实例锁释放异常（忽略）", exc_info=True)
            self.single_instance = None
        # 6. 收尾
        if self.shutdown_hook is not None:
            try:
                self.shutdown_hook.stop()
            except Exception:  # noqa: BLE001
                logger.debug("关机钩子停止异常（忽略）", exc_info=True)
        logger.info("退出完成")
        sys.exit(0)

    def _shutdown(self) -> None:
        """主线程收尾（webview.start() 返回后执行）：执行完整退出序列。

        B08：把退出编排收拢到 _do_exit()（幂等），保证托盘退出、窗口关闭、
        关机信号、watchdog 任一触发都走同一 K5 序列。
        """
        self._do_exit()
