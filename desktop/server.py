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

import logging
import socket
import threading
from typing import Any

import uvicorn

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8000
MAX_PORT_PROBE = 20  # 8000 ~ 8019


def find_free_port(start: int = DEFAULT_PORT) -> int:
    """从 start 开始探测空闲端口（绑定后立即释放，存在轻微竞态，可接受）。

    与 run_app.py 行为一致；B04 将升级为「优先复用上次端口」的粘性探测。
    """
    for port in range(start, start + MAX_PORT_PROBE):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"端口 {start}~{start + MAX_PORT_PROBE - 1} 均被占用，请检查后重试")


class ServerManager:
    """把后端 app 托管到 daemon 线程的 uvicorn 服务器。

    用法：
        mgr = ServerManager(port=8000)
        mgr.start()                 # 起 daemon 线程，线程内 import app + run()
        # ... 轮询 mgr.url + /api/v1/health 直到 200 ...
        mgr.stop()                  # should_exit=True -> join(timeout=10)
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
        app_import: str = "api.main:app",
    ) -> None:
        self.host = host
        self.port = port if port is not None else find_free_port()
        self.app_import = app_import
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

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
        """在 daemon 线程启动 uvicorn（不阻塞调用方）。

        app 通过 import 字符串交给 uvicorn 延迟加载（对齐 run_app.py），
        lifespan（TaskManager + scheduler）由 uvicorn 照常拉起。
        """
        if self.is_alive:
            logger.warning("后端已在运行，忽略重复 start()")
            return
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
        ``run()`` 返回后该实例不可复用（K2），重启需重建实例（B04）。
        """
        config = uvicorn.Config(
            self.app_import,
            host=self.host,
            port=self.port,
            log_level="info",
            reload=False,
            workers=1,
        )
        server = uvicorn.Server(config)
        self._server = server
        try:
            server.run()
        except Exception:  # noqa: BLE001 - 线程内异常兜底记录，避免静默
            logger.exception("uvicorn 线程异常退出")
        finally:
            logger.info("后端托管线程已退出")

    # ---------- 停止 ----------
    def stop(self, timeout: float = 10.0) -> None:
        """优雅退出：should_exit=True -> join(timeout)。

        should_exit 会让 uvicorn 在完成当前请求后关闭并执行 lifespan shutdown。
        """
        if self._server is not None:
            self._server.should_exit = True
            logger.info("已请求后端退出（should_exit=True）")
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("后端线程在 %.0fs 内未退出", timeout)
        self._thread = None
        self._server = None
