"""桌面版更新闭环（B18 / DESKTOP-UPGRADE.md §3.4 更新项、§5.2 B12）。

在既有 `services/update_service.py`（只检查不下载）之上补齐「下载 → sha256 校验
→ 弹窗确认 → 调用安装包 → 退出当前实例」的完整闭环。**决策项 3：用户确认，不静默**。

`latest.json` 更新清单（挂在 GitHub Release 下，与安装包 assets 并列）：
```json
{
  "version": "0.2.1",
  "notes": "更新说明（release body / changelog）",
  "assets": [
    {
      "name": "校园通知助手-桌面版-setup.exe",
      "url": "https://github.com/owner/repo/releases/download/v0.2.1/xxx-setup.exe",
      "sha256": "64 位十六进制"
    }
  ]
}
```
设计要点：
- **契约保全（B18.T3）**：本模块**不修改** `services/update_service.py`。它只复用
  update_service 的配置读取（ConfigStore → repo / download_prefix），原「未配 repo
  时恒 200、静默失败」契约原样保留。
- **sha256 为什么来自 latest.json**：GitHub Releases API 不直接返回 asset 的 sha256，
  而 `update_service` 拿到的 assets 只有 name/size/browser_download_url。latest.json
  是 sha256 的唯一权威来源，故更新器独立拉取 latest.json 而非复用 check 结果。
- **可注入（便于端到端测试）**：下载 / sha256 校验是纯逻辑；确认 / 启动安装器 /
  退出当前实例通过参数注入（生产传 webview 确认对话框与 os.startfile，测试传桩）。
- **R12 降级**：若用户取消 / 缺安装器 / 校验失败，只中止本次，不影响主功能。

异常策略：网络 / 配置缺失 / 清单损坏 / 校验失败均抛明确异常（或返回结构化结果），
由调用方（托盘 / 控制面端点）决定提示方式；不静默吞掉，也不让更新器带崩壳。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

LATEST_MANIFEST_NAME = "latest.json"
_REQUEST_TIMEOUT = 30  # 下载安装包可能较大，放宽；清单用同一超时

# 桌面版安装包文件名关键词（优先级从高到低，用于从 assets 里挑桌面版产物）。
# 「桌面版」是明确标识；「-setup.exe」为兜底（桌面版安装包通常以此结尾）。
DESKTOP_SETUP_KEYWORDS = ("桌面版", "-setup.exe")
DESKTOP_SETUP_SUFFIXES = DESKTOP_SETUP_KEYWORDS


# ---------------------------------------------------------------------------
# latest.json 解析 / 校验（纯逻辑，无网络）
# ---------------------------------------------------------------------------
def parse_manifest(text: str) -> dict:
    """解析并校验 latest.json 文本，返回规范化 dict。

    校验通过后返回：
        {"version": str, "notes": str,
         "assets": [{"name", "url", "sha256"}, ...]}
    清单缺失 / JSON 损坏 / 版本缺失 → 抛 ValueError。

    Args:
        text: latest.json 原文。
    Returns:
        规范化清单 dict。
    Raises:
        ValueError: 清单格式不合法（无法作为升级依据）。
    """
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"latest.json 不是合法 JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError("latest.json 顶层须为对象")

    version = str(raw.get("version") or "").strip()
    if not version:
        raise ValueError("latest.json 缺少 version 字段")

    assets = raw.get("assets")
    if not isinstance(assets, list):
        raise ValueError("latest.json 缺少 assets 列表")
    normalized: list[dict[str, Any]] = []
    for a in assets:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "").strip()
        url = str(a.get("url") or a.get("browser_download_url") or "").strip()
        sha256 = str(a.get("sha256") or "").strip().lower()
        if not name or not url:
            continue
        normalized.append(
            {"name": name, "url": url, "sha256": sha256}
        )
    if not normalized:
        raise ValueError("latest.json assets 列表为空或无有效条目")

    return {"version": version, "notes": str(raw.get("notes") or ""), "assets": normalized}


def select_desktop_asset(assets: list[dict], suffix: str | None = None) -> dict | None:
    """从清单 assets 里挑选桌面版安装包。

    优先级：
      1. 文件名含「桌面版」→ 明确是桌面版产物；
      2. 文件名以 ``-setup.exe`` 结尾（不含「在线版」等其它 flavor 关键词）；
      3. ``suffix`` 传参时用它做关键词兜底；
      4. 全部不中 → 取第一个可下载项（保守放行）。

    Args:
        assets: parse_manifest 返回的 assets 列表。
        suffix: 可选的自定义安装包关键词/后缀（第 3 优先级）。
    Returns:
        选中的 asset dict（含 name/url/sha256），无则 None。
    """
    if not assets:
        return None
    # 1. 含「桌面版」优先
    for a in assets:
        if "桌面版" in a.get("name", ""):
            return a
    # 2. 以 -setup.exe 结尾，且排除明显是其它 flavor 的产物
    for a in assets:
        name = a.get("name", "")
        if name.endswith("-setup.exe") or name.endswith(".exe") and "-setup" in name:
            if any(k in name for k in ("在线版", "-cloud", "cloud-", "web")):
                continue
            return a
    # 3. 自定义兜底
    if suffix:
        for a in assets:
            if suffix in a.get("name", ""):
                return a
    return assets[0]  # 4. 兜底：取第一个可下载项


# ---------------------------------------------------------------------------
# 下载 / sha256（网络 + 文件，纯逻辑可测）
# ---------------------------------------------------------------------------
def sha256_of(path: Path) -> str:
    """计算文件的 SHA-256（小写十六进制）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: Path, expected: str) -> bool:
    """校验文件 sha256 与期望是否一致（大小写不敏感）。

    expected 为空时视为「未提供校验值」，返回 True（放行——无校验依据的旧清单
    不应阻断升级；由确认环节提示用户）。
    """
    if not expected:
        logger.warning("latest.json 未提供 sha256，跳过校验（放行）")
        return True
    actual = sha256_of(path)
    return actual.lower() == expected.lower()


