"""任务执行器：task type → 实际业务函数（WORKERS 注册表）。

每个 worker 签名统一为 `fn(task: dict, progress_cb, deps: dict) -> dict`：
  - task: 含 id/type/params（已解析的 dict）
  - progress_cb: 接收 0.0~1.0 进度的小数回调（manager 绑定好 task_id 的 partial）
  - deps: 依赖注入点（测试用，如 {"extractor": FakeExtractor()}）

阻塞业务函数会经 manager 的 asyncio.to_thread 运行，此处只需复用现有 services。
"""
from __future__ import annotations

from services import admin_service, notice_service, subscription_service, todo_service


def _on_progress(progress_cb, done: int, total: int) -> None:
    """把业务回调的 (done, total) 换算成 0.0~1.0 上报。"""
    if progress_cb is None or not total:
        return
    progress_cb(float(done) / float(total))


def crawl_source(task: dict, progress_cb, deps: dict) -> dict:
    """单源抓取（阶段 7 支持 mode/max_pages/deep_check 覆盖）。"""
    params = task.get("params") or {}
    source_name = params.get("source_name")
    result = notice_service.crawl_source_by_name(
        source_name,
        progress_cb=lambda d, t: _on_progress(progress_cb, d, t),
        mode=params.get("mode"),
        max_pages=params.get("max_pages"),
        deep_check=params.get("deep_check"),
    )
    if result.get("ok") is False:
        raise RuntimeError(result.get("error", "抓取失败"))
    return result


def crawl_all(task: dict, progress_cb, deps: dict) -> dict:
    """全部数据源抓取（阶段 7 支持 sources 多选 / mode / max_pages / deep_check）。"""
    params = task.get("params") or {}
    mode = params.get("mode")
    max_pages = params.get("max_pages")
    deep_check = bool(params.get("deep_check", False))
    sources = params.get("sources") or None

    def _cb(done: int, total: int) -> None:
        _on_progress(progress_cb, done, total)

    results = notice_service.crawl_all_sources(
        progress_cb=_cb, deep_check=deep_check
    )
    if sources:
        results = {k: v for k, v in results.items() if k in sources}

    summary = {
        "sources": len(results),
        "discovered": sum(r.get("discovered", 0) for r in results.values()),
        "new": sum(r.get("new", 0) for r in results.values()),
        "skipped": sum(r.get("skipped", 0) for r in results.values()),
        "changed": sum(r.get("changed", 0) for r in results.values()),
        "failed": sum(r.get("failed", 0) for r in results.values()),
        "mode": mode or "incremental",
        "deep_check": deep_check,
    }
    return {"summary": summary, "per_source": results}


def extract_batch(task: dict, progress_cb, deps: dict) -> dict:
    """批量提取 status=raw 的通知（断点续跑游标 + 阶段 7 前置过滤）。"""
    params = task.get("params") or {}
    return notice_service.extract_batch(
        limit=params.get("limit", 50),
        auto_index=params.get("auto_index", True),
        extractor=deps.get("extractor"),
        prefilter=params.get("prefilter", True),
        notice_ids=params.get("notice_ids"),
        progress_cb=lambda d, t: _on_progress(progress_cb, d, t),
    )


def subscription_add(task: dict, progress_cb, deps: dict) -> dict:
    """新增订阅 + 全库回填。"""
    params = task.get("params") or {}
    result = subscription_service.add_subscription(
        keyword=params.get("keyword", ""),
        notice_type=params.get("notice_type"),
        enabled=params.get("enabled", True),
        progress_cb=lambda d, t: _on_progress(progress_cb, d, t),
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "新增订阅失败"))
    return result


def subscription_update(task: dict, progress_cb, deps: dict) -> dict:
    """更新订阅 + 重算命中关系（只传请求中实际提供的字段，保持 _UNSET 语义）。"""
    params = task.get("params") or {}
    kwargs = {k: params[k] for k in ("keyword", "notice_type", "enabled") if k in params}
    result = subscription_service.update_subscription_record(
        params.get("subscription_id"),
        progress_cb=lambda d, t: _on_progress(progress_cb, d, t),
        **kwargs,
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "更新订阅失败"))
    return result


def match_all(task: dict, progress_cb, deps: dict) -> dict:
    """全库重匹配。"""
    return subscription_service.match_all_notices(
        progress_cb=lambda d, t: _on_progress(progress_cb, d, t)
    )


def rebuild_index(task: dict, progress_cb, deps: dict) -> dict:
    """全量重建 Chroma 索引。"""
    params = task.get("params") or {}
    result = admin_service.rebuild_index(
        statuses=params.get("statuses"),
        dry_run=params.get("dry_run", False),
    )
    if result.get("ok") is False:
        raise RuntimeError(result.get("error", "重建索引失败"))
    return result


def generate_todos(task: dict, progress_cb, deps: dict) -> dict:
    """为指定通知生成待办（生成即落库，回填主键后作为任务结果返回）。"""
    notice_id = (task.get("params") or {}).get("notice_id")
    result = todo_service.generate_todos(notice_id)
    items: list[dict] = []
    if result["success"] and result["items"]:
        rows = {
            r["action"]: r
            for r in todo_service.get_todos_by_notice(notice_id)
            if r.get("status") == "pending"
        }
        items = [rows[it["action"]] for it in result["items"] if it["action"] in rows]
    return {
        "success": result["success"],
        "status": result["status"],
        "items": items,
        "error": result["error"],
    }


def re_extract_notice(task: dict, progress_cb, deps: dict) -> dict:
    """单条通知重置并重新提取。"""
    params = task.get("params") or {}
    result = admin_service.re_extract_notice(
        params.get("notice_id"),
        auto_index=params.get("auto_index", True),
    )
    if not result.get("success"):
        raise RuntimeError(result.get("error", "重新提取失败"))
    return result


def _task_filter(params: dict) -> dict:
    """从任务参数中抽取筛选条件（只保留非空键），与 storage.db.build_notice_where 对齐。"""
    keys = (
        "status",
        "source",
        "notice_type",
        "published_from",
        "published_to",
        "published_before",
        "crawled_from",
        "crawled_to",
    )
    return {k: params[k] for k in keys if params.get(k) is not None}


def batch_delete(task: dict, progress_cb, deps: dict) -> dict:
    """按筛选条件批量删除通知（级联清理向量索引）。"""
    result = admin_service.batch_delete_by_filter(
        _task_filter(task.get("params") or {}),
        progress_cb=lambda d, t: _on_progress(progress_cb, d, t),
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "批量删除失败"))
    return result


def batch_reset(task: dict, progress_cb, deps: dict) -> dict:
    """按筛选条件批量重置通知状态（供重新提取）。"""
    params = task.get("params") or {}
    result = admin_service.batch_reset_by_filter(
        _task_filter(params),
        target_status=params.get("target_status", "raw"),
        progress_cb=lambda d, t: _on_progress(progress_cb, d, t),
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "批量重置失败"))
    return result


WORKERS: dict[str, object] = {
    "crawl_source": crawl_source,
    "crawl_all": crawl_all,
    "extract_batch": extract_batch,
    "subscription_add": subscription_add,
    "subscription_update": subscription_update,
    "match_all": match_all,
    "rebuild_index": rebuild_index,
    "generate_todos": generate_todos,
    "re_extract_notice": re_extract_notice,
    "batch_delete": batch_delete,
    "batch_reset": batch_reset,
}
