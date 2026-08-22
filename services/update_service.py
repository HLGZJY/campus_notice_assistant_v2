"""检查更新服务（打包发布方案 Step 5）。

流程：读 config/app.yaml 的 update.repo（owner/name）→ 调 GitHub
releases/latest API → 与本地 VERSION 数值比较 → 返回更新信息。

设计约束（PACKAGING.md）：
  - 静默失败：网络错误 / 配置缺失 / 响应异常一律返回 update_available=False，
    不抛异常、不影响主功能；error 字段带简短说明便于排查。
  - 镜像切换位：update.download_prefix 非空时，下载链接前缀替换为镜像。
  - 版本比对：tag 形如 v0.2.0，按 MAJOR.MINOR.PATCH 数值比较；解析失败回退
    字符串不等比较（宁可误报也不漏报）。
"""
from __future__ import annotations

import logging
from datetime import datetime

import requests

from config.store import ConfigStore
from utils.app_paths import get_version

logger = logging.getLogger(__name__)

# GitHub API 对匿名请求限流 60 次/小时/IP；检查更新频率低（启动 + 手动），足够
_GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
_REQUEST_TIMEOUT = 8  # 秒；启动静默检查不能拖慢页面
_USER_AGENT = "CampusNoticeAssistant-UpdateChecker"


def _parse_version(version: str) -> tuple[int, ...] | None:
    """把 v0.2.0 / 0.2.0 解析为 (0, 2, 0)；解析失败返回 None。"""
    v = (version or "").strip().lstrip("vV")
    parts = v.split(".")
    try:
        return tuple(int(p) for p in parts[:3])
    except (ValueError, TypeError):
        return None


def _is_newer(latest: str, current: str) -> bool:
    """latest 是否比 current 新（数值比较；解析失败回退字符串不等）。"""
    latest_parsed = _parse_version(latest)
    current_parsed = _parse_version(current)
    if latest_parsed is not None and current_parsed is not None:
        return latest_parsed > current_parsed
    return bool(latest) and latest != current


def _apply_mirror(url: str, prefix: str) -> str:
    """给 GitHub 下载链接套镜像前缀（国内加速切换位）。"""
    if not url or not prefix:
        return url
    if url.startswith(prefix):
        return url
    return f"{prefix}/{url}"


def check_for_update() -> dict:
    """检查更新入口（路由直接调用）。

    返回契约见 api.schemas.UpdateCheckResult；任何失败都返回
    update_available=False + error，HTTP 状态恒为 200。
    """
    checked_at = datetime.now().isoformat(timespec="seconds")
    current_version = get_version()

    result: dict = {
        "update_available": False,
        "current_version": current_version,
        "latest_version": "",
        "notes": "",
        "html_url": "",
        "assets": [],
        "checked_at": checked_at,
        "error": None,
    }

    try:
        cfg = ConfigStore.get_instance().app_config.update
    except Exception as e:  # noqa: BLE001
        result["error"] = f"读取配置失败: {e}"
        return result

    if not cfg.repo:
        result["error"] = "未配置 update.repo（config/app.yaml），检查更新已禁用"
        return result

    try:
        resp = requests.get(
            _GITHUB_API.format(repo=cfg.repo),
            headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        release = resp.json()
    except requests.Timeout:
        result["error"] = "检查更新超时（GitHub 连接不稳定）"
        return result
    except Exception as e:  # noqa: BLE001
        result["error"] = f"检查更新失败: {type(e).__name__}"
        logger.info("Update check failed: %s", e)
        return result

    tag_name = str(release.get("tag_name") or "")
    if not tag_name:
        result["error"] = "Release 缺少 tag_name"
        return result

    assets = [
        {
            "name": str(a.get("name") or ""),
            "size": int(a.get("size") or 0),
            "browser_download_url": _apply_mirror(
                str(a.get("browser_download_url") or ""), cfg.download_prefix
            ),
        }
        for a in release.get("assets", [])
        if isinstance(a, dict) and a.get("browser_download_url")
    ]

    result.update(
        {
            "latest_version": tag_name,
            "notes": str(release.get("body") or ""),
            "html_url": str(release.get("html_url") or ""),
            "assets": assets,
            "update_available": _is_newer(tag_name, current_version),
        }
    )
    return result
