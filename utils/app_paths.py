"""冻结感知的应用根目录解析（打包支持）。

PyInstaller onedir 模式下，模块的 ``__file__`` 指向 ``_internal/`` 内部目录，
而用户数据（data/）、可写配置（config/）、前端产物（frontend/dist/）都应位于
exe 同级目录。所有运行时路径解析统一走本模块：

- 开发模式：项目根 = ``utils/app_paths.py`` 的上上级目录
- 冻结模式：项目根 = exe 所在目录（``sys.executable`` 的父目录）

新增运行时路径时一律从这里取，不要再写 ``Path(__file__).parent``。

B16 起（v0.2.0 数据目录三态，见 docs/DESKTOP-BATCH-PLAN.md §B16）：
数据目录从「仅 dev/frozen 两态」扩展为 **dev / portable / installed 三态**：

- ``dev``      ：源码运行 → 仓库根 ``data/``（开发基线，行为不变）
- ``portable`` ：冻结且 exe 同级存在 ``data/``（老布局 / 便携版，向后兼容优先）
- ``installed``：冻结且 exe 同级无 ``data/`` → 用户数据落 ``%APPDATA%/CampusNoticeAssistant/data``
                （程序与数据分离，升级可整目录替换）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 数据目录名（各处统一引用）
DATA_DIR_NAME = "data"
# 便携模式显式标记（置于 exe 同级 data/ 下，强制走 portable）
PORTABLE_MARKER = ".portable"
# installed 态用户数据根（%APPDATA%/CampusNoticeAssistant）
_INSTALLED_DIR_NAME = "CampusNoticeAssistant"


def get_app_root() -> Path:
    """返回应用根目录（开发 = 仓库根；冻结 = exe 所在目录）。"""
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _frozen_exe_dir() -> Path:
    """冻结模式下 exe 所在目录。"""
    return Path(sys.executable).resolve().parent


def _installed_data_dir() -> Path:
    """installed 态用户数据目录：%APPDATA%/CampusNoticeAssistant/data。

    %APPDATA% 缺失（极罕见）时回退到 exe 同级 data，避免启动失败。
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata).resolve() / _INSTALLED_DIR_NAME / DATA_DIR_NAME
    # 兜底：回退到 exe 同级（与 portable 相同，至少可用）
    return _frozen_exe_dir() / DATA_DIR_NAME


def get_data_mode() -> str:
    """返回数据目录三态：``dev`` / ``portable`` / ``installed``。

    判定优先级：
    1. 未冻结（源码运行）→ ``dev``；
    2. 冻结且 exe 同级存在 ``data/.portable`` 标记 或 存在 ``data/`` → ``portable``；
    3. 冻结且无 exe 同级 data → ``installed``（数据落 %APPDATA%）。
    """
    if not getattr(sys, "frozen", False):
        return "dev"
    exe_dir = _frozen_exe_dir()
    if (exe_dir / DATA_DIR_NAME / PORTABLE_MARKER).exists():
        return "portable"
    if (exe_dir / DATA_DIR_NAME).exists():
        return "portable"
    return "installed"


def get_data_dir() -> Path:
    """用户数据目录（SQLite / Chroma / 日志 / 体检报告）。

    三态解析：
    - ``dev``      → 仓库根 ``data``
    - ``portable`` → exe 同级 ``data``（向后兼容，优先读老数据）
    - ``installed``→ ``%APPDATA%/CampusNoticeAssistant/data``
    """
    mode = get_data_mode()
    if mode == "dev":
        return get_app_root() / DATA_DIR_NAME
    if mode == "portable":
        return _frozen_exe_dir() / DATA_DIR_NAME
    return _installed_data_dir()


def get_config_dir() -> Path:
    """可写配置目录（app.yaml / schools/ / source_catalog.yaml）。

    B16 只对「数据目录」三态化（标题与迁移目标均为 data）；配置仍随应用根
    （冻结 = exe 同级 config），与 build.py 布局严格对齐。见模块文档说明。
    """
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
