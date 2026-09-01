"""B08 退出编排与生命周期验收（离线）。

对应 docs/DESKTOP-ACCEPTANCE.md §4：
  1. 关闭窗口后进程存活且 health 200
  2. 托盘退出后进程消失、无子线程残留
  3. 退出序列顺序正确（几何 → server → scheduler → tray → 锁）
  4. 有 running 任务时退出被二次确认拦截

设计依据见 docs/DESKTOP-UPGRADE.md §6 K5：
window.closing → hide + return False；真退出按顺序释放资源；
注册 WM_QUERYENDSESSION 让关机/注销走同一路径，避免数据损坏。

依赖：desktop.app（B03 骨架 / B08 完整实现；当前未实现则整脚本 SKIP）

用法：python test_desktop_lifecycle.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B08", module="desktop.app", title="退出编排与生命周期（K5）")


@suite.case("1. 关闭窗口后进程存活且 health 200")
def _(app_mod):
    cls = suite.require(app_mod, "DesktopApp")
    suite.pending(
        "构造 DesktopApp（后端起在线程内）→ 触发 window.closing → "
        "断言窗口已 hide、进程仍存活、/api/v1/health 仍返回 200"
    )


@suite.case("2. 托盘退出后进程消失、无子线程残留")
def _(app_mod):
    cls = suite.require(app_mod, "DesktopApp")
    suite.pending(
        "调用 quit() → 断言 threading.enumerate() 仅剩主线程，"
        "uvicorn / pystray / 看门狗线程均已 join"
    )


@suite.case("3. 退出序列顺序正确")
def _(app_mod):
    cls = suite.require(app_mod, "DesktopApp")
    suite.pending(
        "用桩记录调用顺序，断言：写窗口几何 → server.should_exit=True → "
        "join → scheduler.stop() → pystray.stop() → 释放单实例锁 → sys.exit(0)"
    )


@suite.case("4. 有 running 任务时退出被二次确认拦截")
def _(app_mod):
    should_confirm = suite.require(app_mod, "has_running_tasks")
    suite.pending(
        "构造存在 running 任务的状态（TaskManager 内 running>0），"
        "quit() 应先弹二次确认；用户取消则不退出、进程存活"
    )


if __name__ == "__main__":
    sys.exit(suite.run())