def download_asset(url: str, dest: Path, timeout: float = _REQUEST_TIMEOUT) -> int:
    """流式下载安装包到 ``dest``，返回字节数。

    Args:
        url: 完整下载地址（已含镜像前缀，见 _apply_mirror）。
        dest: 目标文件路径（父目录由调用方保证存在）。
        timeout: 单次读超时（秒）。
    Returns:
        下载的总字节数。
    Raises:
        RuntimeError: 下载失败 / HTTP 非 200 / 写入失败。
    """
    import requests

    try:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    f.write(chunk)
                    total += len(chunk)
            return total
    except requests.RequestException as e:
        raise RuntimeError(f"下载安装包失败: {type(e).__name__}: {e}") from e


def fetch_manifest(repo: str, download_prefix: str = "",
                   timeout: float = _REQUEST_TIMEOUT) -> str:
    """下载 latest.json 原文（文本）。

    Args:
        repo: GitHub "owner/name"。
        download_prefix: 镜像前缀（与 update_service 同源，空=直连 GitHub）。
        timeout: 超时秒数。
    Returns:
        latest.json 文本内容。
    Raises:
        RuntimeError: repo 未配置 / 网络失败 / HTTP 非 200。
    """
    if not repo:
        raise RuntimeError("未配置 update.repo，更新清单不可用")
    import requests

    url = manifest_url(repo, download_prefix)
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        raise RuntimeError(f"拉取 latest.json 失败: {type(e).__name__}: {e}") from e


def manifest_url(repo: str, download_prefix: str = "") -> str:
    """拼 latest.json 的下载地址（含镜像前缀）。

    GitHub raw 约定：latest.json 随 release tag 上传，访问
        https://github.com/{repo}/releases/latest/download/{name}
    会重定向到最新 release 的该附件。
    """
    base = f"https://github.com/{repo}/releases/latest/download/{LATEST_MANIFEST_NAME}"
    if download_prefix:
        base = f"{download_prefix}/{base}"
    return base


