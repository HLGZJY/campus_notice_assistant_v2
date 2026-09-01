"""后端托管：uvicorn 到 daemon 线程（K2）。

关键约束（DESKTOP-UPGRADE.md §6 K2）：
  - 必须用 ``uvicorn.Config(...) + uvicorn.Server(cfg)`` 实例，
    在 daemon 线程里 ``server.run()``；
  - **禁用 ``uvicorn.run()``**：它会安装 signal handler，
    在非主线程中抛 ``ValueError: set_wakeup_fd only works in main thread``；
  - 退出用 ``server.should_exit = True`` 再 ``thread.join(timeout=10)``；
  - 重启必须**重建 Server 实例**（``run()`` 返回后 loop 已关闭，不可复用）
    ——本批只提供启动/停止，看门狗重建在 B04 落地；
  - ``log_config`` 由 B05 显式传入，此处留空走 uvicorn 默认（B05 接管）。

B03 阶段端口使用简单顺序探测（对齐 run_app.py 行为）；
K1 端口粘性（data/runtime.json）由 B04 在此之上替换为 ``resolve_port``。
"""
from __future__ import annotations

import json
import logging
import socket
import threading
from pathlib import Path
from typing import Any, Callable

import uvicorn

from desktop.logging_setup import build_uvicorn_log_config
from utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8000
MAX_PORT_PROBE = 20  # 8000 ~ 8019

# 上次成功端口落点。模块级可被测试替换（临时 runtime 文件），避免污染真实 data。
RUNTIME_PATH: Path = get_data_dir() / "runtime.json"


# ---------------------------------------------------------------------------
# K1 端口粘性
#
# localStorage 按 origin 分区且 origin 含端口（DESKTOP-UPGRADE.md §2.4 R2），
# 端口漂移会导致 QA session_id、问答历史缓存、主题设置静默丢失。
# 方案：上次成功端口写入 data/runtime.json；启动时优先复用，失败才顺序探测并写回；
#       runtime.json 写失败视为可容忍（降级为随机端口，不影响启动）。
# ---------------------------------------------------------------------------
def read_runtime_port() -> int | None:
    """读上次成功端口（data/runtime.json）。缺失/损坏返回 None。"""
    try:
        data = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
        port = int(data.get("port"))
        return port if 0 < port < 65536 else None
    except Exception:  # noqa: BLE001 - 缺失/损坏/非法一律视为无粘性
        return None


