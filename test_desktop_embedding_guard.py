"""B21.T2 嵌入模型空闲释放守卫验收（离线）。

对应 docs/DESKTOP-UPGRADE.md A6/B01 结论与 B21.T2：
  - 启动**不预加载** bge（懒加载天然成立）——空闲态内存低；
  - 首次问答/检索加载本地 bge（嵌入态 ~1.7GB）；
  - 空闲超阈值后应**主动释放**强引用，使内存回落（D-51「长跑无增长」）。

本脚本覆盖：
  1. 未加载本地模型时 release_embeddings() 返回 False（云端/未加载，无副作用）
  2. 加载本地模型后 release_embeddings() 返回 True（确会释放）
  3. VectorIndex.release_embeddings() 解除 embedding 与 store 强引用
  4. EmbeddingIdleGuard：空闲达阈值触发释放；活跃不释放
  5. Guard start/stop 幂等

安全：不触碰生产库 / 不实际加载真实 bge（用轻量 fake embedding 验证逻辑）。

依赖：utils.embedding.release_embeddings + storage.vectorstore.VectorIndex +
      desktop.embedding_guard.EmbeddingIdleGuard（B21.T2 实现后自动激活）。

用法：python test_desktop_embedding_guard.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B21", module="utils.embedding", title="嵌入模型空闲释放守卫（B21.T2）")


class _FakeTracker:
    """可编程 idle 秒数，替代真实 GetLastInputInfo，便于测试触发/不触发。"""

    available = True
    seconds = 0.0

    def idle_seconds(self) -> float:
        return self.seconds


@suite.case("1. 未加载本地模型时 release_embeddings() 返回 False（无副作用）")
def _(mod):
    import utils.embedding as emb

    release = suite.require(emb, "release_embeddings", "B21.T2 新增空闲释放")
    emb._EMBEDDING_CACHE = None
    assert release() is False, "未加载本地模型应返回 False"


@suite.case("2. 加载本地模型后 release_embeddings() 返回 True")
def _(mod):
    import utils.embedding as emb

    release = suite.require(emb, "release_embeddings")

    class _FakeLocal:
        pass

    # 本地模型包装是 _CountingEmbeddings（内层 HuggingFace）
    fake = emb._CountingEmbeddings(_FakeLocal(), "fake-model", "local")
    emb._EMBEDDING_CACHE = fake
    assert release() is True, "持有本地模型应返回 True"
    assert emb._EMBEDDING_CACHE is None, "释放后模块缓存应为空"


@suite.case("3. VectorIndex.release_embeddings() 解除强引用")
def _(mod):
    from storage.vectorstore import VectorIndex

    suite.require(VectorIndex, "release_embeddings", "B21.T2 VectorIndex 增释放方法")
    idx = VectorIndex.__new__(VectorIndex)
    idx._embedding = object()
    idx._store = object()
    had = idx.release_embeddings()
    assert had is True, "持有 embedding 时应返回 True"
    assert idx._embedding is None, "释放后 _embedding 应为 None"
    assert idx._store is None, "释放后 _store 应为 None"


@suite.case("4. EmbeddingIdleGuard：空闲达阈值释放，活跃不释放")
def _(mod):
    from desktop.embedding_guard import EmbeddingIdleGuard

    suite.require(EmbeddingIdleGuard, "start", "B21.T2 空闲释放守卫")
    suite.require(EmbeddingIdleGuard, "stop", "B21.T2 空闲释放守卫")

    tracker = _FakeTracker()
    calls: list[bool] = []

    def _fake_release() -> bool:
        # 记录是否被调用；返回 True 表示「本地模型已释放」
        calls.append(True)
        return True

    guard = EmbeddingIdleGuard(
        idle_tracker=tracker,
        idle_threshold_seconds=1,
        release_fn=_fake_release,
        poll_interval=0.05,
    )
    # 活跃状态：不应释放
    tracker.seconds = 0.0
    guard.start()
    time.sleep(0.2)
    assert not calls, "活跃状态不应触发释放"

    # 空闲达阈值：应触发释放
    tracker.seconds = 5.0
    time.sleep(0.3)
    assert calls, "空闲达阈值应触发释放"
    guard.stop()


@suite.case("5. Guard start/stop 幂等")
def _(mod):
    from desktop.embedding_guard import EmbeddingIdleGuard

    tracker = _FakeTracker()
    guard = EmbeddingIdleGuard(
        idle_tracker=tracker,
        idle_threshold_seconds=60,
        poll_interval=0.1,
    )
    assert guard.start() is True
    assert guard.start() is True  # 重复 start 幂等
    assert guard.status()["running"] is True
    guard.stop()
    guard.stop()  # 重复 stop 不抛异常
    assert guard.status()["running"] is False


if __name__ == "__main__":
    sys.exit(suite.run())
