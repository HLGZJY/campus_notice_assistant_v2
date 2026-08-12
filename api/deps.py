"""依赖注入：鉴权占位。

本期为本地单用户，不做登录；预留统一鉴权依赖位，未来多用户/APP 登录时
替换为 JWT/OAuth 校验即可，业务路由零改动。
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Header, HTTPException, status

# 预留：可通过环境变量启用 API Key 校验（默认关闭）
_API_KEY_ENV = "CAMPUS_API_KEY"


def _api_key_enabled() -> bool:
    return bool(os.environ.get(_API_KEY_ENV))


def require_auth(x_api_key: Optional[str] = Header(default=None)) -> None:
    """统一鉴权依赖：未启用 API Key 时放行；启用后校验请求头。

    用法：router 或 endpoint 的 `dependencies=[Depends(require_auth)]`。
    """
    if not _api_key_enabled():
        return
    expected = os.environ.get(_API_KEY_ENV)
    if not expected or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key",
        )
