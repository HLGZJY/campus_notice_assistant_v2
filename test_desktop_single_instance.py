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

import contextlib
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B07", module="desktop.single_instance", title="单实例锁与唤起（K6）")


# ---------------------------------------------------------------------------
# 辅助：端口/锁文件隔离（绝不触碰真实 51999 与真实 data 锁文件）
# ---------------------------------------------------------------------------
def _find_free_port(start: int = 52000, end: int = 52500) -> int:
    """取一个空闲高位端口（用于测试隔离，避免占用真实 51999）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        for port in range(start, end):
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("无可用测试端口")


def _temp_lock_path() -> Path:
    """生成一个 data/ 下未使用的临时锁文件路径（测试隔离）。"""
    return Path(__file__).parent / "data" / f"_tmp_lock_{uuid.uuid4().hex}.lock"


def _occupy(port: int) -> socket.socket:
    """占用一个端口（模拟「无关程序」独占），返回 socket（调用方负责 close）。

    刻意**不设 SO_REUSEADDR**：真实「无关程序」通常独占端口，后到的 bind 必然
    失败，这样才能触发单实例锁的文件锁兜底路径。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


class _SpyCallback:
    """记录被调用次数的唤起回调（用于断言主实例收到 activate）。"""

    def __init__(self) -> None:
        self.count = 0
        self._event = threading.Event()

    def __call__(self) -> None:
        self.count += 1
        self._event.set()

    def wait(self, timeout: float = 2.0) -> bool:
        return self._event.wait(timeout)


@suite.case("1. 二次启动不产生第二个后端")
def _(si):
    SingleInstanceLock = suite.require(si, "SingleInstanceLock")
    port = _find_free_port()
    lock_path = _temp_lock_path()

    lock1 = SingleInstanceLock(port=port, lock_path=lock_path)
    lock2 = SingleInstanceLock(port=port, lock_path=lock_path)
    try:
        assert lock1.acquire() is True, "首个实例应取得单实例锁（成为主实例）"
        assert lock1.acquired, "lock1 应持有锁"
        # 第二个实例：同一端口已被 lock1 监听 → acquire 应返回 False（已有实例），
        # 即不会在本实例上再起第二个后端
        assert lock2.acquire() is False, "第二个实例应判定已有实例，不双开"
        assert not lock2.acquired, "lock2 不应取得锁"
        # 主实例仍持有锁（未被第二实例抢占）
        assert lock1.acquired, "主实例锁不应被第二实例释放"
    finally:
        lock2.release()
        lock1.release()
        _cleanup(lock_path)


@suite.case("2. 第二实例进程退出")
def _(si):
    SingleInstanceLock = suite.require(si, "SingleInstanceLock")
    port = _find_free_port()
    lock_path = _temp_lock_path()

    lock1 = SingleInstanceLock(port=port, lock_path=lock_path)
    lock2 = SingleInstanceLock(port=port, lock_path=lock_path)
    try:
        assert lock1.acquire() is True
        # 第二实例检测到已有实例 → 应返回 False（调用方据此 sys.exit(0)，退出码 0），
        # 且不残留任何监听 socket / 文件锁句柄
        assert lock2.acquire() is False
        assert lock2._socket is None, "第二实例不应持有监听 socket"
        assert lock2.using_file_lock is False, "第二实例不应占用文件锁"
    finally:
        lock2.release()
        lock1.release()
        _cleanup(lock_path)


@suite.case("3. 主实例收到 activate 并恢复窗口")
def _(si):
    SingleInstanceLock = suite.require(si, "SingleInstanceLock")
    send_activate = suite.require(si, "send_activate")
    port = _find_free_port()
    lock_path = _temp_lock_path()

    spy = _SpyCallback()
    lock1 = SingleInstanceLock(port=port, lock_path=lock_path)
    try:
        assert lock1.acquire(on_activate=spy) is True
        # 向锁端口发送 activate → 主实例监听线程触发唤起回调（restore+show+focus 桩）
        ok = send_activate(port)
        assert ok, "向主实例发送 activate 应得到 ACK（唤起指令可达）"
        assert spy.wait(2.0), "主实例应收到 activate 并触发唤起回调"
        assert spy.count == 1, f"唤起回调应恰好触发一次，实际 {spy.count}"
    finally:
        lock1.release()
        _cleanup(lock_path)


@suite.case("4. 锁端口被无关程序占用时走文件锁兜底且启动成功")
def _(si):
    SingleInstanceLock = suite.require(si, "SingleInstanceLock")
    port = _find_free_port()
    lock_path = _temp_lock_path()

    occ = _occupy(port)  # 用无关 socket 占住锁端口
    try:
        lock1 = SingleInstanceLock(port=port, lock_path=lock_path)
        try:
            # 端口被无关程序占用：socket 锁拿不到、对端不响应协议
            # → 应回退到文件锁（Windows msvcrt.locking）并成功加锁
            assert lock1.acquire() is True, "端口被占时应走文件锁兜底且启动成功"
            assert lock1.using_file_lock, "应通过文件锁兜底取得主实例"
            assert not lock1.using_socket, "不应通过 socket 锁取得主实例"
        finally:
            lock1.release()
    finally:
        occ.close()
        _cleanup(lock_path)


def _cleanup(lock_path: Path) -> None:
    """删除临时锁文件（忽略失败）。"""
    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(suite.run())
