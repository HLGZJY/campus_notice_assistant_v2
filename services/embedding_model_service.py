"""本地嵌入模型清单与下载（B21 增强：切分语块本地模型下载选项）。

需求：使用本地嵌入模型（切分语块用）时，不再要求用户手动从 HuggingFace 下载
模型文件放到 `models/`，而是由应用内提供「下载选项」，列出多种可选模型并实时
展示已下载/缺失状态与下载进度。

设计（严格保持「后端 68 端点零改动」约束——新增端点而非改动既有端点）：

  - ``CATALOG``：可下载的本地嵌入模型目录（此处聚焦中文 2 款：bge-small-zh-v1.5
    与 bge-large-zh-v1.5）。每项含：
      - ``model_id``   : 本地模型标识（embedding.py 用 `models/<model_id>` 解析），
                         也是前端展示与下载请求用的 key
      - ``hf_repo_id`` : HuggingFace 仓库 id（如 BAAI/bge-small-zh-v1.5）
      - ``display_name`` / ``desc`` : 展示信息
  - ``list_local_embedding_models()``：返回目录 + 每个模型在 `models/<model_id>`
    的已下载状态与磁盘占用，供前端渲染「已下载 / 未下载」。
  - ``download_embedding_model()``：经 huggingface_hub.snapshot_download 拉取到
    `models/<model_id>`，支持进度回调（由 TaskManager worker 驱动）。

下载落点：模型文件统一放 `models/<model_id>/`（与既有 `models/bge-small-zh-v1.5`
布局一致），embedding.py 的 `get_app_root() / local_model` 路径解析天然兼容。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

from utils.app_paths import get_app_root

logger = logging.getLogger(__name__)

# 模型文件子目录（相对应用根目录）
MODELS_DIR_NAME = "models"

# 可下载的本地嵌入模型目录（聚焦中文；后续可按需扩充）
# model_id 同时是「本地目录名」与「前端展示/下载请求的 key」。
CATALOG: list[dict] = [
    {
        "model_id": "bge-small-zh-v1.5",
        "hf_repo_id": "BAAI/bge-small-zh-v1.5",
        "display_name": "BGE-small-zh-v1.5",
        "desc": "中文嵌入，体积小、速度快，日常切分检索均衡之选（约 100MB）",
        "size_mb": 95,
    },
    {
        "model_id": "bge-large-zh-v1.5",
        "hf_repo_id": "BAAI/bge-large-zh-v1.5",
        "display_name": "BGE-large-zh-v1.5",
        "desc": "中文嵌入，检索精度更高、资源占用更大（约 1.2GB）",
        "size_mb": 1300,
    },
]


def models_root() -> Path:
    """本地嵌入模型根目录（应用根目录 / models）。"""
    root = get_app_root() / MODELS_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _dir_size(path: Path) -> int:
    """递归统计目录字节数；失败返回 0。"""
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        return 0
    return total


def _model_status(model_id: str, hf_repo_id: str) -> dict:
    """返回单个模型的下载状态。"""
    local_dir = models_root() / model_id
    downloaded = local_dir.is_dir() and any(local_dir.iterdir())
    # 「已下载」判定：目录存在且含模型权重文件（safetensors / pytorch_model.bin）
    has_weights = False
    if downloaded:
        has_weights = any(
            local_dir.rglob(p)
            for p in ("*.safetensors", "*.bin", "model.safetensors", "pytorch_model.bin")
        )
    size_bytes = _dir_size(local_dir) if downloaded else 0
    return {
        "model_id": model_id,
        "hf_repo_id": hf_repo_id,
        "local_path": str(local_dir),
        "downloaded": downloaded,
        "has_weights": has_weights,
        "size_bytes": size_bytes,
        # 实际本地占用（float MB）；目录项 size_mb 是「需下载约 N MB」提示（int）
        "local_size_mb": round(size_bytes / (1024 * 1024), 1) if size_bytes else 0.0,
    }


def list_local_embedding_models() -> list[dict]:
    """返回可下载本地嵌入模型目录 + 每个模型的已下载状态。"""
    result = []
    for item in CATALOG:
        result.append(
            {
                **{k: item[k] for k in ("model_id", "hf_repo_id", "display_name", "desc", "size_mb")},
                **_model_status(item["model_id"], item["hf_repo_id"]),
            }
        )
    return result


def get_catalog_model(model_id: str) -> Optional[dict]:
    """按 model_id 查目录项；不存在返回 None。"""
    for item in CATALOG:
        if item["model_id"] == model_id:
            return item
    return None


def is_model_downloaded(model_id: str) -> bool:
    """model_id 是否已在本地下载（含权重文件）。"""
    item = get_catalog_model(model_id)
    if item is None:
        return False
    return _model_status(item["model_id"], item["hf_repo_id"])["has_weights"]


def download_embedding_model(
    model_id: str,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> dict:
    """下载指定本地嵌入模型到 models/<model_id>/。

    经 huggingface_hub.snapshot_download 从 HF 拉取（走 HF_ENDPOINT 镜像，见
    utils/embedding.py 默认 https://hf-mirror.com）。进度通过回调上报（TaskManager
    worker 用它更新任务进度）。

    Args:
        model_id: CATALOG 中的模型标识（本地目录名）。
        progress_cb: 进度回调，参数为 0~1 浮点；None 表示不回调。

    Returns:
        {"model_id": ..., "local_path": ...}

    Raises:
        ValueError: model_id 不在 CATALOG。
        RuntimeError: huggingface_hub 不可用或下载失败。
    """
    item = get_catalog_model(model_id)
    if item is None:
        raise ValueError(f"未知的本地嵌入模型: {model_id}")

    try:
        from huggingface_hub import snapshot_download
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"huggingface_hub 不可用（{type(e).__name__}: {e}）") from e

    target = models_root() / model_id
    target.mkdir(parents=True, exist_ok=True)

    # HF 镜像（与 embedding.py 保持一致，避免国内访问失败）
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    logger.info("开始下载本地嵌入模型 %s（repo=%s）到 %s", model_id, item["hf_repo_id"], target)
    try:
        # allow_patterns：只拉取推理必需文件，跳过重复的 pytorch_model.bin
        # （bge-small-zh-v1.5 同时含 .safetensors 与 .bin，二者其一即可加载，
        # 跳过 .bin 可省 ~95MB；bge-large 同理省 ~1.2GB）。
        # tqdm_class：huggingface_hub 用它显示下载进度；传入自定义类把进度经
        # progress_cb 上报给 TaskManager（0~1）。
        download_kwargs = {
            "repo_id": item["hf_repo_id"],
            "local_dir": str(target),
            "local_dir_use_symlinks": False,
            "allow_patterns": [
                "*.json",
                "*.txt",
                "*.model",
                "*.safetensors",
                "vocab.txt",
                "tokenizer.json",
                "tokenizer_config.json",
                "special_tokens_map.json",
                "config_sentence_transformers.json",
                "modules.json",
                "1_Pooling/*",
            ],
        }
        if progress_cb is not None:
            from tqdm.auto import tqdm as _tqdm_base

            class _ProgressTqdm(_tqdm_base):
                def __init__(self, *a, **kw):
                    super().__init__(*a, **kw)
                    self._last_frac = -1.0

                def update(self, n: int = 1):
                    super().update(n)
                    if self.total:
                        frac = self.n / float(self.total)
                        # 进度回调（阈值去抖，避免高频写库）
                        if frac - self._last_frac >= 0.02 or frac >= 1.0:
                            self._last_frac = frac
                            progress_cb(frac)

            download_kwargs["tqdm_class"] = _ProgressTqdm

        local_dir = snapshot_download(**download_kwargs)
    except Exception as e:  # noqa: BLE001
        logger.exception("下载本地嵌入模型 %s 失败", model_id)
        raise RuntimeError(f"下载失败（{type(e).__name__}: {e}）") from e

    if progress_cb is not None:
        progress_cb(1.0)

    size = _dir_size(target)
    logger.info(
        "本地嵌入模型 %s 下载完成（%s，%.1f MB）",
        model_id,
        local_dir,
        size / (1024 * 1024),
    )
    return {"model_id": model_id, "local_path": str(target), "size_bytes": size}
