"""B11 桌面启动令牌验收（离线）。

对应 docs/DESKTOP-ACCEPTANCE.md §4：
  1. 无 X-Desktop-Token 头请求 /api/v1/* 返回 401
  2. 带正确令牌返回 200
  3. 开发模式（python -m desktop）关闭校验
  4. --browser 降级模式关闭校验
  5. 令牌每次启动不同（secrets.token_urlsafe(32)）

设计依据见 docs/DESKTOP-UPGRADE.md §6 K7：令牌经 webview 初始化脚本注入
window.__CNA_TOKEN__，前端 http client 统一加 X-Desktop-Token 头；
**开发模式与 --browser 降级模式必须关闭校验**，否则会影响 dev 流程与既有测试脚本。

依赖：api.desktop_token（B11 实现后自动激活；当前未实现则整脚本 SKIP）
      fastapi.testclient（需要 httpx，缺失则该 case SKIP）

用法：python test_desktop_token.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B11", module="api.desktop_token", title="启动令牌与校验中间件（K7）")


def _client():
    """构造带令牌中间件的 FastAPI 测试客户端；缺 httpx 时 SKIP。"""
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        raise NotImplementedError(f"缺少测试依赖（httpx/fastapi TestClient）：{exc}")

    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app, TestClient


@suite.case("1. 无 X-Desktop-Token 头请求 /api/v1/* 返回 401")
def _(tok):
    install = suite.require(tok, "install_token_middleware")
    app, TestClient = _client()
    install(app, token="T" * 43)
    with TestClient(app) as c:
        r = c.get("/api/v1/ping")
    assert r.status_code == 401, f"期望 401，实际 {r.status_code}"


@suite.case("2. 带正确令牌返回 200")
def _(tok):
    install = suite.require(tok, "install_token_middleware")
    app, TestClient = _client()
    install(app, token="T" * 43)
    with TestClient(app) as c:
        r = c.get("/api/v1/ping", headers={"X-Desktop-Token": "T" * 43})
    assert r.status_code == 200, f"期望 200，实际 {r.status_code}"


@suite.case("3. 开发模式（python -m desktop）关闭校验")
def _(tok):
    is_enabled = suite.require(tok, "token_check_enabled")
    suite.pending(
        "以开发模式（未注入令牌 / DESKTOP_TOKEN_CHECK=0）构造 app，"
        "is_enabled() 应为 False，无令牌请求 /api/v1/ping 返回 200"
    )


@suite.case("4. --browser 降级模式关闭校验")
def _(tok):
    is_enabled = suite.require(tok, "token_check_enabled")
    suite.pending(
        "以 --browser 模式构造 app，is_enabled() 应为 False，"
        "保证 webview 故障降级时浏览器仍可正常访问 API"
    )


@suite.case("5. 令牌每次启动不同")
def _(tok):
    generate = suite.require(tok, "generate_token")
    a, b = generate(), generate()
    assert a != b, "两次生成的令牌相同，存在重放风险"
    assert len(a) >= 32, f"令牌长度不足：{len(a)}"


if __name__ == "__main__":
    sys.exit(suite.run())
