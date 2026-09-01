"""B18 更新闭环端到端验收（离线，本地 mock HTTP 服务）。

对应 docs/DESKTOP-ACCEPTANCE.md §4 与 docs/DESKTOP-BATCH-PLAN.md §B18：
  1. latest.json 清单解析（版本 / assets / sha256）
  2. 选择桌面版安装包 asset
  3. 下载 → sha256 校验通过 → 进入确认安装（D-44 前半链路）
  4. sha256 校验失败 → 中止并提示（D-45）
  5. perform_update 全流程：确认 → 启动安装器 → 退出当前实例（桩注入）

设计要点：
  - 用本地 ``http.server`` 起一个临时 HTTP 服务，Serve 一个临时目录（内含
    latest.json 与一个真实存在的安装包文件），让 download_asset / fetch_manifest
    在**离线**环境下也能走通真实网络栈。
  - 确认 / 启动安装器 / 退出当前实例全部注入桩，不真的弹窗或退出。
  - 安装包下载目录用 tempfile，绝不触碰 data/notices.db 或真实数据。

依赖：desktop.updater（B18 实现后自动激活；未实现则整脚本 SKIP）

用法：python test_desktop_updater.py
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

# 运行依赖守卫：updater 的下载/清单拉取依赖 requests。缺 requests 的环境
# （如回归基线用的系统 Python）下整脚本 SKIP，不判 FAIL（与既有骨架语义一致）。
try:
    import requests  # noqa: F401
except Exception:  # noqa: BLE001 - 无 requests 时整脚本跳过
    print("== B18 更新闭环（B12 下载+校验+确认安装） ==")
    print("   [SKIP] 依赖 requests 不可用（本环境未安装），整脚本跳过")
    print("结果: 全部跳过（依赖 requests）")
    sys.exit(0)

suite = Suite(batch="B18", module="desktop.updater", title="更新闭环（B12 下载+校验+确认安装）")


# ---------------------------------------------------------------------------
# 本地 mock HTTP 服务器：Serve 一个临时目录（latest.json + 安装包）
# ---------------------------------------------------------------------------
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_: object) -> None:  # 静默，避免刷屏
        pass


@suite.case("1. latest.json 清单解析（version/assets/sha256）")
def _(up):
    parse = suite.require(up, "parse_manifest")
    text = json.dumps(
        {
            "version": "0.2.1",
            "notes": "修复若干问题",
            "assets": [
                {"name": "校园通知助手-桌面版-setup.exe", "url": "https://x/install.exe",
                 "sha256": "AB" * 32},
            ],
        }
    )
    m = parse(text)
    assert m["version"] == "0.2.1", m
    assert m["notes"] == "修复若干问题", m
    assert len(m["assets"]) == 1, m
    a = m["assets"][0]
    assert a["name"] == "校园通知助手-桌面版-setup.exe", a
    assert a["sha256"] == ("ab" * 32), a  # 归一化为小写


@suite.case("2. 从 assets 里挑选桌面版安装包")
def _(up):
    select = suite.require(up, "select_desktop_asset")
    assets = [
        {"name": "校园通知助手-在线版-setup.exe", "url": "u1", "sha256": "s1"},
        {"name": "校园通知助手-桌面版-setup.exe", "url": "u2", "sha256": "s2"},
        {"name": "README.md", "url": "u3", "sha256": ""},
    ]
    chosen = select(assets)
    assert chosen is not None and chosen["name"] == "校园通知助手-桌面版-setup.exe", chosen


@suite.case("3. 下载→sha256 校验通过→进入确认安装（D-44 前半链路）")
def _(up):
    perform = suite.require(up, "perform_update")
    sha256_of = suite.require(up, "sha256_of")

    with _mock_server() as (base, work):
        # 写一个真实文件作为「安装包」，算好它的 sha256 写进 latest.json
        setup = work / "校园通知助手-桌面版-setup.exe"
        setup.write_bytes(b"MZ" + b"\x00" * 100)  # 任意二进制内容
        manifest = {
            "version": "0.2.1",
            "notes": "端到端测试",
            "assets": [
                {"name": setup.name, "url": f"{base}/{setup.name}",
                 "sha256": sha256_of(setup)},
            ],
        }
        (work / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")

        calls = {"confirm": 0, "launch": 0, "quit": 0}
        dl_dir = Path(tempfile.mkdtemp())
        try:
            result = perform(
                repo="fake/owner",
                download_prefix=base,  # 让下载 URL 走本地服务
                download_dir=dl_dir,
                timeout=10,
                fetch_manifest_fn=lambda r, p, t: (work / "latest.json").read_text(encoding="utf-8"),
                confirm_fn=lambda m, p: (calls.__setitem__("confirm", calls["confirm"] + 1), True)[1],
                launch_fn=lambda p: calls.__setitem__("launch", calls["launch"] + 1),
                quit_fn=lambda: calls.__setitem__("quit", calls["quit"] + 1),
            )
            assert result["status"] == "ok", result
            assert calls["confirm"] == 1, calls
            assert calls["launch"] == 1, calls
            assert calls["quit"] == 1, calls
            # 下载到目标目录且存在
            dl = dl_dir / setup.name
            assert dl.exists() and dl.stat().st_size == setup.stat().st_size, dl
        finally:
            shutil.rmtree(dl_dir, ignore_errors=True)


@suite.case("4. sha256 校验失败→中止并提示（D-45）")
def _(up):
    perform = suite.require(up, "perform_update")

    with _mock_server() as (base, work):
        setup = work / "install.exe"
        setup.write_bytes(b"WRONG-CONTENT")
        manifest = {
            "version": "0.2.1",
            "assets": [
                {"name": setup.name, "url": f"{base}/{setup.name}",
                 "sha256": "0" * 64},  # 期望值与实际不符
            ],
        }
        (work / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")

        calls = {"confirm": 0, "launch": 0, "quit": 0}
        dl_dir = Path(tempfile.mkdtemp())
        try:
            result = perform(
                repo="fake/owner", download_prefix=base, download_dir=dl_dir, timeout=10,
                fetch_manifest_fn=lambda r, p, t: (work / "latest.json").read_text(encoding="utf-8"),
                confirm_fn=lambda m, p: (calls.__setitem__("confirm", 1), True)[1],
                launch_fn=lambda p: calls.__setitem__("launch", 1),
                quit_fn=lambda: calls.__setitem__("quit", 1),
            )
            assert result["status"] == "error", result
            assert "sha256" in result["message"] or "校验" in result["message"], result
            # 校验失败不得进入确认 / 安装 / 退出
            assert calls["confirm"] == 0, calls
            assert calls["launch"] == 0, calls
            assert calls["quit"] == 0, calls
        finally:
            shutil.rmtree(dl_dir, ignore_errors=True)


@suite.case("5. 用户取消确认→中止（不启动安装器、不退出）")
def _(up):
    perform = suite.require(up, "perform_update")
    sha256_of = suite.require(up, "sha256_of")

    with _mock_server() as (base, work):
        setup = work / "cancel.exe"
        setup.write_bytes(b"BINARY")
        manifest = {
            "version": "0.2.2",
            "assets": [
                {"name": setup.name, "url": f"{base}/{setup.name}", "sha256": sha256_of(setup)},
            ],
        }
        (work / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")

        calls = {"launch": 0, "quit": 0}
        dl_dir = Path(tempfile.mkdtemp())
        try:
            result = perform(
                repo="fake/owner", download_prefix=base, download_dir=dl_dir, timeout=10,
                fetch_manifest_fn=lambda r, p, t: (work / "latest.json").read_text(encoding="utf-8"),
                confirm_fn=lambda m, p: False,  # 用户取消
                launch_fn=lambda p: calls.__setitem__("launch", 1),
                quit_fn=lambda: calls.__setitem__("quit", 1),
            )
            assert result["status"] == "cancelled", result
            assert calls["launch"] == 0 and calls["quit"] == 0, calls
        finally:
            shutil.rmtree(dl_dir, ignore_errors=True)


@suite.case("6. 未配置 repo→更新禁用（不抛异常）")
def _(up):
    perform = suite.require(up, "perform_update")
    check = suite.require(up, "check_repo_configured")
    assert check("") is False, "空 repo 应视为未配置"
    result = perform(repo="", download_dir=Path(tempfile.mkdtemp()))
    assert result["status"] == "disabled", result


# ---------------------------------------------------------------------------
# mock 服务器辅助
# ---------------------------------------------------------------------------
@__import__("contextlib").contextmanager
def _mock_server():
    """起一个临时 HTTP 服务，Serve 临时目录。yield (base_url, work_dir)。"""
    work = Path(tempfile.mkdtemp())
    handler = lambda *a, **kw: _QuietHandler(*a, directory=str(work), **kw)  # noqa: E731
    # 随机空闲端口
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}", work
        finally:
            httpd.shutdown()
            httpd.server_close()
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(suite.run())
