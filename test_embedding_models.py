"""本地嵌入模型清单与下载服务验收（B21 增强：切分语块本地模型下载选项）。

覆盖：
  1. 可下载模型目录：中文 2 款（bge-small-zh-v1.5 / bge-large-zh-v1.5）
  2. 已下载状态检测：已存在模型 has_weights=True；未下载模型 downloaded=False
  3. 未知 model_id 校验：get_catalog_model 返回 None / 下载抛 ValueError
  4. 下载流程：mock snapshot_download，验证落点目录与参数（allow_patterns 跳过 .bin）
  5. 任务锁键：embedding_download 按 model_id 去重

安全：不触发真实网络下载（mock huggingface_hub.snapshot_download）；只读本地
models/ 目录（不写入任何真实模型）。

依赖：services.embedding_model_service（B21 实现后自动激活）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B21", module="services.embedding_model_service", title="本地嵌入模型下载（B21）")


@suite.case("1. 可下载模型目录含中文 2 款")
def _(mod):
    models = suite.require(mod, "CATALOG")
    ids = [m["model_id"] for m in models]
    assert "bge-small-zh-v1.5" in ids, "应含 bge-small-zh-v1.5"
    assert "bge-large-zh-v1.5" in ids, "应含 bge-large-zh-v1.5"
    for m in models:
        assert m["hf_repo_id"].startswith("BAAI/"), f"hf_repo_id 应为 BAAI 仓库: {m['hf_repo_id']}"


@suite.case("2. 已下载状态检测（现有 bge-small 已下载，bge-large 未下载）")
def _(mod):
    list_models = suite.require(mod, "list_local_embedding_models")
    info = {m["model_id"]: m for m in list_models()}
    assert "bge-small-zh-v1.5" in info
    # 本地存在 bge-small-zh-v1.5（仓库内）→ 应识别为已下载且含权重
    assert info["bge-small-zh-v1.5"]["downloaded"] is True, "bge-small 应识别为已下载"
    assert info["bge-small-zh-v1.5"]["has_weights"] is True, "bge-small 应含权重文件"
    # bge-large 未下载
    assert info["bge-large-zh-v1.5"]["downloaded"] is False, "bge-large 应识别为未下载"
    assert info["bge-large-zh-v1.5"]["size_bytes"] == 0, "未下载模型 size 应为 0"


@suite.case("3. 未知 model_id 校验")
def _(mod):
    get_catalog = suite.require(mod, "get_catalog_model")
    download = suite.require(mod, "download_embedding_model")
    assert get_catalog("bad-model") is None, "未知 model_id 应返回 None"
    try:
        download("bad-model")
        assert False, "未知 model_id 应抛 ValueError"
    except ValueError:
        pass


@suite.case("4. 下载流程：mock snapshot_download 验证落点与参数")
def _(mod):
    download = suite.require(mod, "download_embedding_model")
    import importlib
    import tempfile

    import types

    calls = {}

    def fake_snapshot_download(**kwargs):
        calls.update(kwargs)
        # 写入一个占位文件，模拟下载产物，让目录被识别为已下载
        local_dir = Path(kwargs["local_dir"])
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "config.json").write_text("{}", encoding="utf-8")
        (local_dir / "model.safetensors").write_bytes(b"\x00" * 10)
        return str(local_dir)

    # 注入 fake 到 huggingface_hub 模块
    hf = importlib.import_module("huggingface_hub")
    original = hf.snapshot_download
    hf.snapshot_download = fake_snapshot_download
    try:
        with tempfile.TemporaryDirectory() as tmp:
            # 用临时 models 根目录，避免写真实 models/
            import services.embedding_model_service as ems

            old_root = ems.models_root
            tmp_models = Path(tmp) / "models"
            ems.models_root = lambda: tmp_models
            try:
                res = download("bge-large-zh-v1.5")
            finally:
                ems.models_root = old_root
        assert res["model_id"] == "bge-large-zh-v1.5"
        assert calls["repo_id"] == "BAAI/bge-large-zh-v1.5", "repo_id 应正确"
        assert calls.get("local_dir", "").endswith("bge-large-zh-v1.5"), "落点应为 models/<model_id>"
        patterns = calls.get("allow_patterns") or []
        assert "*.safetensors" in patterns, "应包含 safetensors 模式"
        assert not any("pytorch_model.bin" in p for p in patterns), "应跳过重复的 pytorch_model.bin"
    finally:
        hf.snapshot_download = original


@suite.case("5. 任务锁键按 model_id 去重")
def _(mod):
    from api.tasks.lock import compute_lock_key

    k1 = compute_lock_key("embedding_download", {"model_id": "bge-small-zh-v1.5"})
    k2 = compute_lock_key("embedding_download", {"model_id": "bge-large-zh-v1.5"})
    assert k1 != k2, "不同模型锁键应不同"
    assert compute_lock_key("embedding_download", {}) is None, "缺 model_id 返回 None"


if __name__ == "__main__":
    sys.exit(suite.run())
