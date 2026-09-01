"""B04 端口粘性验收（离线，不依赖真实网络与 WebView）。

对应 docs/DESKTOP-ACCEPTANCE.md §4「自动化验收脚本清单与断言」：
  1. 首次启动分配端口并写入 data/runtime.json
  2. 二次启动复用同一端口
  3. 目标端口被占时自动切换到下一可用端口并写回
  4. runtime.json 不可写时降级为随机端口且启动不失败

设计依据见 docs/DESKTOP-UPGRADE.md §6 K1：
localStorage 按 origin 分区且 origin 含端口，端口漂移会导致 QA session_id、
问答历史缓存、主题设置静默丢失，因此端口必须「粘性」。

依赖：desktop.server（B04 实现后自动激活；未实现则整脚本 SKIP）

用法：python test_desktop_port_sticky.py
"""
from __future__ import annotations

import contextlib
import socket
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B04", module="desktop.server", title="端口粘性（K1）")


@contextlib.contextmanager
def temp_runtime(srv):
    """把 desktop.server.RUNTIME_PATH 临时指向 data/_tmp_*.json，退出即删除。

    绝不触碰真实 data/runtime.json（与 testkit 的 temp_db 同款隔离约定）。
    """
    orig = srv.RUNTIME_PATH
    tmp = Path(__file__).parent / "data" / f"_tmp_runtime_{uuid.uuid4().hex}.json"
    srv.RUNTIME_PATH = tmp
    try:
        yield tmp
    finally:
        srv.RUNTIME_PATH = orig
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _occupy(port: int) -> socket.socket:
    """占用一个端口，返回 socket（调用方负责 close 释放）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


@suite.case("1. 首次启动分配端口并写入 data/runtime.json")
def _(srv):
    resolve_port = suite.require(srv, "resolve_port")
    read_runtime_port = suite.require(srv, "read_runtime_port")
    with temp_runtime(srv):
        port = resolve_port()
        # 首次从 8000 量级起探测，且写回 runtime.json
        assert 8000 <= port <= 8019, f"端口应落在 8000~8019，实际 {port}"
        assert read_runtime_port() == port, f"runtime.json 应记录端口 {port}"
        assert srv.RUNTIME_PATH.exists(), "runtime.json 应已写入"


@suite.case("2. 二次启动复用同一端口")
def _(srv):
    resolve_port = suite.require(srv, "resolve_port")
    with temp_runtime(srv):
        first = resolve_port()
        second = resolve_port()
        assert first == second, f"端口不粘：{first} != {second}"


@suite.case("3. 目标端口被占时自动切换到下一可用端口并写回")
def _(srv):
    resolve_port = suite.require(srv, "resolve_port")
    read_runtime_port = suite.require(srv, "read_runtime_port")
    with temp_runtime(srv):
        first = resolve_port()
        occ = _occupy(first)  # 占用 runtime 记录的端口
        try:
            second = resolve_port()
            assert second != first, f"端口被占后应切换，实际仍为 {second}"
            assert second > first, f"应从被占端口向上探测，实际 {second}"
            # 新端口写回 runtime.json
            assert read_runtime_port() == second, f"应写回新端口 {second}"
        finally:
            occ.close()


@suite.case("4. runtime.json 不可写时降级为随机端口且启动不失败")
def _(srv):
    resolve_port = suite.require(srv, "resolve_port")
    _port_free = suite.require(srv, "_port_free")
    with temp_runtime(srv):
        # 把 runtime 路径指向一个「目录」，write_text 会抛 OSError → 降级
        srv.RUNTIME_PATH = srv.RUNTIME_PATH.parent / f"_tmp_dir_{uuid.uuid4().hex}"
        srv.RUNTIME_PATH.mkdir(parents=True, exist_ok=True)
        try:
            port = resolve_port()  # 不抛异常
            assert _port_free(port), f"返回端口 {port} 应可绑定"
        finally:
            # 清理临时目录
            import shutil
            shutil.rmtree(srv.RUNTIME_PATH, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(suite.run())
