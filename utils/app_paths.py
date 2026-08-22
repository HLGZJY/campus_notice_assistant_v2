"""冻结感知的应用根目录解析（打包支持）。

PyInstaller onedir 模式下，模块的 ``__file__`` 指向 ``_internal/`` 内部目录，
而用户数据（data/）、可写配置（config/）、前端产物（frontend/dist/）都应位于
exe 同级目录。所有运行时路径解析统一走本模块：

- 开发模式：项目根 = ``utils/app_paths.py`` 的上上级目录
- 冻结模式：项目根 = exe 所在目录（``sys.executable`` 的父目录）

新增运行时路径时一律从这里取，不要再写 ``Path(__file__).parent``。
"""
from __future__ import annotations

import sys
from pathlib import Path


def get_app_root() -> Path:
    """返回应用根目录（开发 = 仓库根；冻结 = exe 所在目录）。"""
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def get_data_dir() -> Path:
    """用户数据目录（SQLite / Chroma / 日志 / 体检报告）。"""
    return get_app_root() / "data"


def get_config_dir() -> Path:
    """可写配置目录（app.yaml / schools/ / source_catalog.yaml）。"""
    return get_app_root() / "config"


def get_frontend_dist() -> Path:
    """前端构建产物目录（frontend/dist）。"""
    return get_app_root() / "frontend" / "dist"


def get_env_path() -> Path:
    """.env 文件路径（API key，安装后用户可编辑）。"""
    return get_app_root() / ".env"


def get_version() -> str:
    """读取应用版本号（根目录 VERSION 文件，单行）。

    读不到时返回 "0.0.0"（开发环境无 VERSION 文件不打断启动）。
    """
    version_file = get_app_root() / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"
