"""桌面壳设置（settings.json）。

管理壳层自身的持久化配置：窗口几何、开机自启、关闭行为、最小化到托盘等。
与后端 `config/app.yaml` 职责分离——本模块只管「壳」的偏好，不碰业务配置。

B03 阶段只落「加载/保存」基础框架与默认值；窗口几何（B06）、
自启与关闭行为（B08/B13）在对应批次填充具体字段。

存储位置：可写配置目录下 `settings.json`（与后端 config/ 目录一致）。
写入失败视为可容忍（降级为内存态，不打断启动）。
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from utils.app_paths import get_config_dir

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.json"

# 窗口几何默认值（首次启动 / 无历史记录时使用，B06.T3）
DEFAULT_GEOMETRY = {
    "x": None,         # 默认由系统定位
    "y": None,
    "width": 1280,
    "height": 800,
    "maximized": False,
}


def settings_path() -> Path:
    """壳设置文件完整路径。"""
    return get_config_dir() / SETTINGS_FILENAME


class ShellSettings:
    """壳设置的读写封装（线程安全）。

    结构为自由字典，B06/B13 等批次在此之上叠加字段。默认值在
    ``defaults()`` 中集中定义，未落盘时返回默认。
    """

    _lock = threading.RLock()

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or settings_path()
        self._data: dict[str, Any] = {}

    # ---------- 默认值 ----------
    @staticmethod
    def defaults() -> dict[str, Any]:
        """壳设置的默认值（B06 起叠加窗口几何等字段）。"""
        return {
            "window": {},          # 窗口几何（B06 填充：x/y/width/height/maximized）
            "close_action": "minimize_to_tray",  # B06 实际行为即最小化到托盘；B15 起可配置 "exit"
            "autostart": False,    # B13 填充：开机自启
            "start_minimized": False,  # B13 填充；B15 起作为「启动方式」用户偏好
        }

    # ---------- 读写 ----------
    def load(self) -> "ShellSettings":
        """从磁盘加载设置；文件缺失/损坏时回退默认值（不抛异常）。"""
        with self._lock:
            merged = dict(self.defaults())
            try:
                if self._path.exists():
                    raw = json.loads(self._path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        merged = self._merge(merged, raw)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("壳设置加载失败，使用默认值: %s", e)
            self._data = merged
            return self

    def save(self) -> bool:
        """把当前设置写回磁盘。失败仅记日志，不抛异常。"""
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return True
            except OSError as e:
                logger.warning("壳设置写入失败（降级为内存态）: %s", e)
                return False

    def get(self, key: str, default: Any = None) -> Any:
        """取单键值。"""
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设单键值（仅内存，需自行调用 save 落盘）。"""
        with self._lock:
            self._data[key] = value

    # ---------- 窗口几何（B06.T3） ----------
    def get_window_geometry(self) -> dict[str, Any]:
        """读取窗口几何（已与默认合并）。缺失字段回退默认值。

        Returns:
            dict：含 x / y / width / height / maximized。
        """
        with self._lock:
            window = self._data.get("window") or {}
            result = dict(DEFAULT_GEOMETRY)
            for k, v in window.items():
                if k in result and v is not None:
                    result[k] = v
            # 非法数值防御：宽高必须为正整数
            for k in ("width", "height"):
                try:
                    result[k] = max(200, int(result[k]))
                except (TypeError, ValueError):
                    result[k] = DEFAULT_GEOMETRY[k]
            return result

    def set_window_geometry(
        self,
        *,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        maximized: bool | None = None,
    ) -> bool:
        """保存窗口几何到 settings.json 并落盘。

        仅覆盖传入的非 None 字段；write 失败返回 False（降级内存态，不打断退出）。
        """
        with self._lock:
            window = dict(self._data.get("window") or {})
            for key, val in (
                ("x", x), ("y", y), ("width", width),
                ("height", height), ("maximized", maximized),
            ):
                if val is not None:
                    window[key] = val
            self._data["window"] = window
        return self.save()

    @staticmethod
    def _merge(base: dict, overlay: dict) -> dict:
        """浅合并：overlay 覆盖 base，保留 base 未出现的默认键。"""
        result = dict(base)
        for k, v in overlay.items():
            if isinstance(v, dict) and isinstance(result.get(k), dict):
                result[k] = {**result[k], **v}
            else:
                result[k] = v
        return result
