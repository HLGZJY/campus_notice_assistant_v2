"""B05 日志落盘与崩溃兜底验收（离线）。

对应 docs/DESKTOP-ACCEPTANCE.md §4：
  1. sys.stdout = None 时启动流程不抛异常
  2. root logger 写入 data/logs/app.log
  3. uvicorn logger 不覆盖 root（propagate=False 生效）
  4. 文件轮转在 5MB 边界生效，保留 3 份
  5. 未捕获异常经 excepthook 落盘完整 traceback

设计依据见 docs/DESKTOP-UPGRADE.md §6 K3：
console=False 时 sys.stdout 为 None，任何残留 print()/basicConfig() 到 stdout
都会导致「启动即退」——这是关闭控制台后最高频的崩溃原因（R1）。

依赖：desktop.logging_setup（B05 实现后自动激活；当前未实现则整脚本 SKIP）

用法：python test_desktop_logging.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B05", module="desktop.logging_setup", title="日志落盘与崩溃兜底（K3）")


@suite.case("1. sys.stdout = None 时启动流程不抛异常")
def _(ls):
    setup_logging = suite.require(ls, "setup_logging")
    suite.pending(
        "把 sys.stdout / sys.stderr 置为 None 后调用 setup_logging()，"
        "应正常返回；随后 root logger 的 handlers 中不含 StreamHandler"
    )


@suite.case("2. root logger 写入 data/logs/app.log")
def _(ls):
    setup_logging = suite.require(ls, "setup_logging")
    log_path = suite.require(ls, "LOG_PATH")
    suite.pending(
        "setup_logging() 后经 logging.getLogger().info() 写入一条，"
        "断言 log_path 文件出现且内容包含该条"
    )


@suite.case("3. uvicorn logger 不覆盖 root（propagate=False 生效）")
def _(ls):
    build_uvicorn_log_config = suite.require(ls, "build_uvicorn_log_config")
    suite.pending(
        "build_uvicorn_log_config() 产出的 dictConfig 应用到 logging 后，"
        "uvicorn / uvicorn.access 的 propagate 为 False，且日志只落一份（不重复）"
    )


@suite.case("4. 文件轮转在 5MB 边界生效，保留 3 份")
def _(ls):
    setup_logging = suite.require(ls, "setup_logging")
    suite.pending(
        "连续写入超过 5MB 日志，断言出现 app.log.1 / .2 / .3 且 .4 不存在"
        "（maxBytes=5MB, backupCount=3，与 scheduler.py:101 参数一致）"
    )


@suite.case("5. 未捕获异常经 excepthook 落盘完整 traceback")
def _(ls):
    install_excepthooks = suite.require(ls, "install_excepthooks")
    suite.pending(
        "install_excepthooks() 后触发一次未捕获异常，"
        "断言 data/logs/app.log 内含 'Traceback' 与异常类型名"
    )


if __name__ == "__main__":
    sys.exit(suite.run())