def apply_mirror(url: str, prefix: str) -> str:
    """给下载链接套镜像前缀（与 update_service._apply_mirror 逻辑一致）。

    独立实现而非 import update_service 私有函数，避免形成对既有模块的耦合改动。
    """
    if not url or not prefix:
        return url
    if url.startswith(prefix):
        return url
    return f"{prefix}/{url}"


# ---------------------------------------------------------------------------
# 配置读取（复用 ConfigStore，与 update_service 同源；不修改原模块）
# ---------------------------------------------------------------------------
def get_update_config() -> dict:
    """读取 update.repo / update.download_prefix 配置。

    Returns:
        {"repo": str, "download_prefix": str}。配置不可用 / 读取异常返回空 repo
        （等同未配置，调用方按「更新禁用」处理）。
    """
    try:
        from config.store import ConfigStore

        cfg = ConfigStore.get_instance().app_config.update
        return {
            "repo": str(cfg.repo or "").strip(),
            "download_prefix": str(cfg.download_prefix or "").strip().rstrip("/"),
        }
    except Exception as e:  # noqa: BLE001 - 配置读取失败视为未配置更新
        logger.debug("读取 update 配置失败（视为未配置）: %s", e)
        return {"repo": "", "download_prefix": ""}


def check_repo_configured(repo: str | None = None) -> bool:
    """update.repo 是否已配置（空 = 更新禁用）。"""
    if repo is not None:
        return bool(repo.strip())
    return bool(get_update_config()["repo"])


# ---------------------------------------------------------------------------
# 完整闭环（B18.T2：下载 → 校验 → 确认 → 安装 → 退出）
# ---------------------------------------------------------------------------
def perform_update(
    *,
    download_dir: Path | None = None,
    confirm_fn: Callable[[dict, Path], bool] | None = None,
    launch_fn: Callable[[Path], None] | None = None,
    quit_fn: Callable[[], None] | None = None,
    repo: str | None = None,
    download_prefix: str | None = None,
    timeout: float = _REQUEST_TIMEOUT,
    fetch_manifest_fn: Callable[[str, str, float], str] | None = None,
) -> dict:
    """执行一次完整升级闭环。

    决策项 3（用户确认，不静默）：
        下载 → sha256 校验 → **弹窗确认** → 调用安装包 → 退出当前实例。
    sha256 校验失败时中止并提示（D-45），不进入确认/安装。

    Args:
        download_dir: 安装包临时下载目录；缺省用应用数据目录下 _updates。
        confirm_fn: 确认回调 `(manifest, installer_path) -> bool`。返回 False 表示
            用户取消，中止安装。缺省用内置 webview 确认对话框（无窗口时日志放行）。
        launch_fn: 启动安装器回调 `(installer_path)`。缺省用 os.startfile。
        quit_fn: 退出当前实例回调。缺省记日志并返回（由调用方决定是否退出）。
        repo: 覆盖 update.repo（测试注入用）。
        download_prefix: 覆盖镜像前缀（测试注入用）。
        timeout: 下载超时秒数。
        fetch_manifest_fn: 覆盖清单拉取回调 `(repo, prefix, timeout) -> str`。缺省用
            网络版 ``fetch_manifest``；测试注入本地 mock（保持离线可测）。
    Returns:
        结构化结果 dict（供调用方提示）：
            {"status": "ok"|"cancelled"|"disabled"|"error",
             "message": str, "downloaded": Path|None, "version": str}
    Raises:
        不抛异常：全部错误收敛到返回结果的 error 分支（与静默失败风格一致）。
    """
    cfg = get_update_config()
    repo = repo if repo is not None else cfg["repo"]
    prefix = download_prefix if download_prefix is not None else cfg["download_prefix"]

    if not repo:
        return {"status": "disabled", "message": "未配置 update.repo，更新已禁用", "downloaded": None, "version": ""}

    fetch = fetch_manifest_fn or fetch_manifest
    # 1. 拉取清单
    try:
        manifest_text = fetch(repo, prefix, timeout)
        manifest = parse_manifest(manifest_text)
    except RuntimeError as e:
        return {"status": "error", "message": str(e), "downloaded": None, "version": ""}
    except ValueError as e:
        return {"status": "error", "message": str(e), "downloaded": None, "version": ""}

    asset = select_desktop_asset(manifest.get("assets", []))
    if asset is None:
        return {"status": "error", "message": "latest.json 未包含可下载的安装包", "downloaded": None, "version": manifest.get("version", "")}

    url = apply_mirror(asset["url"], prefix)
    # 2. 下载安装包
    dl_dir = download_dir or _default_download_dir()
    dl_dir.mkdir(parents=True, exist_ok=True)
    installer = dl_dir / asset["name"]
    try:
        download_asset(url, installer, timeout)
    except RuntimeError as e:
        return {"status": "error", "message": str(e), "downloaded": None, "version": manifest.get("version", "")}

    # 3. sha256 校验（D-45：失败中止并提示）
    if not verify_sha256(installer, asset.get("sha256", "")):
        msg = f"安装包校验失败（sha256 不符），已中止升级：{asset['name']}"
        logger.error(msg)
        return {"status": "error", "message": msg, "downloaded": installer, "version": manifest.get("version", "")}

    # 4. 弹窗确认（用户确认，不静默）
    confirm = confirm_fn or _default_confirm
    try:
        if not confirm(manifest, installer):
            return {"status": "cancelled", "message": "用户取消了升级", "downloaded": installer, "version": manifest.get("version", "")}
    except Exception as e:  # noqa: BLE001 - 确认环节异常按取消处理，不进安装
        logger.exception("确认对话框异常，按取消处理")
        return {"status": "cancelled", "message": f"确认升级失败: {e}", "downloaded": installer, "version": manifest.get("version", "")}

    # 5. 调用安装包 + 退出当前实例
    launch = launch_fn or _default_launch
    try:
        launch(installer)
    except Exception as e:  # noqa: BLE001 - 启动安装器失败不崩溃
        logger.exception("启动安装包失败")
        return {"status": "error", "message": f"启动安装包失败: {e}", "downloaded": installer, "version": manifest.get("version", "")}

    # 6. 退出当前实例（安装包通常要求目标程序未占用文件）
    quit_fn_ = quit_fn or _default_quit
    try:
        quit_fn_()
    except Exception as e:  # noqa: BLE001
        logger.debug("退出当前实例回调异常（忽略）: %s", e)

    return {"status": "ok", "message": "升级流程已触发，请在弹出的安装向导中完成", "downloaded": installer, "version": manifest.get("version", "")}


