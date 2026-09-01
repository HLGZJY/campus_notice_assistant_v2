"""B04 端口粘性验收（离线，不依赖真实网络与 WebView）。

对应 docs/DESKTOP-ACCEPTANCE.md §4「自动化验收脚本清单与断言」：
  1. 首次启动分配端口并写入 data/runtime.json
  2. 二次启动复用同一端口
  3. 目标端口被占时自动切换到下一可用端口并写回
  4. runtime.json 不可写时降级为随机端口且启动不失败

设计依据见 docs/DESKTOP-UPGRADE.md §6 K1：
localStorage 按 origin 分区且 origin 含端口，端口漂移会导致 QA session_id、
问答历史缓存、主题设置静默丢失，因此端口必须「粘性」。

依赖：desktop.server（B03/B04 实现后自动激活；当前未实现则整脚本 SKIP）

用法：python test_desktop_port_sticky.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite, temp_db  # noqa: E402

suite = Suite(batch="B04", module="desktop.server", title="端口粘性（K1）")


@suite.case("1. 首次启动分配端口并写入 data/runtime.json")
def _(srv):
    resolve_port = suite.require(srv, "resolve_port")
    read_runtime_port = suite.require(srv, "read_runtime_port")
    suite.pending(
        "清理 runtime.json → resolve_port() 应返回 8000 量级端口，"
        "并把端口写回 data/runtime.json，read_runtime_port() 可读回"
    )


@suite.case("2. 二次启动复用同一端口")
def _(srv):
    resolve_port = suite.require(srv, "resolve_port")
    first = resolve_port()
    second = resolve_port()
    assert first == second, f"端口不粘：{first} != {second}"


@suite.case("3. 目标端口被占时自动切换到下一可用端口并写回")
def _(srv):
    resolve_port = suite.require(srv, "resolve_port")
    read_runtime_port = suite.require(srv, "read_runtime_port")
    suite.pending(
        "占用 runtime.json 中记录的端口后 resolve_port() 应返回其他可用端口，"
        "并把新端口写回 runtime.json"
    )


@suite.case("4. runtime.json 不可写时降级为随机端口且启动不失败")
def _(srv):
    resolve_port = suite.require(srv, "resolve_port")
    suite.pending(
        "把 data/runtime.json 设为只读或目录不可写，"
        "resolve_port() 应仍能返回一个可绑定端口，不抛异常"
    )


if __name__ == "__main__":
    sys.exit(suite.run())
