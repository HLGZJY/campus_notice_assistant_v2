"""在线 embedding 模型可行性测试（qwen3.7-text-embedding @ 阿里云百炼）。

不改动任何全局配置：直接通过 utils.embedding.create_embeddings("bailian", MODEL)
走项目内建的 OpenAI-compatible 在线 embedding 路径（_MeteredOpenAIEmbeddings）。

验证项：
  1. 连通性 + 鉴权（HTTP 200 / 401 / 404）
  2. 返回向量维度（与现有本地 bge 512 维对比，判断离线索引是否兼容）
  3. 延迟（批量 embed 通知 chunk + 单次 query）
  4. 相似度质量（相关 query 与不相关 query 的 cosine 区分度）
  5. 端到端检索（隔离临时 Chroma 目录，monkeypatch 注入在线 embedding）

仅用临时目录，不触碰生产 data/chroma（512 维离线索引）。
"""
from __future__ import annotations

import math
import os
import sys
import time
import tempfile
from pathlib import Path

# 项目根目录加载 .env（含 DASHSCOPE_API_KEY）
ROOT = Path(__file__).resolve().parent
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

try:
    import requests
except ImportError:
    print("FATAL: requests 未安装（venv312 应已含）")
    sys.exit(2)

PROVIDER = "bailian"
MODEL = "qwen3.7-text-embedding"
FALLBACK_MODEL = "text-embedding-v3"  # 真实存在的百炼 embedding 模型，用于区分「命名错」vs「连通/鉴权错」

# 真实感的校园通知样例（中文）
SAMPLES = [
    ("奖学金", "关于评选2026年度国家奖学金的通知：符合条件的全日制本科生可于9月30日前通过学院提交申请，需附成绩单与科研成果证明。"),
    ("宿舍", "学生宿舍停电检修通知：9月15日22:00至次日6:00，南校区1-6号宿舍楼将进行电路检修，请提前保存电脑文件并关闭大功率电器。"),
    ("竞赛", "2026年全国大学生英语竞赛报名通知：即日起至10月10日开放报名，本科生可登录教务处系统填报，初赛定于11月。"),
    ("考试", "关于2026年秋季学期期末考试安排的通知：考试周为12月20日至12月31日，请同学们及时查询个人考试安排并诚信应考。"),
    ("图书馆", "图书馆延长开放时间通知：自9月起，中心校区图书馆每周五、周六开放至23:00，考研专区全天候开放。"),
]
QUERY_RELATED = "国家奖学金怎么申请？需要什么材料"  # 应贴近「奖学金」
QUERY_UNRELATED = "宿舍什么时候停电检修？"          # 应贴近「宿舍」而非「奖学金」


def cosine(a, b):
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


def raw_probe(base_url: str, api_key: str, model: str) -> dict:
    """直接打 /embeddings，原样返回状态码与报文，便于定位模型名/鉴权问题。"""
    url = f"{base_url}/embeddings"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json={"input": "测试", "model": model}, timeout=20)
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        return {"model": model, "status": resp.status_code, "body": body}
    except Exception as e:  # noqa: BLE001
        return {"model": model, "status": -1, "body": f"{type(e).__name__}: {e}"}


