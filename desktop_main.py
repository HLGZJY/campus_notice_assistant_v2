"""桌面版打包入口（PyInstaller 打包的启动脚本，与 run_app.py 并存）。

用途（DESKTOP-UPGRADE.md §4.2 / B4）：
  - 作为 `--flavor desktop` 构建的 PyInstaller 入口脚本，产出桌面版 exe；
  - 与在线版入口 `run_app.py` 并存：桌面版加载 http://127.0.0.1:<port>
    于系统 WebView2 窗口内（pywebview），不打开外部浏览器；
    `run_app.py` 保留为在线版 / `--browser` 降级入口。

与开发入口 `python -m desktop`（desktop/__main__.py）的区别：
  - 本文件是独立脚本，可被 PyInstaller 直接打平，不依赖 `-m desktop` 的包调用；
  - 两者调用同一套 `desktop.app.DesktopApp`，生命周期与退出编排完全一致。

R1 对策（关闭控制台后 stdout 为 None 会「启动即退」）：
  - 不在脚本内做任何 `print()` / `logging.basicConfig()` 到 stdout 的调用；
  - 日志统一走 `desktop.logging_setup.setup_logging()`（RotatingFileHandler 落盘），
    并显式安装未捕获异常钩子（`install_excepthooks`）；
  - 由 `--flavor desktop` 的 spec `console=False` 关闭控制台窗口。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("desktop_main")

# 确保开发模式下 import api.* 可用（冻结模式下 PyInstaller 已打平）。
# 与 desktop/__main__.py 相同：在非冻结环境（python desktop_main.py）下把
# 仓库根插到 sys.path；冻结 onedir 下由 spec 的 pathex 与打平保证，此处无害。
if getattr(sys, "frozen", False):
    pass  # PyInstaller 已把依赖打平到 _internal/，无需手动插路径
else:
    _root = str(Path(__file__).resolve().parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)


def main() -> None:
    from desktop.app import DesktopApp, parse_cli_args
    from desktop.logging_setup import install_excepthooks, setup_logging

    # B13.T3：解析启动参数 --minimized/--autostart/--browser（及 --no-tray）。
    # 开机自启（HKCU Run，autostart.py）写入的命令带 --autostart，Windows 拉起
    # 本 exe 时经此标记静默到托盘。
    args = parse_cli_args()

    # B05：最早配置日志落盘（含 stdout 为 None 时的重定向），并装崩溃钩子。
    # 必须放在任何业务逻辑之前，否则 console=False 下 stdout 为 None 时
    # 早期 print / logging 可能直接崩溃（R1）。
    # console=False：桌面版日志统一落盘 data/logs/app.log，不依赖 stdout
    # 编码（中文 Windows 控制台 cp1252 写中文会 UnicodeEncodeError），
    # 即使 console=True 调试形态也只落盘、不挂 stdout。
    setup_logging(console=False)
    install_excepthooks()

    from utils.app_paths import get_version

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
