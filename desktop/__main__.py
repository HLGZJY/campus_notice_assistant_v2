"""桌面壳开发入口：python -m desktop

职责：以最少编排把后端托管到线程并弹出原生窗口，供开发联调。
与 run_app.py 的区别：本入口加载 http://127.0.0.1:<port> 于 pywebview 窗口内，
而非打开系统浏览器；run_app.py 保留为在线版 / --browser 降级入口。

日志：B05 起改由 logging_setup 落盘（console=False 兼容），不再 basicConfig
到 stdout（R1：stdout 为 None 时会「启动即退」）。
"""
from __future__ import annotations

import logging
import sys

from utils.app_paths import get_version

logger = logging.getLogger("desktop")

# 确保开发模式下 import api.* 可用（冻结模式下 PyInstaller 已打平）
from pathlib import Path  # noqa: E402

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    from desktop.app import DesktopApp
    from desktop.logging_setup import install_excepthooks, setup_logging

    # B05：最早配置日志落盘（含 stdout 为 None 时的重定向），并装崩溃钩子
    setup_logging()
    install_excepthooks()

    logger.info("校园通知智能助手 桌面版 v%s 启动中……", get_version())
    app = DesktopApp()
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在关闭……")
        sys.exit(0)


if __name__ == "__main__":
    main()
