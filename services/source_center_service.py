"""数据源中心：公共数据源目录的读取 / 选用 / 移除 / 预览。

目录数据：config/source_catalog.yaml（静态公共库，随代码库维护）。
个人数据源：config/schools/<active_school>.yaml 的 sources（"我的数据源"），
写入口唯一为 ConfigStore.save_sources（与 config_service.update_sources 同路径）。

联动语义：按 list_url 判重——目录条目的 list_url 已存在于个人数据源 → adopted=True。
  - 选用 = 以「组织-栏目名」追加 SourceConfig(enabled=True) 到个人数据源；
  - 移除 = 按 list_url 从个人数据源删除（不影响目录条目本身）。
与「系统配置-数据源」页读写同一份 YAML，自动双向同步。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

import requests
import yaml

from config.schema import SchoolConfig, SourceConfig
from config.store import ConfigStore
from crawler.base import ListPageParser

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "source_catalog.yaml"

# 预览抓取：UA 与 config_service.test_source_url 保持一致
_PREVIEW_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_catalog_lock = threading.Lock()
_catalog_cache: Optional[dict] = None


def _load_catalog(force: bool = False) -> dict:
    """读取数据源中心公共目录（进程内缓存，force 可强制重读）。"""
    global _catalog_cache
    if _catalog_cache is not None and not force:
        return _catalog_cache
    with _catalog_lock:
        if _catalog_cache is not None and not force:
            return _catalog_cache
        if not CATALOG_PATH.exists():
            logger.warning("数据源中心目录文件缺失: %s", CATALOG_PATH)
            _catalog_cache = {"school": "", "school_code": "", "sources": []}
            return _catalog_cache
        with open(CATALOG_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        _catalog_cache = {
            "school": raw.get("school", ""),
            "school_code": raw.get("school_code", ""),
            "sources": raw.get("sources") or [],
        }
        return _catalog_cache


def _get_user_sources() -> list[dict]:
    """当前活跃学校的个人数据源列表（dict 形式）。"""
    try:
        school = ConfigStore.get_instance().get_school()
        return [s.model_dump() for s in school.sources]
    except Exception as e:
        logger.warning("读取个人数据源失败: %s", e)
        return []


def _adopted_urls(user_sources: list[dict]) -> set[str]:
    """个人数据源中已收录的 list_url 集合（判重依据）。"""
    return {s.get("list_url", "") for s in user_sources if s.get("list_url")}


def _find_entry(source_id: str) -> Optional[dict]:
    """按 id 查找目录条目。"""
    for s in _load_catalog()["sources"]:
        if s.get("id") == source_id:
            return s
    return None


# ---------- 查询 ----------


def get_overview() -> dict:
    """数据源中心总览：学校信息 + 分类树 + 目录条目（含 adopted 状态）。"""
    catalog = _load_catalog()
    user_sources = _get_user_sources()
    adopted = _adopted_urls(user_sources)

    # 分类树：一级分组 → 二级组织 → 三级栏目（可折叠）
    # key 约定：一级 group:{group} / 二级 group:{group}:{org} / 三级 item:{id}，
    # 前端统一按此解析（item: 前缀按 id 精确匹配单条）。
    tree: list[dict] = []
    groups: dict[str, dict[str, list[dict]]] = {}
    for s in catalog["sources"]:
        group = s.get("org_group", "其他")
        org = s.get("org", s.get("name", ""))
        groups.setdefault(group, {}).setdefault(org, []).append(s)
    for group, orgs in groups.items():
        children = []
        for org, srcs in sorted(orgs.items(), key=lambda kv: -len(kv[1])):
            leaves = [
                {"key": f"item:{s['id']}", "label": s.get("name", ""), "count": 1}
                for s in srcs
            ]
            children.append(
                {
                    "key": f"group:{group}:{org}",
                    "label": org,
                    "count": len(srcs),
                    "children": leaves,
                }
            )
        tree.append(
            {
                "key": f"group:{group}",
                "label": group,
                "count": sum(len(v) for v in orgs.values()),
                "children": children,
            }
        )

    items = []
    for s in catalog["sources"]:
        items.append(
            {
                "id": s["id"],
                "name": s.get("name", ""),
                "org": s.get("org", ""),
                "org_group": s.get("org_group", ""),
                "list_url": s.get("list_url", ""),
                "description": s.get("description", ""),
                "tags": s.get("tags") or [],
                "updated_at": s.get("updated_at", ""),
                "adopted": s.get("list_url", "") in adopted,
            }
        )

    return {
        "school": catalog["school"],
        "school_code": catalog["school_code"],
        "tree": tree,
        "items": items,
        "adopted_count": sum(1 for it in items if it["adopted"]),
    }


def preview_source(source_id: str, limit: int = 10, timeout: int = 12) -> dict:
    """预览样例数据：抓取列表页，解析前 N 条标题/链接/日期（只读，不落库）。

    失败返回 ok=False + error（网络不可达 / 解析异常等），不抛异常。
    """
    entry = _find_entry(source_id)
    if entry is None:
        return {"ok": False, "source_id": source_id, "list_url": "", "items": [], "error": "数据源不存在"}
    url = entry.get("list_url", "")
    try:
        start = time.time()
        resp = requests.get(url, headers=_PREVIEW_HEADERS, timeout=timeout)
        resp.encoding = resp.apparent_encoding
        if resp.status_code != 200:
            return {
                "ok": False,
                "source_id": source_id,
                "list_url": url,
                "items": [],
                "error": f"HTTP {resp.status_code}（{int((time.time() - start) * 1000)}ms）",
            }
        parser = ListPageParser(resp.text, url)
        links = parser.discover_notice_links()
        items = [
            {
                "title": item.title,
                "url": item.url,
                "date": item.published_at or None,
            }
            for item in links[:limit]
        ]
        return {
            "ok": True,
            "source_id": source_id,
            "list_url": url,
            "items": items,
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "source_id": source_id,
            "list_url": url,
            "items": [],
            "error": f"{type(e).__name__}: {e}",
        }


# ---------- 选用 / 移除（与「我的数据源」联动） ----------


def adopt_source(source_id: str) -> dict:
    """选用目录条目：追加到个人数据源（按 list_url 判重，已存在则幂等返回）。"""
    entry = _find_entry(source_id)
    if entry is None:
        return {"ok": False, "source_id": source_id, "adopted": False, "already": False, "error": "数据源不存在"}
    url = entry.get("list_url", "")
    store = ConfigStore.get_instance()
    try:
        school = store.get_school()
    except Exception as e:
        return {"ok": False, "source_id": source_id, "adopted": False, "already": False, "error": f"读取学校配置失败: {e}"}

    user_sources = [s.model_dump() for s in school.sources]
    if url in _adopted_urls(user_sources):
        return {"ok": True, "source_id": source_id, "adopted": True, "already": True, "error": None}

    # 命名沿用「组织-栏目」惯例（如 计算机学院-通知公告）
    name = f"{entry.get('org', '')}-{entry.get('name', '')}" if entry.get("org") else entry.get("name", "")
    new_source = SourceConfig(name=name, list_url=url)
    new_sources = [SourceConfig(**s) for s in user_sources] + [new_source]
    result = store.save_sources(school.code, SchoolConfig(name=school.name, code=school.code, sources=new_sources))
    if not result.get("ok"):
        return {"ok": False, "source_id": source_id, "adopted": False, "already": False, "error": result.get("error", "保存失败")}
    logger.info("数据源中心选用: %s → %s（%d 个个人数据源）", source_id, url, len(new_sources))
    return {"ok": True, "source_id": source_id, "adopted": True, "already": False, "error": None}


def remove_source(source_id: str) -> dict:
    """移除目录条目：按 list_url 从个人数据源删除（幂等，未选用也返回 ok）。"""
    entry = _find_entry(source_id)
    if entry is None:
        return {"ok": False, "source_id": source_id, "adopted": False, "already": False, "error": "数据源不存在"}
    url = entry.get("list_url", "")
    store = ConfigStore.get_instance()
    try:
        school = store.get_school()
    except Exception as e:
        return {"ok": False, "source_id": source_id, "adopted": False, "already": False, "error": f"读取学校配置失败: {e}"}

    kept = [s for s in school.sources if s.list_url != url]
    if len(kept) == len(school.sources):
        # 本来就没选用：幂等成功
        return {"ok": True, "source_id": source_id, "adopted": False, "already": False, "error": None}

    result = store.save_sources(
        school.code,
        SchoolConfig(name=school.name, code=school.code, sources=kept),
    )
    if not result.get("ok"):
        return {"ok": False, "source_id": source_id, "adopted": False, "already": False, "error": result.get("error", "保存失败")}
    logger.info("数据源中心移除: %s → %s（剩余 %d 个）", source_id, url, len(kept))
    return {"ok": True, "source_id": source_id, "adopted": False, "already": False, "error": None}
