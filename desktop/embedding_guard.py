"""嵌入模型空闲释放守卫（B21.T2，内存优化）。

背景：本地 bge 嵌入模型（HuggingFace）加载后私有内存可达 ~1.7GB（B01 实测）。
首次问答/检索会加载它（懒加载天然成立，启动不预载）；但加载后若无任何机制，
模型会一直驻留，导致空闲内存居高不下（D-51「长跑无增长」无法保证）。

本模块提供一个轻量 daemon 监控线程：轮询用户空闲时长（复用 desktop.idle.
IdleTracker），当空闲连续超过阈值（默认 30 分钟，可配）且本地嵌入模型已加载
时，调用 ``utils.embedding.release_embeddings()`` + ``VectorIndex.release_
embeddings()`` 解除强引用，使模型可被 GC 回收，空闲内存回落。

释放后下次问答/检索经 ``get_embeddings()`` 自动按需重新加载，不影响功能
（冷加载延迟仅在恢复使用的那一次问答出现，属可接受权衡）。

设计要点：
  - 独立于 IdleGate（调度器重活 gate），职责单一：只做内存释放；
  - 叶子模块，不 import webview/pystray 壳，可在测试与纯后端安全 import；
  - 仅本地嵌入模型才释放；云端 embedding（OpenAI-compatible）无本地占用，
    release_embeddings() 内部自判并返回 False，不产生副作用。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 默认空闲释放阈值（秒）：连续空闲该时长后释放本地嵌入模型
DEFAULT_EMBEDDING_IDLE_RELEASE_SECONDS = 1800  # 30 分钟
# 轮询间隔（秒）
POLL_INTERVAL_SECONDS = 60.0


class EmbeddingIdleGuard:
    """空闲触发本地嵌入模型释放的 daemon 监控线程。"""

    def __init__(
        self,
        idle_tracker: Optional[object] = None,
        idle_threshold_seconds: Optional[int] = None,
        release_fn: Optional[Callable[[], bool]] = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ):
        """Args:
            idle_tracker: 提供 ``idle_seconds()`` 与 ``available`` 的对象；None 时
                惰性构造 desktop.idle.IdleTracker。
            idle_threshold_seconds: 空闲释放阈值（秒）。None 用默认 30 分钟。
            release_fn: 实际释放回调；None 时用默认实现（release_embeddings +
                VectorIndex.release_embeddings）。
            poll_interval: 轮询间隔（秒）。
        """
        self._tracker = idle_tracker
        self._threshold = float(
            idle_threshold_seconds or DEFAULT_EMBEDDING_IDLE_RELEASE_SECONDS
        )
        self._release_fn = release_fn or self._default_release
        self._poll_interval = float(poll_interval)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._released = False  # 本空闲周期是否已释放（避免反复释放）
        self._last_released_at: Optional[float] = None

    def _get_tracker(self):
        """惰性构造 IdleTracker；失败降级为「始终活跃」对象。"""
        if self._tracker is not None:
            return self._tracker
        try:
            from desktop.idle import IdleTracker

            return IdleTracker()
        except Exception:  # noqa: BLE001 - 空闲检测不可用则永不触发释放
            logger.debug("EmbeddingIdleGuard 空闲检测不可用，降级为不释放", exc_info=True)

            class _NeverIdle:
                available = False

                def idle_seconds(self) -> float:
                    return 0.0

            return _NeverIdle()

    @staticmethod
    def _default_release() -> bool:
        """默认释放实现：清空 embedding 模块缓存 + 解 VectorIndex 强引用。"""
        from utils.embedding import release_embeddings

        released = release_embeddings()
        if released:
            try:
                from storage.vectorstore import get_vector_index

                get_vector_index().release_embeddings()
            except Exception:  # noqa: BLE001 - 索引解引用失败不影响核心释放
                logger.debug("VectorIndex 解引用失败（模块缓存已清空）", exc_info=True)
        return released

    def start(self) -> bool:
        """启动 daemon 监控线程；重复调用幂等。返回是否已启动。"""
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="embedding-idle-guard",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "嵌入模型空闲释放守卫已启动（空闲阈值 %ds，轮询 %.0fs）",
            self._threshold,
            self._poll_interval,
        )
        return True

    def stop(self) -> None:
        """停止监控线程（幂等）。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        tracker = self._get_tracker()
        while not self._stop.wait(self._poll_interval):
            try:
                idle_sec = tracker.idle_seconds()
                if idle_sec >= self._threshold:
                    # 达到空闲阈值且本周期未释放 → 释放
                    if not self._released:
                        released = self._release_fn()
                        self._released = released
                        if released:
                            self._last_released_at = time.time()
                            logger.info(
                                "已释放本地嵌入模型（空闲 %ds ≥ 阈值 %ds）",
                                int(idle_sec),
                                int(self._threshold),
                            )
                else:
                    # 用户活跃：重置释放标记，允许下一个空闲周期再次释放
                    self._released = False
            except Exception:  # noqa: BLE001 - 单次轮询异常不影响线程存活
                logger.debug("嵌入模型空闲释放守卫轮询异常", exc_info=True)

    def status(self) -> dict:
        """当前状态（供壳状态展示 / 调试）。"""
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "idle_threshold_seconds": self._threshold,
            "released": self._released,
            "last_released_at": self._last_released_at,
        }
