"""打包版应用入口（PyInstaller 打包的启动脚本）。

职责（PACKAGING.md 实施步骤 3 冒烟测试对应的入口行为）：
  1. 从 8000 起找一个空闲端口（避免同学机器上端口被占）
  2. 以编程方式启动 uvicorn（api.main:app）
  3. 就绪后自动打开默认浏览器
  4. Ctrl+C / 控制台关闭时优雅退出（lifespan 负责停调度器与任务管理器）

开发模式下同样可用：python run_app.py

说明：保留控制台窗口（PyInstaller console=True）。本地 Web 应用出问题时
控制台日志是唯一的排障入口，对早期分发给同学使用的版本利大于弊。
"""
from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# 确保 import api.* 在开发模式下可用（冻结模式下 PyInstaller 已打平）
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("run_app")

DEFAULT_PORT = 8000
MAX_PORT_PROBE = 20  # 8000 ~ 8019


def find_free_port(start: int = DEFAULT_PORT) -> int:
    """从 start 开始探测空闲端口（绑定后立即释放，存在轻微竞态，可接受）。"""
    for port in range(start, start + MAX_PORT_PROBE):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"端口 {start}~{start + MAX_PORT_PROBE - 1} 均被占用，请检查后重试")


def open_browser_when_ready(url: str, timeout: float = 30.0) -> None:
    """轮询健康端点，服务就绪后打开浏览器；超时也打开（让用户看到错误页）。"""
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/v1/health", timeout=2):
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    webbrowser.open(url)


def main() -> None:
    import uvicorn

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    logger.info("校园通知智能助手启动中…… 浏览器未自动打开请访问 %s", url)

    # 就绪后开浏览器（独立线程，不阻塞 uvicorn 启动）
    threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()

    try:
        uvicorn.run(
            "api.main:app",
            host="127.0.0.1",
            port=port,
            log_level="info",
            # 打包版禁用 reload/workers：单进程是调度器与 SQLite 单写者模型的前提
            reload=False,
            workers=1,
        )
    except KeyboardInterrupt:
        logger.info("收到退出信号，正在关闭……")


if __name__ == "__main__":
    main()
