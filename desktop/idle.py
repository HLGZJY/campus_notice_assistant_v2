"""空闲检测与重活 gate（B14 / DESKTOP-UPGRADE.md §4.2 idle.py、§3.4）。

目标（DESKTOP-BATCH-PLAN.md B14）：
  - 空闲 5 分钟（可配）才跑抓取/批量提取这类重活；
  - 检测到用户输入后 1s 内挂起重活；
  - 避免合盖 / 睡眠 / 虚拟机下的误判（R11 唤醒补偿 + 电量感知）。

两个组件：
  1. ``IdleTracker``：查询距上次用户输入（键盘/鼠标）的空闲秒数。
     - Windows：``GetLastInputInfo``（ctypes，约 30 行，无第三方依赖）。
     - 非 Windows / API 不可用：``available=False``，``idle_seconds()`` 恒返回 0
       ——视为「始终活跃」，即永不因此触发重活（保守，不阻塞用户）。
  2. ``IdleGate``：daemon 监控线程，把 IdleTracker 的空闲状态翻译为对调度器
     ``pause_heavy() / resume_heavy()`` 的调用（只挂起重活，不动 daily/reminder）。

设计要点（R11）：
  - 睡眠唤醒补偿：若采样间隔内墙钟发生大幅跳变（合盖/睡眠），判定经历唤醒，
    把「最近活跃时刻」重置为唤醒时刻——避免唤醒瞬间被误判为「已空闲 5 分钟」
    而立刻跑重活；
  - 电量感知：电池供电且电量过低（默认 <20%）时抑制 resume_heavy（省电）；
    虚拟机下电源 API 不可用则降级为「不因电量阻塞」；
  - 所有系统 API 均以「失败即保守降级」处理：宁可少跑重活，不可误判打扰用户。

线程模型：IdleGate 在独立 daemon 线程轮询，不阻塞主线程；对调度器的调用轻量
（只改 APScheduler job 状态），不在长任务里做。
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 默认：空闲满 5 分钟才恢复重活
DEFAULT_IDLE_HEAVY_SECONDS = 300
# 电量阈值（%）：电池供电且低于该值时不恢复重活（省电）
LOW_BATTERY_PERCENT = 20
# 判定「经历睡眠」的墙钟跳变阈值（倍于轮询间隔）与最小跳变秒数
SLEEP_JUMP_FACTOR = 5.0
SLEEP_MIN_JUMP_SECONDS = 30.0


# ---------------------------------------------------------------------------
# IdleTracker：Win GetLastInputInfo
# ---------------------------------------------------------------------------
class IdleTracker:
    """查询距上次用户输入的空闲秒数（Windows GetLastInputInfo，ctypes）。

    ``available``：是否可检测空闲（Windows 且 API 可用）。不可用（macOS /
    Linux / 虚拟机缺少 API / 非 Win）时 ``idle_seconds()`` 恒返回 0，即视为
    「始终活跃」——调用方永不因此触发重活（保守）。

    用法：
        tracker = IdleTracker()
        if tracker.available:
            seconds = tracker.idle_seconds()
    """

    def __init__(self) -> None:
        self.available = False
        self._last_input_info = None
        self._user32 = None
        self._get_tick_count = None
        try:
            import sys

            if sys.platform != "win32":
                return
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            last_input_info = type(
                "LASTINPUTINFO",
                (ctypes.Structure,),
                [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)],
            )()
            last_input_info.cbSize = ctypes.sizeof(last_input_info)
            self._user32 = user32
            self._last_input_info = last_input_info
            self._get_tick_count = ctypes.windll.kernel32.GetTickCount  # type: ignore[attr-defined]
            self.available = True
        except Exception:  # noqa: BLE001 - 任何初始化失败都降级为不可检测
            logger.debug("GetLastInputInfo 不可用，空闲检测降级为始终活跃", exc_info=True)
            self.available = False

    def idle_seconds(self) -> float:
        """距上次键盘/鼠标输入的秒数。不可用时返回 0（视为始终活跃）。"""
        if not self.available:
            return 0.0
        try:
            ok = self._user32.GetLastInputInfo(ctypes.byref(self._last_input_info))
            if not ok:
                return 0.0
            now = self._get_tick_count()
            elapsed = (now - self._last_input_info.dwTime) & 0xFFFFFFFF  # 32 位回绕处理
            return max(0.0, elapsed / 1000.0)
        except Exception:  # noqa: BLE001 - 查询失败保守返回 0（视为活跃）
            logger.debug("GetLastInputInfo 查询失败（降级为活跃）", exc_info=True)
            return 0.0


# ---------------------------------------------------------------------------
# 电量感知（B14.T4）
# ---------------------------------------------------------------------------
class PowerState:
    """查询电源状态（GetSystemPowerStatus）。

    仅用于「电池供电 + 低电量时抑制重活」（省电）。任何查询失败都保守降级：
    ``on_battery()`` / ``battery_percent()`` 返回 None，调用方视为「不因电量阻塞」。
    """

    def __init__(self) -> None:
        self._power_status = None
        self._kernel32 = None
        try:
            import sys

            if sys.platform != "win32":
                return
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            power_status = type(
                "SYSTEM_POWER_STATUS",
                (ctypes.Structure,),
                [
                    ("ACLineStatus", ctypes.c_byte),
                    ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte),
                    ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_uint),
                    ("BatteryFullLifeTime", ctypes.c_uint),
                ],
            )()
            self._kernel32 = kernel32
            self._power_status = power_status
        except Exception:  # noqa: BLE001 - 电源 API 不可用（虚拟机等）降级
            logger.debug("GetSystemPowerStatus 不可用，电量感知降级", exc_info=True)

    def on_battery(self) -> Optional[bool]:
        """是否电池供电；无法判断返回 None。ACLineStatus=1 为市电。"""
        if self._power_status is None:
            return None
        try:
            ok = self._kernel32.GetSystemPowerStatus(ctypes.byref(self._power_status))
            if not ok:
                return None
            return self._power_status.ACLineStatus == 0
        except Exception:  # noqa: BLE001
            return None

    def battery_percent(self) -> Optional[int]:
        """电池电量百分比（0~100）；未知/市电/失败返回 None。"""
        if self._power_status is None:
            return None
        try:
            ok = self._kernel32.GetSystemPowerStatus(ctypes.byref(self._power_status))
            if not ok:
                return None
            pct = self._power_status.BatteryLifePercent
            if pct == 255:  # 未知
                return None
            return int(pct)
        except Exception:  # noqa: BLE001
            return None

    def low_battery(self, threshold: int = LOW_BATTERY_PERCENT) -> bool:
        """是否电池供电且电量低于阈值（应抑制重活）。

        电池状态无法判断（None）时返回 False——不因电量阻塞（保守放行）。
        """
        on_batt = self.on_battery()
        pct = self.battery_percent()
        if on_batt is None or pct is None:
            return False
        return bool(on_batt and pct < threshold)


# ---------------------------------------------------------------------------
# IdleGate：空闲 → 恢复重活；活跃 → 挂起重活
# ---------------------------------------------------------------------------
class IdleGate:
    """daemon 监控线程：把空闲状态翻译为对调度器重活 pause_heavy/resume_heavy。

    触发规则：
      - 空闲时长 >= 阈值（默认 300s，可配）且当前重活被挂起 → ``resume_heavy()``；
      - 用户恢复输入（空闲回落）→ 1s 内 ``pause_heavy()``（挂起重活）。
      - 睡眠唤醒后需**重新**空闲满阈值才恢复重活（R11 唤醒补偿）；
      - 电池低电量时抑制恢复重活（省电）。

    Args:
        get_scheduler: 无参可调用，返回调度器实例（有 pause_heavy/resume_heavy）或 None。
        idle_threshold_seconds: 空闲阈值（秒）。None 用 DEFAULT_IDLE_HEAVY_SECONDS。
        poll_interval: 轮询间隔（秒），默认 1.0。越小「1s 内挂起」越灵敏。
        tracker: 可注入的 IdleTracker（测试用）。
        power: 可注入的 PowerState（测试用）。
    """

    def __init__(
        self,
        get_scheduler: Callable[[], Any],
        idle_threshold_seconds: Optional[int] = None,
        poll_interval: float = 1.0,
        tracker: Optional[IdleTracker] = None,
        power: Optional[PowerState] = None,
    ) -> None:
        self._get_scheduler = get_scheduler
        self._threshold = float(idle_threshold_seconds or DEFAULT_IDLE_HEAVY_SECONDS)
        self._poll_interval = max(0.2, float(poll_interval))
        self._tracker = tracker if tracker is not None else IdleTracker()
        self._power = power if power is not None else PowerState()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_wall = time.time()  # 上次采样墙钟
        self._wake_at: Optional[float] = None  # 最近一次唤醒时刻（睡眠补偿）
        self._was_idle = False  # 上一轮是否处于「已空闲满阈值」态
        self._enabled = True  # 检测开关（可整体禁用）

    # ---------- 生命周期 ----------
    def start(self) -> None:
        """启动监控 daemon 线程（幂等）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_wall = time.time()
        self._thread = threading.Thread(
            target=self._loop,
            name="desktop-idle-gate",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "空闲重活 gate 已启动（阈值 %ds，轮询 %.0fs，空闲检测可用=%s）",
            self._threshold, self._poll_interval, self._tracker.available,
        )

    def stop(self) -> None:
        """停止监控线程（幂等）。"""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def set_enabled(self, enabled: bool) -> None:
        """开关检测（设置页可配）；关闭时立即恢复重活，避免误挂起。"""
        self._enabled = bool(enabled)
        if not self._enabled:
            logger.info("空闲重活 gate 已禁用，恢复重活")
            sched = self._scheduler()
            if sched is not None:
                try:
                    sched.resume_heavy()
                except Exception:  # noqa: BLE001
                    logger.debug("禁用 gate 时恢复重活失败（忽略）", exc_info=True)

    # ---------- 对外只读 ----------
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def threshold(self) -> float:
        return self._threshold

    def status(self) -> dict:
        """供壳状态展示：是否运行/阈值/空闲检测可用性。"""
        return {
            "running": self.is_running(),
            "enabled": self._enabled,
            "threshold_seconds": int(self._threshold),
            "tracker_available": self._tracker.available,
            "idle_seconds": int(self._tracker.idle_seconds()),
        }

    # ---------- 内部 ----------
    def _scheduler(self) -> Any:
        try:
            return self._get_scheduler()
        except Exception:  # noqa: BLE001 - 取调度器失败视为无调度器
            logger.debug("取调度器失败（IdleGate 视为无调度器）", exc_info=True)
            return None

    def _loop(self) -> None:
        """轮询循环：空闲 gate。"""
        while not self._stop.is_set():
            if self._stop.wait(self._poll_interval):
                break
            if not self._enabled:
                continue
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - 单轮异常不影响后续轮询
                logger.debug("IdleGate 单轮检测异常（忽略）", exc_info=True)

    def _tick(self) -> None:
        now = time.time()
        wall_gap = now - self._last_wall
        self._last_wall = now
        idle = self._tracker.idle_seconds()
        sched = self._scheduler()

        # 1. 睡眠唤醒补偿（R11）：墙钟大幅跳变（合盖/睡眠）→ 判定刚唤醒，
        #    重置空闲计时器：唤醒后需重新空闲满阈值才恢复重活，避免唤醒瞬间
        #    GetLastInputInfo 返回超大空闲值被误判为「已空闲 5 分钟」而立刻跑重活。
        if wall_gap > max(SLEEP_MIN_JUMP_SECONDS, self._poll_interval * SLEEP_JUMP_FACTOR):
            logger.info("检测到时间跳变（%.0fs，可能睡眠唤醒），重置空闲计时", wall_gap)
            self._wake_at = now
            self._was_idle = False
            return

        # 2. 电量感知：电池低电量时抑制恢复重活（省电）。
        low_batt = self._power.low_battery()

        # 3. 空闲 gate：
        if idle >= self._threshold and not low_batt:
            # 已空闲满阈值 → 若刚唤醒且唤醒后未满阈值，需再观察一轮。
            if self._wake_at is not None and (now - self._wake_at) < self._threshold:
                self._was_idle = False
                return
            self._wake_at = None
            if not self._was_idle and sched is not None:
                try:
                    if sched.heavy_paused:
                        sched.resume_heavy()
                        logger.info("空闲 %ds >= 阈值 %ds，恢复重活", int(idle), self._threshold)
                    else:
                        logger.debug("已空闲且重活未挂起，无需动作")
                except Exception:  # noqa: BLE001
                    logger.debug("恢复重活失败（忽略）", exc_info=True)
            self._was_idle = True
        else:
            # 用户活跃（或低电量）→ 若上一轮曾空闲满阈值，1s 内挂起重活。
            if self._was_idle and sched is not None:
                try:
                    if not sched.heavy_paused:
                        sched.pause_heavy()
                        logger.info("检测到用户输入（空闲 %ds < 阈值 %ds），挂起重活", int(idle), self._threshold)
                    else:
                        logger.debug("重活已挂起，无需重复动作")
                except Exception:  # noqa: BLE001
                    logger.debug("挂起重活失败（忽略）", exc_info=True)
            self._was_idle = False
