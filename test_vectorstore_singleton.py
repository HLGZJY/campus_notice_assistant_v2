"""阶段 A1 验收：Chroma PersistentClient 共享单例（离线，不依赖真实模型/网络）。

覆盖验收信号：
  1. get_vector_index() 多次调用返回同一实例（进程级单例）。
  2. 同目录 VectorIndex 共享同一个底层 PersistentClient（HNSW 只加载一次）；
     不同 persist_dir 使用不同客户端。
  3. 单例复用后 add_notice / search / remove_notice / rebuild / delete_collection 均正常。

用法：python test_vectorstore_singleton.py
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import storage.vectorstore as vs

TMP_CHROMA_A = Path(__file__).parent / "data" / "test_vs_singleton_a"
TMP_CHROMA_B = Path(__file__).parent / "data" / "test_vs_singleton_b"


class FakeEmbeddings:
    """确定性 embedding 桩：相同文本 → 相同固定维度向量。"""

    _DIM = 8

    def embed_documents(self, texts):
        return [[float(abs(hash(t)) % 1000) / 1000.0] * self._DIM for t in texts]

    def embed_query(self, text):
        return [float(abs(hash(text)) % 1000) / 1000.0] * self._DIM


def _sample_notice(nid: int, title: str, content: str) -> dict:
    return {
        "id": nid,
        "title": title,
        "notice_type": "competition",
        "summary": "测试",
        "deadline": "",
        "raw_content": content,
        "source": "测试来源",
        "url": f"https://example.com/{nid}.htm",
        "published_at": "2026-01-01T00:00:00",
        "status": "extracted",
    }


def cleanup():
    """先关闭本次测试创建的共享客户端（Windows 下文件占用才能删除目录）。"""
    for client in list(vs._CLIENTS.values()):
        try:
            client.close()
        except Exception:
            pass
    vs._CLIENTS.clear()
    vs._DEFAULT_INDEX = None
    for path in (TMP_CHROMA_A, TMP_CHROMA_B):
        try:
            if path.exists():
                shutil.rmtree(path)
        except OSError:
            pass


def run():
    cleanup()
    failures = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    # 默认路径指向临时目录，避免触碰真实 data/chroma
    vs.DEFAULT_PERSIST_DIR = TMP_CHROMA_A

    print("== 1. 默认路径 get_vector_index() 为进程级单例 ==")
    vs.get_embeddings = lambda: FakeEmbeddings()
    i1 = vs.get_vector_index()
    i2 = vs.get_vector_index()
    check("两次调用返回同一实例", i1 is i2, f"{id(i1)} vs {id(i2)}")

    print("== 2. 同目录共享底层 PersistentClient，异目录独立 ==")
    direct = vs.VectorIndex()  # 直构默认路径：与单例共享底层 client
    check(
        "直构 VectorIndex 与单例共享底层 client",
        direct._get_store()._client is i1._get_store()._client,
    )
    other = vs.VectorIndex(persist_dir=TMP_CHROMA_B)
    check(
        "不同 persist_dir 使用不同 client",
        other._get_store()._client is not i1._get_store()._client,
    )
    check(
        "同目录再次 get_vector_index 仍共享 client",
        i2._get_store()._client is i1._get_store()._client,
    )

    print("== 3. 单例复用 add_notice / search / remove_notice ==")
    n1 = _sample_notice(
        1,
        "ICPC 校赛报名通知",
        "关于 ICPC 国际大学生程序设计竞赛校内选拔赛报名的通知，报名截止时间 2026年9月30日。",
    )
    info = i1.add_notice(n1)
    check("add_notice 写入 chunk", info["chunks"] > 0, f"chunks={info['chunks']}")
    docs = i1.search("ICPC 校赛报名", k=3)
    check(
        "search 命中该通知",
        any(d.metadata.get("notice_id") == 1 for d in docs),
        f"top={[d.metadata.get('notice_id') for d in docs]}",
    )
    removed = i1.remove_notice(1)
    check("remove_notice 删除 chunk", removed == info["chunks"], f"removed={removed}")
    docs2 = i1.search("ICPC 校赛报名", k=3)
    check(
        "删除后不再命中",
        not any(d.metadata.get("notice_id") == 1 for d in docs2),
    )

    print("== 4. 单例 rebuild / delete_collection 仍正常 ==")
    notices = [
        _sample_notice(1, "通知A", "关于A的正文内容，这是一个用于测试的通知。"),
        _sample_notice(2, "通知B", "关于B的正文内容，这是另一个用于测试的通知。"),
    ]
    rb = i1.rebuild(notices)
    check("rebuild 索引 2 条通知", rb["notices"] == 2 and rb["chunks"] > 0, f"{rb}")
    check("count 与 rebuild chunk 一致", i1.count() == rb["chunks"], f"count={i1.count()}")
    i1.delete_collection()
    check("delete_collection 后 count=0", i1.count() == 0, f"count={i1.count()}")
    info_after = i1.add_notice(_sample_notice(3, "通知C", "关于C的正文内容，删除后重建。"))
    check("删除后 add_notice 重建可用", info_after["chunks"] > 0, f"chunks={info_after['chunks']}")

    cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    run()