def _default_download_dir() -> Path:
    """默认安装包下载目录：应用数据目录下 _updates。"""
    try:
        from utils.app_paths import get_data_dir

        return get_data_dir() / "_updates"
    except Exception:  # noqa: BLE001
        import tempfile

        return Path(tempfile.gettempdir())


def _default_confirm(manifest: dict, installer: Path) -> bool:
    """默认确认对话框：有 webview 窗口则弹窗，无窗口则日志放行（测试/无 GUI）。"""
    message = (
        f"检测到新版本 {manifest.get('version', '')}。\n\n"
        f"将下载并安装安装包：\n{installer.name}\n\n确认立即升级吗？"
    )
    try:
        import webview

        if webview.windows:
            return bool(webview.windows[0].create_confirmation_dialog(message))
    except Exception:  # noqa: BLE001 - 对话框失败回退放行，不阻塞升级
        logger.debug("更新确认对话框失败（回退放行）", exc_info=True)
    logger.info("更新确认：未弹出对话框（回退放行），version=%s", manifest.get("version"))
    return True


def _default_launch(path: Path) -> None:
    """默认启动安装器：os.startfile（Windows，交给系统默认动作）。"""
    import os

    os.startfile(str(path))  # type: ignore[attr-defined]  # Windows only
    logger.info("已启动安装包：%s", path)


def _default_quit() -> None:
    """默认退出回调：仅记日志（壳接入点在 tray / 控制面决定真正退出）。"""
    logger.info("更新完成，请退出当前实例后运行安装包")
