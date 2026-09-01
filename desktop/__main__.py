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
    from desktop.app import DesktopApp, parse_cli_args
    from desktop.logging_setup import install_excepthooks, setup_logging

    # B13.T3：解析启动参数 --minimized/--autostart/--browser（及 --no-tray）
    args = parse_cli_args()

    # B05：最早配置日志落盘（含 stdout 为 None 时的重定向），并装崩溃钩子
    # console=False：日志统一落盘 data/logs/app.log，不挂 stdout（避免中文
    # 控制台 cp1252 编码写中文日志报错），与 desktop_main.py 打包入口一致。
    setup_logging(console=False)
    install_excepthooks()

    logger.info("校园通知智能助手 桌面版 v%s 启动中……", get_version())
    app = DesktopApp(
        enable_tray=not args.no_tray,
        start_minimized=args.minimized,
        autostart=args.autostart,
        browser_mode=args.browser,
    )
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在关闭……")
        sys.exit(0)


if __name__ == "__main__":
    main()
