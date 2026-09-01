"""桌面启动令牌（K7）与 API 校验中间件。

对应 DESKTOP-UPGRADE.md §6 K7：
  - 每次启动 ``secrets.token_urlsafe(32)`` 生成令牌；
  - 经 webview 初始化脚本注入前端 ``window.__CNA_TOKEN__``，
    前端 http client 统一加 ``X-Desktop-Token`` 头；
  - 后端中间件仅对 ``/api/v1/*`` 且本机来源生效；
  - **开发模式（python -m desktop）与 --browser 降级模式关闭校验**，
    避免影响 dev 流程与既有测试脚本。

对外接口（与 tools/_desktop_testkit / test_desktop_token.py 契约一致）：
  - ``generate_token()``           生成新令牌（每次不同，≥32 字符）
  - ``install_token_middleware(app, token=None, enabled=None)``  安装中间件
  - ``token_check_enabled()``      当前是否应启用令牌校验
  - ``init_token()``               壳启动时生成令牌并写入模块态 + 环境变量

模式判定（token_check_enabled）：
  - ``CNA_BROWSER_MODE`` 被置位（--browser 降级）→ False；
  - 否则读 ``DESKTOP_TOKEN_CHECK``：显式 "0"/"false" → False（开发模式），
    缺省 / 其它值 → True（桌面模式默认开启）。
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# 请求头名称（K7：前端 http client 统一携带）
TOKEN_HEADER = "X-Desktop-Token"
# 环境变量（壳启动时设置）
_ENV_TOKEN = "CNA_DESKTOP_TOKEN"
_ENV_CHECK = "DESKTOP_TOKEN_CHECK"
_ENV_BROWSER = "CNA_BROWSER_MODE"

# 本机来源白名单：uvicorn 绑 127.0.0.1，TestClient 来源为 "testclient"。
# 命中才做令牌校验（防御远程访问）。
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

# 模块级当前令牌（壳注入 / 测试注入用）
_current_token: str = ""


def generate_token() -> str:
    """生成新的启动令牌（secrets.token_urlsafe(32)，每次不同，≥32 字符）。"""
    return secrets.token_urlsafe(32)


def token_check_enabled() -> bool:
    """当前是否应启用令牌校验（开发模式 / --browser 降级时关闭）。"""
    if os.environ.get(_ENV_BROWSER):
        return False  # --browser 降级：浏览器访问不应被令牌拦截
    val = os.environ.get(_ENV_CHECK)
    if val is None:
        return True  # 桌面模式默认开启
    return val.strip().lower() not in ("0", "false", "no", "")


def init_token() -> str:
    """壳启动时调用：生成令牌并写入模块态 + 环境变量，返回令牌。

    后端 ``create_app`` 经 ``CNA_DESKTOP_TOKEN`` 读到同一令牌来安装中间件；
    前端经 webview 注入 ``window.__CNA_TOKEN__`` 读到。
    """
    global _current_token
    token = generate_token()
    _current_token = token
    os.environ[_ENV_TOKEN] = token
    return token


def get_token() -> str:
    """返回当前令牌（壳 / 前端注入用）。"""
    return _current_token or os.environ.get(_ENV_TOKEN, "")


def install_token_middleware(
    app: object,
    token: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> None:
    """给 FastAPI app 安装桌面令牌校验中间件（K7）。

    Args:
        app: FastAPI 应用实例。
        token: 期望的令牌；None 时用 ``get_token()``（环境变量优先）。
        enabled: 是否启用校验；None 时按 ``token_check_enabled()`` 决定。

    中间件逻辑：仅对 ``/api/v1/*`` 路径、本机来源、且校验启用时，检查
    ``X-Desktop-Token`` 头是否等于期望令牌；不符返回 401。
    """
    if token is None:
        token = get_token()
    if enabled is None:
        enabled = token_check_enabled()
    if not token:
        logger.warning("未提供桌面令牌，令牌校验中间件将放行所有请求")
        return

    # FastAPI 2.0 兼容：app 可能无 add_middleware（直接传 app 对象），
    # 这里用 BaseHTTPMiddleware 风格的自定义纯 ASGI 中间件，避免依赖。
    token_ = token

    @app.middleware("http")
    async def _desktop_token_check(request: Request, call_next):  # type: ignore[misc]
        if enabled and request.url.path.startswith("/api/v1/"):
            host = (request.client.host if request.client else "") or ""
            if host in _LOCAL_HOSTS:
                if request.headers.get(TOKEN_HEADER) != token_:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "无效的桌面令牌"},
                    )
        return await call_next(request)
