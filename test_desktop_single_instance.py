"""B07 单实例锁与唤起验收（离线）。

对应 docs/DESKTOP-ACCEPTANCE.md §4：
  1. 二次启动不产生第二个后端（health 端口唯一）
  2. 第二实例进程退出
  3. 主实例收到 activate 并恢复窗口
  4. 锁端口被无关程序占用时走文件锁兜底且启动成功

设计依据见 docs/DESKTOP-UPGRADE.md §6 K6：双开会写坏 SQLite（R7），
首启即加锁；锁通道同时承担「唤起已有实例」职责。

依赖：desktop.single_instance（B07 实现后自动激活；当前未实现则整脚本 SKIP）

用法：python test_desktop_single_instance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B07", module="desktop.single_instance", title="单实例锁与唤起（K6）")


@suite.case("1. 二次启动不产生第二个后端")
def _(si):
    lock_cls = suite.require(si, "SingleInstanceLock")
    suite.pending(
        "实例化两个 SingleInstanceLock（模拟双开），"
        "第二个 acquire() 应返回 False（已有实例），且不再起第二个 uvicorn"
    )


@suite.case("2. 第二实例进程退出")
def _(si):
    lock_cls = suite.require(si, "SingleInstanceLock")
    suite.pending(
        "第二实例检测到已有实例后应发送 activate 指令并立即退出（退出码 0，不残留进程）"
    )


@suite.case("3. 主实例收到 activate 并恢复窗口")
def _(si):
    lock_cls = suite.require(si, "SingleInstanceLock")
    send_activate = suite.require(si, "send_activate")
    suite.pending(
        "向锁端口发送 activate → 主实例的回调被触发（restore + show + focus），"
        "测试内用桩回调断言被调用一次"
    )


@suite.case("4. 锁端口被无关程序占用时走文件锁兜底且启动成功")
def _(si):
    lock_cls = suite.require(si, "SingleInstanceLock")
    suite.pending(
        "先占用固定高位端口（如 51999），再 acquire()："
        "应回退到 named mutex / 文件锁（Windows 侧 msvcrt.locking）"
        "并成功加锁，同时记录日志"
    )


if __name__ == "__main__":
    sys.exit(suite.run())