def write_runtime_port(port: int) -> bool:
    """把成功端口写回 data/runtime.json。失败返回 False（调用方容忍降级）。"""
    try:
        RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_PATH.write_text(json.dumps({"port": port}), encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 - 写失败降级为随机端口，不阻塞启动
        logger.warning("写入 runtime.json 失败（端口 %d），降级为无粘性", port)
        return False


def _port_free(port: int) -> bool:
    """端口当前是否可绑定（127.0.0.1）。绑定后立即释放。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port(start: int = DEFAULT_PORT) -> int:
    """从 start 开始探测空闲端口（绑定后立即释放，存在轻微竞态，可接受）。"""
    for port in range(start, start + MAX_PORT_PROBE):
        if _port_free(port):
            return port
    raise RuntimeError(f"端口 {start}~{start + MAX_PORT_PROBE - 1} 均被占用，请检查后重试")


def resolve_port(preferred: int | None = None) -> int:
    """解析本次启动端口（K1 粘性）。

    规则：
      1. 优先复用上次端口（preferred 未显式给出时从 runtime.json 读）；
      2. 上次端口空闲 → 直接复用（不写回，保持原值）；
      3. 上次端口被占 / 无记录 → 顺序探测并写回新端口；
      4. 写回失败 → 降级（不抛异常），本次用探测到的端口即可。

    Args:
        preferred: 显式指定的首选端口；None 表示读 runtime.json 记录。
    """
    if preferred is None:
        preferred = read_runtime_port()
    if preferred is not None and _port_free(preferred):
        return preferred
    start = preferred if preferred is not None else DEFAULT_PORT
    port = find_free_port(start=start)
    write_runtime_port(port)  # 写失败可容忍，忽略返回值
    return port


class ServerManager:
    """把后端 app 托管到 daemon 线程的 uvicorn 服务器，并带看门狗自动重启（B04）。

    用法：
        mgr = ServerManager(port=8000)
        mgr.start()                 # 起 daemon 线程 + 看门狗，线程内 import app + run()
        # ... 轮询 mgr.url + /api/v1/health 直到 200 ...
        mgr.stop()                  # should_exit=True -> join(timeout=10)
    """

    # 看门狗参数：探活间隔 5s，连续 3 次失败（约 15s）触发重建
    WATCHDOG_INTERVAL = 5.0
    WATCHDOG_MAX_FAILURES = 3

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
        app_import: str = "api.main:app",
        watchdog_enabled: bool = True,
        stop_scheduler: Callable[[], None] | None = None,
    ) -> None:
        self.host = host
        # K1：端口粘性——优先复用上次端口，失败才顺序探测并写回
        self.port = port if port is not None else resolve_port()
        self.app_import = app_import
        self.watchdog_enabled = watchdog_enabled
        # 看门狗重启前停调度的回调（默认从 api.main.app.state.scheduler 取实例 stop，
        # 也可由壳注入显式引用；None 则跳过显式 stop——B04.T3 幂等保护兜底）
        self._stop_scheduler = stop_scheduler
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._stop_watchdog = threading.Event()
        self._health_failures = 0
        self._ever_ready = False  # 是否首次 health 就绪过（冷启动期间不累计故障）

    # ---------- 只读属性 ----------
    @property
    def url(self) -> str:
        """后端根地址（前端加载 / health 轮询基准）。"""
        return f"http://{self.host}:{self.port}"

    @property
    def is_alive(self) -> bool:
        """后端线程是否仍在运行。"""
        return self._thread is not None and self._thread.is_alive()

    # ---------- 启动 ----------
    def start(self) -> None:
        """在 daemon 线程启动 uvicorn + 看门狗（不阻塞调用方）。

        app 通过 import 字符串交给 uvicorn 延迟加载（对齐 run_app.py），
        lifespan（TaskManager + scheduler）由 uvicorn 照常拉起。
        """
        if self.is_alive:
            logger.warning("后端已在运行，忽略重复 start()")
            return
        self._start_server_thread()
        if self.watchdog_enabled:
            self._start_watchdog()

    def _start_server_thread(self) -> None:
        """仅启动 server daemon 线程（看门狗重建时复用，不重复启动看门狗）。"""
        self._thread = threading.Thread(
            target=self._run_server,
            name="uvicorn-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("后端托管线程已启动（%s）", self.url)

    def _run_server(self) -> None:
        """线程目标：创建 Config + Server 并 run()。

        注意：``server.run()`` 内部自建 asyncio event loop，子线程安全；
        ``run()`` 返回后该实例不可复用（K2），重启需重建实例。
        """
        config = uvicorn.Config(
            self.app_import,
            host=self.host,
            port=self.port,
            log_level="info",
            reload=False,
            workers=1,
            # B05.T2（K3）：显式传 log_config，把 uvicorn* logger 指向与 root
            # 共享的同一文件 handler 且 propagate=False，避免默认 dictConfig
            # 覆盖 root、以及 uvicorn 日志与 app 日志重复。
            log_config=build_uvicorn_log_config(),
        )
        server = uvicorn.Server(config)
        self._server = server
        try:
            server.run()
        except Exception:  # noqa: BLE001 - 线程内异常兜底记录，避免静默
            logger.exception("uvicorn 线程异常退出")
        finally:
            logger.info("后端托管线程已退出")

    # ---------- 看门狗 ----------
    def _start_watchdog(self) -> None:
        """启动看门狗 daemon 线程（探活 + 崩溃自恢复）。"""
        self._stop_watchdog.clear()
        self._health_failures = 0
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="desktop-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()
        logger.info("看门狗已启动（每 %.0fs 探活，连续 %d 次失败重建）",
                    self.WATCHDOG_INTERVAL, self.WATCHDOG_MAX_FAILURES)

    def _watchdog_loop(self) -> None:
        """每 5s 探 /api/v1/health；连续 3 次失败 → 停调度 → 重建 server 实例。

        冷启动（首次 health 就绪前）不累计故障，避免后端 import 链耗时导致误重启；
        首次就绪后，若 health 连续失败或后端线程意外退出（崩溃），即触发重建。
        """
        while not self._stop_watchdog.is_set():
            if self._stop_watchdog.wait(self.WATCHDOG_INTERVAL):
                break  # 收到停止信号
            if self.is_alive and self._health_ok():
                self._ever_ready = True
                self._health_failures = 0
                continue
            # 不健康（或后端线程已退出）：
            if not self._ever_ready:
                continue  # 冷启动中，等待首次就绪
            self._health_failures += 1
            logger.warning(
                "后端不可用（%s），第 %d/%d 次",
                "线程已退出" if not self.is_alive else "health 非 200",
                self._health_failures, self.WATCHDOG_MAX_FAILURES,
            )
            if self._health_failures >= self.WATCHDOG_MAX_FAILURES:
                logger.warning("连续 %d 次后端不可用，重建后端线程",
                               self.WATCHDOG_MAX_FAILURES)
                self._stop_scheduler_if_any()
                self._restart()
                self._health_failures = 0

    def _health_ok(self) -> bool:
        """GET /api/v1/health 是否 200。"""
        import urllib.request

        try:
            with urllib.request.urlopen(f"{self.url}/api/v1/health", timeout=2) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001 - 探活失败即视为不健康
            return False

    def _stop_scheduler_if_any(self) -> None:
        """看门狗重启前停掉当前调度器（避免 lifespan 重跑时的可重入冲突）。

        优先用壳注入的回调；否则尝试从 api.main.app.state.scheduler 取实例 stop；
        都没有则跳过（B04.T3 的 start_scheduler 幂等保护会在 lifespan 重跑时兜底停旧实例）。
        """
        if self._stop_scheduler is not None:
            try:
                self._stop_scheduler()
                logger.info("看门狗已停止调度器（显式回调）")
            except Exception:  # noqa: BLE001
                logger.exception("看门狗停止调度器回调失败")
            return
        try:
            from api.main import app
            sch = app.state.scheduler
            if sch is not None:
                sch.stop()
                logger.info("看门狗已停止调度器（api.main.app.state.scheduler）")
        except Exception:  # noqa: BLE001 - 拿不到调度器不阻塞重启
            logger.debug("看门狗未能显式停止调度器（由幂等保护兜底）")

    def _restart(self) -> None:
        """停掉旧 server 线程后重建（K2：不可复用已返回的 Server 实例）。"""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._thread = None
        self._server = None
        self._start_server_thread()

    # ---------- 停止 ----------
    def stop(self, timeout: float = 10.0) -> None:
        """优雅退出：停看门狗 → should_exit=True → join(timeout)。

        should_exit 会让 uvicorn 在完成当前请求后关闭并执行 lifespan shutdown。
        """
        self._stop_watchdog.set()
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=5)
        self._watchdog_thread = None

        if self._server is not None:
            self._server.should_exit = True
            logger.info("已请求后端退出（should_exit=True）")
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("后端线程在 %.0fs 内未退出", timeout)
        self._thread = None
        self._server = None
