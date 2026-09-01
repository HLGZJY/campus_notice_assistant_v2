"""桌面控制面路由（/api/v1/desktop/*）。

对应 DESKTOP-UPGRADE.md §4.3 的 6 个端点：

| 端点 | 方法 | 用途 | 优先级 |
|---|---|---|---|
| /desktop/status | GET | 壳状态：版本/端口/后端健康/调度状态/窗口可见性 | P0 |
| /desktop/scheduler | POST | {"action": "pause"|"resume"} 暂停/恢复调度 | P1 |
| /desktop/autostart | POST | {"enabled": bool} 开关开机自启 | P0 |
| /desktop/open-log-dir | POST | 打开日志目录（系统文件管理器） | P0 |
| /desktop/restart-backend | POST | 手动重启后端线程（排障用） | P1 |
| /desktop/quit | POST | 请求应用退出（前端"退出应用"按钮） | P1 |

安全：这些端点仅本机（uvicorn 绑 127.0.0.1），且与 K7 启动令牌共用
校验中间件（见 api/desktop_token.py）；/desktop/quit、/desktop/restart-backend
等敏感操作要求带令牌。

与壳的耦合方式：本模块**不 import desktop.app**（避免桌面壳未装时把壳模块
连带拉进后端），通过一个轻量注册表 ``set_desktop_app / get_desktop_app`` 与
壳解耦——DesktopApp 在启动后端前把自己的实例注册进来。壳缺省（纯后端 /
--browser 降级）时，各端点返回 ``{available: false}`` 或明确的状态，不报错。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/desktop",
    tags=["desktop"],
)


# ---------------------------------------------------------------------------
# 壳实例注册表：让控制面路由能触达运行中的 DesktopApp，而不 import 壳模块。
# ---------------------------------------------------------------------------
_desktop_app: Optional[Any] = None


def set_desktop_app(app: Any) -> None:
    """注册运行中的 DesktopApp 实例（桌面壳启动后端前调用）。"""
    global _desktop_app
    _desktop_app = app


def get_desktop_app() -> Any:
    """取已注册的 DesktopApp 实例；未注册返回 None（纯后端 / 降级模式）。"""
    return _desktop_app


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------
class SchedulerAction(BaseModel):
    action: str  # "pause" | "resume"


class AutostartRequest(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
@router.get("/status")
def status(request: Request) -> dict:
    """壳状态：版本/端口/后端健康/调度状态/窗口可见性（P0）。

    壳未注册（纯后端 / --browser 降级）时返回 available=false，其余字段
    尽力填充（版本 / 后端健康始终可得）。
    """
    from utils.app_paths import get_version

    app = get_desktop_app()
    server = getattr(app, "server", None)
    window = getattr(app, "_window", None)
    sched = getattr(request.app.state, "scheduler", None)

    scheduler_state = {}
    if sched is not None:
        try:
            info = sched.get_status()
            scheduler_state = {
                "running": info.get("running", False),
                "interval_minutes": info.get("interval_minutes"),
                "jobs": info.get("jobs", []),
            }
        except Exception:  # noqa: BLE001 - 调度器状态取不到不阻塞 status
            logger.debug("desktop/status 取调度器状态失败", exc_info=True)
            scheduler_state = {"running": False, "jobs": []}

    return {
        "available": app is not None,
        "version": get_version(),
        "port": getattr(server, "port", None),
        "backend_health": "ok",  # 能进到本端点即后端健康
        "scheduler": scheduler_state,
        "window_visible": bool(window is not None),
    }


@router.post("/scheduler")
def scheduler_action(payload: SchedulerAction, request: Request) -> dict:
    """暂停/恢复调度（P1）。

    实际 pause()/resume() 由 B14 落地；本批在调度器尚未实现 pause/resume
    时返回 ``supported=false``，但契约（请求/响应结构）先固定。
    """
    action = payload.action
    if action not in ("pause", "resume"):
        raise HTTPException(status_code=422, detail="action 须为 pause 或 resume")

    sched = getattr(request.app.state, "scheduler", None)
    if sched is None:
        raise HTTPException(status_code=409, detail="调度器未启用")

    pause = getattr(sched, "pause", None)
    resume = getattr(sched, "resume", None)
    if pause is None or resume is None:
        return {
            "supported": False,
            "action": action,
            "message": "调度 pause/resume 尚未实现（B14 落地）",
        }

    (pause if action == "pause" else resume)()
    logger.info("桌面控制面：调度器已 %s", action)
    return {"supported": True, "action": action, "paused": action == "pause"}


@router.post("/autostart")
def autostart(payload: AutostartRequest, request: Request) -> dict:
    """开关开机自启（P0，B13.T2 落地注册表写入）。

    两步：
      1. 把开关持久化到壳 settings.json（壳未注册时也尽力记录，保留用户意图）；
      2. 实际写/删 HKCU Run 注册表值（复用 desktop.autostart，B13.T1）。

    ``desktop.autostart`` 是叶子模块（仅依赖 winreg/sys/logging，不拉 webview/
    pystray 壳），从控制面路由安全 import，不会把桌面壳连带拉进纯后端进程。
    返回 ``supported``/``applied`` 表示注册表操作是否成功，``enabled`` 为生效值，
    ``message`` 供前端展示。
    """
    app = get_desktop_app()
    settings = getattr(app, "settings", None)
    # 1. 持久化用户意图（settings.json）
    if settings is not None:
        settings.set("autostart", payload.enabled)
        settings.save()
    # 2. 实际写删 HKCU Run（B13.T1/T2）
    try:
        from desktop.autostart import set_enabled
    except Exception as e:  # noqa: BLE001 - 模块缺失/平台不支持 → supported=False
        logger.warning("desktop.autostart 不可用（%s），开关仅持久化未落地注册表", e)
        return {
            "supported": False,
            "enabled": payload.enabled,
            "applied": False,
            "message": "当前环境不支持开机自启注册表操作",
        }

    ok, detail = set_enabled(payload.enabled)
    logger.info("桌面控制面：开机自启设为 %s -> ok=%s（%s）", payload.enabled, ok, detail)
    return {
        "supported": ok,
        "enabled": payload.enabled if ok else (not payload.enabled),
        "applied": ok,
        "message": detail,
    }


@router.post("/open-log-dir")
def open_log_dir() -> dict:
    """打开日志目录（P0，系统文件管理器）。"""
    import os

    try:
        from utils.app_paths import get_data_dir

        log_dir = get_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(log_dir))  # type: ignore[attr-defined]  # Windows
        return {"ok": True, "path": str(log_dir)}
    except Exception as e:  # noqa: BLE001 - 打开失败返回错误信息，不崩溃
        logger.exception("desktop/open-log-dir 失败")
        raise HTTPException(status_code=500, detail=f"打开日志目录失败: {e}")


@router.post("/restart-backend")
def restart_backend() -> dict:
    """手动重启后端线程（P1，排障用）。

    复用壳 server 的看门狗重启能力：停旧线程后重建。
    """
    app = get_desktop_app()
    server = getattr(app, "server", None)
    if server is None or not hasattr(server, "_restart"):
        raise HTTPException(status_code=409, detail="后端托管未注册，无法重启")

    try:
        server._restart()
        logger.info("桌面控制面：手动重启后端线程")
        return {"ok": True, "message": "后端线程已重启"}
    except Exception as e:  # noqa: BLE001
        logger.exception("desktop/restart-backend 失败")
        raise HTTPException(status_code=500, detail=f"重启后端失败: {e}")


@router.post("/quit")
def quit_app() -> dict:
    """请求应用退出（P1，前端"退出应用"按钮）。

    触发壳的退出序列（K5）；退出过程中本响应尽力返回 ok，随后进程结束。
    """
    app = get_desktop_app()
    if app is None:
        raise HTTPException(status_code=409, detail="壳未注册，无法退出")
    quit_fn = getattr(app, "quit", None)
    if quit_fn is None:
        raise HTTPException(status_code=409, detail="壳无退出入口")
    # 异步触发：先返回响应，再执行退出，避免连接被中断
    import threading

    threading.Thread(target=quit_fn, daemon=True).start()
    logger.info("桌面控制面：已请求应用退出")
    return {"ok": True, "message": "正在退出"}