def main() -> int:
    from config.store import ConfigStore
    from utils.embedding import create_embeddings, _MeteredOpenAIEmbeddings

    store = ConfigStore.get_instance()
    provider = store.get_provider(PROVIDER)
    api_key = store.get_api_key(PROVIDER)
    base_url = provider.base_url

    print("=" * 70)
    print(f"在线 embedding 测试  provider={PROVIDER}  base_url={base_url}")
    print(f"API key 状态: {'已配置' if api_key else '缺失!!!'}  (len={len(api_key)})")
    if not api_key:
        print("FATAL: DASHSCOPE_API_KEY 未加载，无法测试在线 embedding")
        return 2
    print("=" * 70)

    # ---- 1. 原生探测：模型名是否可用 ----
    print("\n[1] 原生 /embeddings 探测")
    probe = raw_probe(base_url, api_key, MODEL)
    print(f"  model={MODEL}  ->  HTTP {probe['status']}")
    if probe["status"] != 200:
        print(f"  body: {str(probe['body'])[:400]}")
        # 用真实存在的模型探一下，区分命名错 vs 连通/鉴权错
        fb = raw_probe(base_url, api_key, FALLBACK_MODEL)
        print(f"  [对照] model={FALLBACK_MODEL}  ->  HTTP {fb['status']}")
        if fb["status"] == 200:
            print("  => 端点与 key 正常，问题在模型名 qwen3.7-text-embedding 不存在，需确认正确模型名。")
        elif fb["status"] in (-1, 401, 403):
            print("  => 端点或鉴权异常，请检查 DASHSCOPE_API_KEY 与网络。")
        # 即便模型名错，仍可继续用 fallback 验证「在线路线」本身可行
        if fb["status"] == 200:
            print(f"  ** 继续用 {FALLBACK_MODEL} 验证在线 embedding 链路（仅验证可用性，非用户指定模型）**")
            effective_model = FALLBACK_MODEL
        else:
            print("  在线链路不可用，终止。")
            return 1
    else:
        effective_model = MODEL
        print("  => 目标模型名可用 ✅")

    # ---- 2. 走项目代码 create_embeddings 在线路径 ----
    print(f"\n[2] 项目内建 create_embeddings('{PROVIDER}', '{effective_model}')")
    emb = create_embeddings(PROVIDER, effective_model)
    online = isinstance(emb, _MeteredOpenAIEmbeddings)
    print(f"  返回实例类型: {type(emb).__name__}  ->  {'在线路径 ✅' if online else '本地 fallback ⚠️'}")
    if not online:
        print("  FATAL: 未走在线路径，探针应已成功，请检查 _probe_embedding_endpoint。")
        return 1

    # ---- 3. 维度 + 延迟 ----
    print("\n[3] 维度与延迟")
    t0 = time.perf_counter()
    vecs = emb.embed_documents([s for _, s in SAMPLES])
    dt = time.perf_counter() - t0
    dim = len(vecs[0]) if vecs else 0
    qv = emb.embed_query(QUERY_RELATED)
    print(f"  文档批量 embed: {len(vecs)} 段, 维度={dim}, 耗时={dt*1000:.0f}ms "
          f"(≈{dt*1000/len(vecs):.0f}ms/段)")
    print(f"  注：本地 bge-small-zh-v1.5 维度=512；在线维度若不同，现有离线 Chroma 索引不兼容，需在线重建。")

    # ---- 4. 相似度质量 ----
    print("\n[4] 相似度质量（cosine）")
    sim_rel = cosine(qv, vecs[0])   # 奖学金
    sim_unrel = cosine(qv, vecs[1])  # 宿舍
    print(f"  query(奖学金) vs 奖学金通知 : {sim_rel:.4f}")
    print(f"  query(奖学金) vs 宿舍通知   : {sim_unrel:.4f}")
    print(f"  区分度 = {sim_rel - sim_unrel:+.4f}  ->  {'合理 ✅' if sim_rel > sim_unrel else '异常 ⚠️'}")

    # ---- 5. 端到端检索（隔离临时目录，monkeypatch 注入在线 embedding）----
    print("\n[5] 端到端检索（隔离临时 Chroma）")
    import unittest.mock as mock
    from storage.vectorstore import VectorIndex

    # VectorIndex 内部 from utils.embedding import get_embeddings，名字绑定在 storage.vectorstore 命名空间，
    # 故需 patch storage.vectorstore.get_embeddings 才能注入在线 embedding。
    with mock.patch("storage.vectorstore.get_embeddings", return_value=emb):
        tmp = Path(tempfile.mkdtemp(prefix="cna_online_emb_"))
        try:
            idx = VectorIndex(persist_dir=tmp)
            notices = [
                {"id": i + 1, "title": t, "notice_type": "通知", "summary": "",
                 "deadline": "", "source": "test", "url": "", "status": "extracted",
                 "published_at": "2026-09-01", "raw_content": c}
                for i, (t, c) in enumerate(SAMPLES)
            ]
            res = idx.rebuild(notices)
            print(f"  临时索引: 通知={res['notices']} chunk={res['chunks']} 维度由 embedding 决定")
            hits = idx.search(QUERY_RELATED, k=3, strategy="none")
            top_title = hits[0].metadata.get("title", "?") if hits else "无"
            print(f"  query='{QUERY_RELATED}' Top1 = {top_title}")
            print(f"  ->  {'命中正确（奖学金）✅' if top_title == '奖学金' else '未命中预期 ⚠️'}")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 70)
    print("结论：在线 embedding 链路可用，质量与延迟见上。打包改为纯在线方案时，")
    print("需：① 将 config/app.yaml embedding.provider 由 local 改为 bailian，")
    print(f"② embedding.models 改为 ['{effective_model}']，③ 用在线 embedding 重建 Chroma 索引")
    print("（维度变更必然导致旧离线索引失效，首次启动需自动 rebuild）。")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
