"""Windows 关机/注销信号拦截（B08 / DESKTOP-UPGRADE.md §6 K5）。

需求（K5）：注册 ``WM_QUERYENDSESSION``（Windows 关机/注销），让系统关机前
走与应用内托盘「退出」完全相同的退出路径（写窗口几何 → 停后端 → 停调度 →
停托盘 → 释放单实例锁 → sys.exit(0)），避免 SQLite 在关机瞬间被强制杀进程
而产生 ``-journal`` 残留或数据损坏（Gate B08 自动项：无 -journal + integrity_check=ok）。

实现策略：
  系统发起关机/注销时，会把 ``WM_QUERYENDSESSION`` 以广播 SendMessage 形式
  发送到**所有顶层窗口**。本模块创建一个**隐藏的顶层窗口**（自带消息泵线程），
  用它接收该广播，并把事件转发给注入的回调（壳层据此触发统一退出序列）。
  该窗口对用户不可见，只承担「系统关机信号 → 壳退出路径」的桥接职责。

被 test_desktop_lifecycle.py / test_desktop_shutdown.py 契约引用的接口（勿随意改名）：
  - ``ShutdownHook``              （类：隐藏顶层窗口 + 消息泵 + WndProc）
  - ``WM_QUERYENDSESSION`` / ``WM_ENDSESSION``（常量，供测试直发消息）
  - ``handle_message``            模块级：把一条系统消息分发给 WndProc 逻辑
                                   （测试可同步直调，隔离 GUI/线程依赖）

平台：仅 Windows 有意义；非 win32 环境 ``start()`` 返回 False 且静默降级
（测试/CI/开发可在非 Windows 跑，不报错）。
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 系统消息 ID（WinUser.h）
WM_QUERYENDSESSION = 0x0011  # 系统询问「是否可以结束会话」；返回 1=允许
WM_ENDSESSION = 0x0016       # 会话已结束/正在结束（wParam=1 时）

# 顶层窗口类名
_HIDDEN_CLASS = "CampusNoticeShutdownHook"

# GWL_WNDPROC 用于窗口子类化（备用路径，当前未使用，保留扩展位）
_GWL_WNDPROC = -4


def _load_user32() -> Any:
    return ctypes.windll.user32


def _load_kernel32() -> Any:
    return ctypes.windll.kernel32


# WNDCLASSW 结构（ctypes.wintypes 未内置，需自定义；与 WinUser.h 字段顺序一致）
_HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)  # 缺失时等价 HANDLE


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", _HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def handle_message(
    hwnd: int,
    msg: int,
    wparam: int,
    lparam: int,
    on_shutdown: Callable[[], Any] | None,
    on_endsession: Callable[[], Any] | None,
    def_proc: Callable[..., int] | None = None,
) -> int:
    """把一条 Windows 消息分发给处理逻辑（供 WndProc 与测试直调复用）。

    - ``WM_QUERYENDSESSION`` → 调用 ``on_shutdown``（触发壳统一退出序列），
      返回 1（允许系统继续结束会话）；
    - ``WM_ENDSESSION``      → ``wparam==1`` 时调用 ``on_endsession``（此时系统
      已确认要结束会话，做兜底收尾），返回 0；
    - 其他消息 → 交给 ``def_proc``（缺省返回 0）。

    Returns:
        int：Windows LRESULT，作为 WndProc 的返回值。
    """
    if msg == WM_QUERYENDSESSION:
        if on_shutdown is not None:
            try:
                on_shutdown()
            except Exception:  # noqa: BLE001 - 关机回调异常不得阻断系统关机
                logger.exception("WM_QUERYENDSESSION 回调异常（忽略，放行关机）")
        return 1  # TRUE：允许关机/注销
    if msg == WM_ENDSESSION:
        if wparam and on_endsession is not None:
            try:
                on_endsession()
            except Exception:  # noqa: BLE001
                logger.exception("WM_ENDSESSION 回调异常（忽略）")
        return 0
    if def_proc is not None:
        return def_proc(hwnd, msg, wparam, lparam)
    return 0


class ShutdownHook:
    """隐藏顶层窗口 + 消息泵线程，拦截系统关机/注销广播（K5）。

    用法：
        hook = ShutdownHook(on_shutdown=self._on_system_shutdown)
        if hook.start():          # 成功（仅 Windows）
            ...
        # 退出时（可选，壳收尾会 stop）：hook.stop()

    线程模型：隐藏窗口创建并运行在**独立 daemon 线程**（该线程跑 GetMessage
    消息泵），这样系统广播的 SendMessage 能被正确处理（跨线程 SendMessage
    依赖目标线程的队列泵送）。``on_shutdown`` 在消息泵线程被调用，需线程安全
    （壳会把实际重活调度回主线程 / 或只做轻量收尾，见 app.py）。
    """

    def __init__(
        self,
        on_shutdown: Callable[[], Any] | None = None,
        on_endsession: Callable[[], Any] | None = None,
    ) -> None:
        self._on_shutdown = on_shutdown
        self._on_endsession = on_endsession
        self._hwnd: int | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._running = False
        # 保持 WNDPROC 引用（ctypes 回调对象需避免被 GC）
        self._wndproc_ref: Any = None

    @property
    def running(self) -> bool:
        """钩子是否已成功启动（仅 Windows 有效）。"""
        return self._running

    @property
    def hwnd(self) -> int | None:
        """隐藏窗口句柄（测试直发消息时可用）。"""
        return self._hwnd

    # ---------- 启动 / 停止 ----------
    def start(self) -> bool:
        """启动隐藏窗口 + 消息泵线程。非 Windows / 失败返回 False（静默降级）。"""
        if self._running:
            return True
        if not self._is_windows():
            logger.debug("关机钩子：非 Windows 平台，跳过（ShutdownHook 降级）")
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_pump,
            name="desktop-shutdown-hook",
            daemon=True,
        )
        self._thread.start()
        # 等待窗口创建完成（给 _run_pump 一点时间建窗并回填 hwnd）
        for _ in range(100):
            if self._running or self._stop.is_set():
                break
            threading.Event().wait(0.02)
        if not self._running:
            logger.warning("关机钩子窗口创建失败，降级（系统关机时数据风险升高）")
            return False
        logger.info("关机钩子已启动（隐藏窗口 hwnd=%s）", self._hwnd)
        return True

    def stop(self) -> None:
        """停止消息泵线程并销毁隐藏窗口（幂等）。"""
        self._stop.set()
        if self._hwnd:
            try:
                _load_user32().PostMessageW(wintypes.HWND(self._hwnd), 0x0012, 0, 0)  # WM_QUIT
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        self._running = False
        self._hwnd = None

    # ---------- 内部 ----------
    @staticmethod
    def _is_windows() -> bool:
        import sys

        return sys.platform == "win32"

    def _run_pump(self) -> None:
        """线程目标：注册窗口类 → 创建隐藏顶层窗口 → 消息泵。"""
        user32 = _load_user32()
        kernel32 = _load_kernel32()
        try:
            hinst = kernel32.GetModuleHandleW(None)
            # WNDPROC 回调（ctypes 需持有引用）
            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_long, wintypes.HWND, ctypes.c_uint,
                wintypes.WPARAM, wintypes.LPARAM,
            )
            self._wndproc_ref = WNDPROC(self._wnd_proc)

            wc = _WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
            wc.cbClsExtra = 0
            wc.cbWndExtra = 0
            wc.hInstance = hinst
            wc.hIcon = None
            wc.hCursor = None
            wc.hbrBackground = None
            wc.lpszMenuName = None
            wc.lpszClassName = _HIDDEN_CLASS

            # 设置 user32 函数签名（GetLastError / 指针参数需要）
            user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
            user32.RegisterClassW.restype = wintypes.ATOM
            user32.GetClassInfoW.argtypes = [
                wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.POINTER(_WNDCLASSW),
            ]
            user32.GetClassInfoW.restype = wintypes.BOOL
            user32.CreateWindowExW.argtypes = [
                ctypes.c_uint, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_uint,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
            ]
            user32.CreateWindowExW.restype = wintypes.HWND

            atom = user32.RegisterClassW(ctypes.byref(wc))
            if atom == 0:
                # 类已存在（同进程重复启动）则直接使用，否则失败
                if user32.GetClassInfoW(hinst, _HIDDEN_CLASS, ctypes.byref(wc)) == 0:
                    logger.warning("关机钩子窗口类注册失败（error=%s）",
                                   kernel32.GetLastError())
                    self._stop.set()
                    return
            # 创建隐藏顶层窗口：WS_OVERLAPPED 且不显示，仍属顶层窗口，可收广播
            hwnd = user32.CreateWindowExW(
                0, _HIDDEN_CLASS, _HIDDEN_CLASS,
                0x00000000,  # WS_OVERLAPPED（隐藏，不显示）
                0, 0, 0, 0,
                wintypes.HWND(0), wintypes.HMENU(0), hinst, None,
            )
            if not hwnd:
                logger.warning("关机钩子窗口创建失败（error=%s）", kernel32.GetLastError())
                self._stop.set()
                return
            self._hwnd = int(hwnd)
            self._running = True
        except Exception:  # noqa: BLE001 - 钩子创建失败降级，不影响启动
            logger.exception("关机钩子创建异常，降级")
            self._stop.set()
            return

        # 消息泵：GetMessageW 直到收到 WM_QUIT
        msg = wintypes.MSG()
        while not self._stop.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), wintypes.HWND(0), 0, 0)
            if ret <= 0:  # 0=WM_QUIT，-1=错误
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        """隐藏窗口的 WndProc：把系统消息交给 handle_message 分派。"""
        user32 = _load_user32()
        try:
            user32.DefWindowProcW.argtypes = [
                wintypes.HWND, ctypes.c_uint,
                wintypes.WPARAM, wintypes.LPARAM,
            ]
            user32.DefWindowProcW.restype = ctypes.c_long
            def_proc = user32.DefWindowProcW
        except Exception:  # noqa: BLE001
            def_proc = None
        return handle_message(
            hwnd, msg, wparam, lparam,
            on_shutdown=self._on_shutdown,
            on_endsession=self._on_endsession,
            def_proc=def_proc if def_proc else None,
        )
