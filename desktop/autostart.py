"""桌面开机自启（Win HKCU Run）。

复用 campus_notice.iss:74-78 的落点：``HKCU\Software\Microsoft\Windows\CurrentVersion\Run``，
ValueName 用 Inno 里的 ``MyAppNameEn``（"CampusNoticeAssistant"）。写入值 =
应用可执行路径 + `` --autostart``——Windows 开机拉起该命令时，应用通过
``--autostart`` 标记识别自己正处于「自启」场景，从而静默到托盘、不弹窗打扰。

本模块只做「注册表增删读」这一件事，不耦合壳的窗口/退出逻辑；控制面端点
（``api/routes/desktop.py`` 的 ``/desktop/autostart``）与测试都通过它落地。
启动参数的真正行为（--minimized/--autostart/--browser）在 desktop/app.py 编排，
这里只负责把「要不要开机自启」持久化到注册表。

对外接口：
  - ``RUN_KEY`` / ``VALUE_NAME``            注册表路径与值名常量
  - ``build_command()``                    构造要写入的启动命令
  - ``is_enabled()``                       当前是否已写入（读注册表）
  - ``set_enabled(enabled)``               写/删注册表值，返回 (ok, detail)
  - ``set_registry(open_factory)``         测试注入：替换注册表访问工厂（不碰真实 HKCU）

非 Windows（无 winreg）时模块仍可导入，所有操作返回 not supported，不抛异常——
保证回归 / CI（非 Win）环境下桌面用例照常 [SKIP] 而非崩溃。
"""
from __future__ import annotations

import logging
import sys
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# 是否真正可用（Windows 且可 import winreg）
_winreg = None
try:
    import winreg  # type: ignore[import-not-found]  # Windows only

    _winreg = winreg
except ImportError:  # 非 Windows：模块可导入，但功能不可用
    _winreg = None

# 注册表落点（复用 campus_notice.iss:74-78 的 ValueName，保证安装时可选自启与
# 应用内开关写的是同一个键，互不冲突、卸载时 Inno 的 uninsdeletevalue 一并清理）
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Inno 里的 #define MyAppNameEn "CampusNoticeAssistant"
VALUE_NAME = "CampusNoticeAssistant"

# 自启场景标记：写入命令尾部追加 --autostart，应用据此静默到托盘
AUTOSTART_FLAG = "--autostart"


# ---------------------------------------------------------------------------
# 注册表访问工厂（默认指向真实 HKCU；测试可注入临时 Key）
# ---------------------------------------------------------------------------
def _default_open_run_key(mode: str):
    """打开 HKCU 下的 Run 键（返回 (key_handle, already_existed)）。

    mode: "read" 只读打开；"write" 打开/创建用于写删。返回 handle；调用方负责 CloseKey。
    """
    if _winreg is None:
        raise OSError("winreg 不可用（非 Windows 平台）")
    if mode == "read":
        return _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, RUN_KEY, 0, _winreg.KEY_READ)
    return _winreg.CreateKeyEx(
        _winreg.HKEY_CURRENT_USER,
        RUN_KEY,
        0,
        _winreg.KEY_SET_VALUE | _winreg.KEY_READ,
    )


# 模块级工厂：测试经 set_registry() 替换它指向临时注册表根，避免触碰真实 Run 键。
_open_run_key: Callable[[str], object] = _default_open_run_key


def set_registry(open_factory: Callable[[str], object]) -> None:
    """测试注入：替换注册表打开工厂。

    Args:
        open_factory: 接收 mode（"read"/"write"）并返回一个带 QueryValueEx /
            SetValueEx / DeleteValue / CloseKey 语义的 handle。测试可用 winreg 指向
            临时 HKCU 子键，从而在真实 Run 键之外验证增删逻辑。
    """
    global _open_run_key
    _open_run_key = open_factory


def supported() -> bool:
    """当前平台是否支持开机自启（Windows 且 winreg 可用）。"""
    return _winreg is not None


# ---------------------------------------------------------------------------
# 命令构造
# ---------------------------------------------------------------------------
def build_command(exe_path: Optional[str] = None) -> str:
    """构造写入注册表的启动命令。

    Args:
        exe_path: 应用可执行文件路径；None 时自动推导——
                  冻结（PyInstaller）= sys.executable；开发 = 当前 Python + -m desktop。

    Returns:
        str：形如 ``"C:\\path\\app.exe" --autostart`` 的字符串（值数据）。
    """
    if exe_path is None:
        if getattr(sys, "frozen", False):
            base = f'"{sys.executable}"'
        else:
            # 开发模式：用当前解释器 + python -m desktop 作为自启命令
            base = f'"{sys.executable}" -m desktop'
    else:
        base = f'"{exe_path}"'
    # 统一加 --autostart 标记：Windows 开机拉起时应用识别自启场景
    return f"{base} {AUTOSTART_FLAG}"


# ---------------------------------------------------------------------------
# 读 / 写 / 删
# ---------------------------------------------------------------------------
def is_enabled() -> bool:
    """当前 Run 键下是否已写入自启值。

    非 Windows / 键不存在 / 值不存在 → False（不抛异常）。
    """
    if not supported():
        return False
    try:
        key = _open_run_key("read")
        try:
            # KEY_READ 打开时 value 为 None，仅需确认值存在
            _winreg.QueryValueEx(key, VALUE_NAME)  # type: ignore[union-attr]
            return True
        finally:
            _winreg.CloseKey(key)  # type: ignore[union-attr]
    except FileNotFoundError:
        return False  # Run 键或值不存在
    except OSError as e:
        logger.debug("读取自启注册表失败（视为未启用）: %s", e)
        return False


def set_enabled(enabled: bool, exe_path: Optional[str] = None) -> Tuple[bool, str]:
    """写入或删除开机自启注册表值。

    Args:
        enabled: True 写入；False 删除。
        exe_path: 写入时用的可执行路径；None 自动推导（见 build_command）。

    Returns:
        (ok, detail)：ok 是否成功；detail 供日志/前端展示的说明。
    """
    if not supported():
        return False, "当前平台不支持开机自启（需 Windows）"
    try:
        key = _open_run_key("write")
        try:
            if enabled:
                cmd = build_command(exe_path)
                _winreg.SetValueEx(  # type: ignore[union-attr]
                    key, VALUE_NAME, 0, _winreg.REG_SZ, cmd
                )
                logger.info("开机自启已写入 HKCU\\%s\\%s = %s", RUN_KEY, VALUE_NAME, cmd)
                return True, "已开启开机自启"
            try:
                _winreg.DeleteValue(key, VALUE_NAME)  # type: ignore[union-attr]
                logger.info("开机自启已删除 HKCU\\%s\\%s", RUN_KEY, VALUE_NAME)
                return True, "已关闭开机自启"
            except FileNotFoundError:
                # 本就没启用：视为操作成功（幂等）
                return True, "未检测到自启记录（已处于关闭状态）"
        finally:
            _winreg.CloseKey(key)  # type: ignore[union-attr]
    except OSError as e:  # noqa: BLE001 - 注册表访问失败返回可展示错误，不抛异常
        logger.exception("写删自启注册表失败")
        return False, f"注册表操作失败: {e}"
