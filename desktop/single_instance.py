"""单实例锁与已有实例唤起（B07 / DESKTOP-UPGRADE.md §6 K6）。

目标：防双开写坏 SQLite（R7）。首启即加锁；锁通道同时承担「唤起已有实例」
职责——第二个实例检测到已有实例后，向主实例发送 ``activate`` 指令并立即退出。

实现策略（两级）：
  1. **socket 端口锁**（首选，跨平台统一且天然带唤醒通道）：
     启动时尝试绑定固定高位端口（默认 51999）。绑定成功 → 本实例为主实例，
     开启监听线程，收到 ``activate`` → 触发 ``on_activate`` 回调
     （``window.restore() + show() + focus()``）。
     绑定失败 → 有两种可能：
       a. 端口被**本应用**已有实例占用 → 连接该端口发 ``activate``，对方回
          ``ACK`` → 判定已有实例，立即退出（不双开）；
       b. 端口被**无关程序**占用 → 对端不按协议响应 → 降级到文件锁兜底。
  2. **文件锁兜底**（端口被无关程序占用时）：
     Windows 侧用 ``msvcrt.locking``（文件区域锁）；POSIX 用 ``fcntl.flock``。
     拿到锁 → 本实例为主实例（文件锁模式）；拿不到 → 判定已有实例，退出。

退出语义：``acquire()`` 返回 True 表示本实例是主实例；返回 False 表示已有
实例（此时已尝试唤起主实例，调用方应立即退出，退出码 0）。

回滚点：本模块独立，``DesktopApp`` 中通过 ``enable_single_instance`` 开关可
整体禁用（回退为可双开，但 R7 风险回归）。

被 test_desktop_single_instance.py 契约引用的接口（勿随意改名）：
  - ``SingleInstanceLock``  （类）
  - ``send_activate``       （模块级函数：向锁端口发 activate 并等 ACK）
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable

from utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

# 固定高位锁端口（K6）。模块级常量可被测试改端口做隔离。
DEFAULT_PORT = 51999

# 监听 backlog
_BACKLOG = 5
# 与已有实例握手 / 唤起指令的短超时（秒）：避免被无关程序占用的端口拖住启动
_HANDSHAKE_TIMEOUT = 1.0

# 指令协议：第二实例向主实例发送该行请求唤起
CMD_ACTIVATE = "activate"
# 主实例确认收到唤起的响应
ACK = "ACK"

# 文件锁默认落点（data/ 目录可写，与运行时文件同级）
DEFAULT_LOCK_PATH = get_data_dir() / "single_instance.lock"


# ---------------------------------------------------------------------------
# 文件锁（兜底）
# ---------------------------------------------------------------------------
class _FileLock:
    """基于文件区域锁/建议锁的互斥（Windows msvcrt.locking，POSIX fcntl.flock）。

    拿到即代表「我是主实例」；拿不到说明已有实例持锁。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: Any = None
        self._locked = False

    @property
    def is_locked(self) -> bool:
        """当前是否持有文件锁。"""
        return self._locked

    def acquire(self) -> bool:
        """尝试加文件锁。成功返回 True（成为主实例），否则 False。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # 以追加模式打开：不截断、保留跨进程共享语义
            fh = open(self._path, "a+", encoding="utf-8")  # noqa: SIM115 - 全程持有句柄
            if os.name == "nt":  # Windows：msvcrt.locking
                import msvcrt

                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    fh.close()
                    return False
            else:  # POSIX：fcntl.flock 非阻塞
                import fcntl

                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    fh.close()
                    return False
            self._fh = fh
            self._locked = True
            return True
        except Exception:  # noqa: BLE001 - 文件锁不可用则视作无法取得主实例
            try:
                fh = locals().get("fh")
                if fh is not None:
                    fh.close()
            except Exception:  # noqa: BLE001
                pass
            return False

    def release(self) -> None:
        """释放文件锁并关闭句柄。"""
        if self._fh is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    try:
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl

                    try:
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    self._fh.close()
                except Exception:  # noqa: BLE001
                    pass
                self._fh = None
        self._locked = False


# ---------------------------------------------------------------------------
# 唤起指令发送（第二实例 → 主实例）
# ---------------------------------------------------------------------------
def send_activate(port: int = DEFAULT_PORT, timeout: float = _HANDSHAKE_TIMEOUT) -> bool:
    """向锁端口发送 ``activate`` 指令并等待 ``ACK``。

    Returns:
        True：端口上有本应用主实例（收到 ACK）；
        False：对端无响应/响应非预期（端口可能被无关程序占用，或无可达实例）。
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall((CMD_ACTIVATE + "\n").encode("utf-8"))
            data = s.recv(64)
            return data.strip().decode("utf-8", "ignore") == ACK
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 单实例锁对象
# ---------------------------------------------------------------------------
class SingleInstanceLock:
    """单实例互斥锁：socket 端口锁 + 文件锁兜底（K6）。

    用法：
        lock = SingleInstanceLock()
        if not lock.acquire(on_activate=my_restore_cb):
            # 已有实例（已尝试唤起它），调用方应 sys.exit(0)
            return
        # ... 本实例是主实例，正常启动 ...
        # 退出时：
        lock.release()

    线程模型：``on_activate`` 在独立监听 daemon 线程被调用，需线程安全；
    唤起主窗口等操作用户应确保其可跨线程执行（pywebview 的 window 方法
    通常线程安全；与托盘回调同源）。
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        lock_path: Path | None = None,
    ) -> None:
        self.port = port
        self.lock_path = lock_path or DEFAULT_LOCK_PATH
        self._on_activate: Callable[[], Any] | None = None
        self._socket: socket.socket | None = None
        self._file_lock = _FileLock(self.lock_path)
        self._listener: threading.Thread | None = None
        self._stop = threading.Event()
        self._acquired = False
        self._using_socket = False
        self._using_file_lock = False

    # ---------- 只读属性 ----------
    @property
    def acquired(self) -> bool:
        """本实例是否持有锁（是主实例）。"""
        return self._acquired

    @property
    def using_socket(self) -> bool:
        """是否通过 socket 端口锁成为主实例。"""
        return self._using_socket

    @property
    def using_file_lock(self) -> bool:
        """是否通过文件锁兜底成为主实例。"""
        return self._using_file_lock

    # ---------- 加锁 ----------
    def acquire(self, on_activate: Callable[[], Any] | None = None) -> bool:
        """尝试取得单实例锁。

        采用「socket 端口锁 + 身份文件锁」两级互斥：
          - 主实例无论走 socket 还是文件锁路径，都会**持有文件锁**（身份锁），
            作为第二实例在 socket 判定失败时的全局互斥后备；
          - 第二实例 socket 绑定失败后，先尝试唤起已有实例，再判定是否已有实例。

        Args:
            on_activate: 收到「已有实例唤起」指令时回调（仅主实例注册）。

        Returns:
            True：本实例成为主实例，可继续启动；
            False：已有实例（已尝试唤起它），调用方应立即退出。
        """
        if self._acquired:
            return True
        self._on_activate = on_activate

        # 1. 尝试 socket 端口锁（成功 → 本实例拥有监听通道）
        socket_ok = self._try_socket_acquire()
        # 2. 身份文件锁：主实例恒持有，作为 socket 判定失败时的全局互斥后备
        file_ok = self._file_lock.acquire()

        if socket_ok and file_ok:
            # 正常路径：socket 锁 + 文件锁均取得 → 主实例（socket 模式）
            self._acquired = True
            self._using_socket = True
            logger.info("单实例锁已取得（socket 端口 %d + 文件锁）", self.port)
            return True

        if file_ok and not socket_ok:
            # 端口被无关程序占用，但拿到身份文件锁 → 本实例是主实例（文件锁兜底）
            self._acquired = True
            self._using_file_lock = True
            logger.warning("单实例锁已取得（文件锁兜底 %s）", self.lock_path)
            return True

        # 到这里：主实例身份未取得。
        #  - socket_ok=True 但 file_ok=False：端口空闲却拿不到文件锁
        #    → 存在另一个走文件锁兜底的主实例 → 释放刚绑定的 socket，判为已有实例。
        #  - 均失败：socket 端口被占 + 文件锁被占 → 已有实例。
        if socket_ok:
            self._release_socket()
        if send_activate(self.port):
            logger.info("检测到已有实例（端口 %d 有主实例），已发送唤起指令", self.port)
        else:
            logger.warning("单实例锁获取失败（socket 与文件锁均不可得），判定已有实例")
        return False

    def _try_socket_acquire(self) -> bool:
        """尝试把 127.0.0.1:port 绑定为本实例的监听 socket。成功返回 True。"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", self.port))
            s.listen(_BACKLOG)
        except OSError:
            # 端口被占用（无论本应用还是无关程序）→ 释放并交给上层判定
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
            return False
        self._socket = s
        self._stop.clear()
        self._listener = threading.Thread(
            target=self._listen_loop,
            name=f"single-instance-listener-{self.port}",
            daemon=True,
        )
        self._listener.start()
        return True

    def _release_socket(self) -> None:
        """停监听线程并关闭监听 socket（acquire 失败路径 / release 复用）。"""
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._listener is not None and self._listener.is_alive():
            self._listener.join(timeout=2)
        self._listener = None

    # ---------- 监听 ----------
    def _listen_loop(self) -> None:
        """主实例监听线程：接受连接、读取指令、触发唤起回调。"""
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._socket.accept()
            except OSError:
                if self._stop.is_set():
                    break
                # 非停止导致的 accept 失败：短暂等待后重试，避免忙循环
                self._stop.wait(0.1)
                continue
            with conn:
                try:
                    conn.settimeout(_HANDSHAKE_TIMEOUT)
                    data = conn.recv(64).decode("utf-8", "ignore").strip()
                    if data == CMD_ACTIVATE:
                        logger.info("收到已有实例的唤起指令（activate）")
                        conn.sendall((ACK + "\n").encode("utf-8"))
                        self._notify_activate()
                except OSError:
                    logger.debug("监听连接读取异常（忽略）", exc_info=True)

    def _notify_activate(self) -> None:
        """在监听线程内触发唤起回调（restore + show + focus）。"""
        if self._on_activate is None:
            logger.debug("未注册唤起回调，忽略 activate")
            return
        try:
            self._on_activate()
        except Exception:  # noqa: BLE001 - 唤起失败不应拖垮监听线程
            logger.exception("唤起主窗口回调异常")

    # ---------- 释放 ----------
    def release(self) -> None:
        """释放锁：停监听线程、关闭 socket、释放文件锁（若有）。"""
        self._release_socket()
        if self._using_file_lock or self._file_lock.is_locked:
            self._file_lock.release()
            logger.info("单实例文件锁已释放")
        self._acquired = False
        self._using_socket = False
        self._using_file_lock = False
