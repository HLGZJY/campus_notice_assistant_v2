"""桌面托盘（B06 / DESKTOP-UPGRADE.md §4.2 tray.py、§3.4 系统通知）。

基于 pystray（纯 Python，Win/mac 统一 API）+ Pillow（已随 newspaper 依赖存在）。

菜单五项（DESKTOP-UPGRADE.md §9.3）：
  打开主界面 / 暂停调度 / 检查更新 / 打开日志目录 / 退出

B06 阶段各菜单项的行为：
  - 打开主界面  → 回调 on_open：恢复并显示主窗口（本批实现）
  - 暂停调度    → 回调 on_toggle_pause：B14 才接入 scheduler.pause()/resume()，
                  B06 阶段为占位（点击给出「后续版本启用」通知），避免提供假功能
  - 检查更新    → 回调 on_check_update：调用既有 services/update_service.check_for_update()
  - 打开日志目录 → 回调 on_open_log_dir：os.startfile 打开 data/logs
  - 退出        → 回调 on_quit：串联 B03 退出路径（完整编排 B08）

线程模型：pystray 图标在独立 daemon 线程 run_detached()，不阻塞主线程；
菜单点击与关闭窗口事件都在图标线程回调，回调需尽量轻量（耗时操作放回主线程
或子线程，避免拖慢托盘事件循环）。

notify()：Win 托盘气球通知（HAS_NOTIFICATION 时可用）；无通知能力时静默降级。

回滚点：`--no-tray` 启动参数可整体禁用托盘，窗口退回「关闭即退出」（见 app.py）。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from utils.app_paths import get_version

logger = logging.getLogger(__name__)

# 依赖延迟导入：无 pystray/Pillow 环境（回归、CI 无托盘）不阻塞模块加载
_imported = False
_Icon = None
_Menu = None
_MenuItem = None
_Image = None
_ImageDraw = None


def _ensure_imports() -> None:
    """惰性导入 pystray / PIL（无 GUI 环境不阻塞 import）。"""
    global _imported, _Icon, _Menu, _MenuItem, _Image, _ImageDraw
    if _imported:
        return
    from PIL import Image, ImageDraw  # type: ignore[import-not-found]
    from pystray import Icon, Menu, MenuItem  # type: ignore[import-not-found]

    _Icon, _Menu, _MenuItem = Icon, Menu, MenuItem
    _Image, _ImageDraw = Image, ImageDraw
    _imported = True


def _icon_file() -> Path | None:
    """定位品牌图标 app.ico：冻结态取 _internal（spec datas），dev 态取 packaging/。"""
    import sys

    candidates = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "app.ico")
    else:
        candidates.append(Path(__file__).resolve().parents[1] / "packaging" / "app.ico")
    for p in candidates:
        if p.is_file():
            return p
    return None


def _build_icon_image(size: int = 64) -> Any:
    """托盘图标：优先加载品牌 app.ico（与 exe/窗口图标统一）。

    ico 缺失时（如非打包环境手动清理）退回 Pillow 程序绘制兜底，保证托盘
    图标始终可见。品牌图标由 scratch/make_app_icon.py 生成（packaging/app.ico）。
    """
    _ensure_imports()
    ico_path = _icon_file()
    if ico_path is not None:
        try:
            img = _Image.open(ico_path)
            img.load()
            img = img.convert("RGBA")
            if img.width != size:
                img = img.resize((size, size), _Image.LANCZOS)
            return img
        except Exception:  # noqa: BLE001 - ico 损坏时退回绘制兜底
            logger.debug("品牌图标加载失败，退回绘制兜底", exc_info=True)
    img = _Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = _ImageDraw.Draw(img)
    # 圆角方形底（品牌靛紫，与应用 --primary 一致）
    radius = size // 5
    draw.rounded_rectangle(
        (1, 1, size - 1, size - 1),
        radius=radius,
        fill=(99, 102, 241, 255),
        outline=(79, 70, 229, 255),
        width=max(1, size // 32),
    )
    # 中央白色「C」字形（Campus Notice Assistant）
    cx, cy = size // 2, size // 2
    r = size * 0.27
    w = max(1, size // 10)
    draw.arc(
        (cx - r, cy - r, cx + r, cy + r),
        start=-60, end=200, fill=(255, 255, 255, 255), width=w,
    )
    return img


# ---------------------------------------------------------------------------
# 托盘对象
# ---------------------------------------------------------------------------
class DesktopTray:
    """pystray 托盘图标与菜单（B06.T1）。

    与 DesktopApp 通过回调解耦，避免模块循环依赖。全部菜单行为由
    ``callbacks`` 注入；未注入的项点击时给出「功能暂未启用」通知。
    """

    def __init__(
        self,
        title: str = "南湖窗",
        callbacks: dict[str, Callable[[], Any] | None] | None = None,
    ) -> None:
        _ensure_imports()
        self.title = title
        self.callbacks: dict[str, Callable[[], Any] | None] = callbacks or {}
        self._icon: Any = None
        self._thread: threading.Thread | None = None
        self._menu = self._build_menu()

    # ---------- 菜单 ----------
    def _build_menu(self) -> Any:
        """构建托盘菜单（五项）。"""
        _ensure_imports()
        return _Menu(
            _MenuItem("打开主界面", self._on_open, default=True),
            _MenuItem("暂停调度", self._on_toggle_pause),
            _MenuItem("检查更新", self._on_check_update),
            _MenuItem("打开日志目录", self._on_open_log_dir),
            _MenuItem("退出", self._on_quit),
        )

    def _cb(self, key: str) -> Callable[[], Any] | None:
        return self.callbacks.get(key)

    def _on_open(self, *_: Any) -> None:
        cb = self._cb("on_open")
        if cb:
            cb()

    def _on_toggle_pause(self, *_: Any) -> None:
        cb = self._cb("on_toggle_pause")
        if cb:
            cb()
            return
        # B06 占位：B14 接入 scheduler.pause()/resume() 前，点击给出提示而非假功能
        self.notify("调度暂停/恢复将在 v0.2.0 后续版本启用（B14）", "调度")

    def _on_check_update(self, *_: Any) -> None:
        cb = self._cb("on_check_update")
        if cb:
            cb()
            return
        self.notify("检查更新功能尚未接入（B18 落地）", "检查更新")

    def _on_open_log_dir(self, *_: Any) -> None:
        cb = self._cb("on_open_log_dir")
        if cb:
            cb()

    def _on_quit(self, *_: Any) -> None:
        cb = self._cb("on_quit")
        if cb:
            cb()

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        """创建图标并在线程中运行（run_detached，不阻塞主线程）。

        Returns:
            bool：托盘是否成功启动（无 pystray 环境/创建失败返回 False）。
        """
        _ensure_imports()
        if self._icon is not None:
            logger.info("托盘已在运行，忽略重复 start()")
            return True
        try:
            icon = _Icon(self.title, _build_icon_image(), menu=self._menu)
            icon.run_detached()  # 独立 daemon 线程
            self._icon = icon
            logger.info("托盘已启动（菜单五项就绪）")
            return True
        except Exception:  # noqa: BLE001 - 无托盘环境（回归/CI/无 GUI）静默降级
            logger.debug("托盘启动失败，降级为无托盘模式", exc_info=True)
            self._icon = None
            return False

    def stop(self) -> None:
        """停止托盘图标（B08 完整退出序列中调用；B06 由 on_quit 隐式触发）。"""
        if self._icon is not None:
            try:
                self._icon.stop()
                logger.info("托盘已停止")
            except Exception:  # noqa: BLE001
                logger.debug("托盘停止异常（忽略）", exc_info=True)
            self._icon = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def notify(self, message: str, title: str | None = None) -> bool:
        """发送托盘气球通知（Win balloon）。无通知能力时静默降级。

        Returns:
            bool：通知是否已投递。
        """
        if self._icon is None:
            return False
        if not getattr(_Icon, "HAS_NOTIFICATION", False):
            logger.debug("当前平台/后端不支持托盘通知，跳过 notify")
            return False
        try:
            self._icon.notify(message, title or self.title)
            return True
        except Exception:  # noqa: BLE001
            logger.debug("托盘通知发送失败（忽略）", exc_info=True)
            return False


def notify(
    message: str,
    title: str | None = None,
    tray_title: str = "南湖窗",
) -> bool:
    """模块级便捷函数：创建一次性托盘发通知（供暂未持有 DesktopTray 的场景）。

    适用于 B21 通知中心等需要从壳外发通知的场合；B06 主要走 DesktopTray 实例。
    无托盘环境返回 False，不抛异常。
    """
    try:
        t = DesktopTray(title=tray_title)
        if not t.start():
            return False
        ok = t.notify(message, title)
        t.stop()
        return ok
    except Exception:  # noqa: BLE001
        logger.debug("模块级通知发送失败（忽略）", exc_info=True)
        return False
