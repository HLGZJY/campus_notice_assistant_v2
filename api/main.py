"""FastAPI 应用工厂。

- 路由统一挂载于 /api/v1（盘点 §5.6 映射表）
- CORS 白名单（本地开发默认放行，生产收紧）
- lifespan 拉起异步任务管理器（阶段 4）+ 调度器（阶段 6）
- 挂载前端静态产物（阶段 7 接入）

启动：uvicorn api.main:app --reload
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import config, events, notices, qa, reminders, scheduler, subscriptions, tasks, todos
from api.tasks.manager import TaskManager
from scheduler import start_scheduler

logger = logging.getLogger(__name__)

# 前端地址白名单（生产收紧为实际部署源）
CORS_ORIGINS = [
    "http://localhost:5173",  # Vite dev server
    "http://127.0.0.1:5173",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：拉起异步任务管理器（阶段 4）+ 调度器（阶段 6）。

    调度器单进程并入后端（§5.8 app.yaml 写入权唯一）；test 模式或
    scheduler.enabled=false 时不拉起（app.state.scheduler=None）。
    """
    manager = TaskManager()
    app.state.task_manager = manager
    await manager.start()

    app.state.scheduler = None
    if os.environ.get("APP_ENV") != "test":
        app.state.scheduler = start_scheduler()
    logger.info(
        "后端启动（异步任务管理器已就绪%s）",
        "，调度器已并入" if app.state.scheduler is not None else "，调度器未启用",
    )
    try:
        yield
    finally:
        if app.state.scheduler is not None:
            app.state.scheduler.stop()
            logger.info("调度器已停止")
        await manager.stop()
        logger.info("后端关闭（异步任务管理器已停止）")


def create_app() -> FastAPI:
    app = FastAPI(
        title="校园通知智能助手 API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 业务路由（统一 /api/v1 前缀）
    # 顺序：subscriptions 的 /notices/count、/notices/matched-ids 等精确路径须先于
    # notices 的 /notices/{notice_id} 注册，否则会被通配段捕获而 422（Starlette 顺序匹配）。
    app.include_router(subscriptions.notice_router, prefix="/api/v1")
    app.include_router(todos.notice_router, prefix="/api/v1")
    app.include_router(todos.router, prefix="/api/v1")
    app.include_router(reminders.router, prefix="/api/v1")
    app.include_router(subscriptions.router, prefix="/api/v1")
    app.include_router(config.router, prefix="/api/v1")
    app.include_router(scheduler.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(qa.router, prefix="/api/v1")
    app.include_router(notices.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")

    @app.get("/api/v1/health", tags=["system"])
    def health() -> dict:
        """健康检查：DB 探活 + 通知计数。"""
        from services.notice_service import get_status_counts

        counts = get_status_counts()
        return {
            "status": "ok",
            "version": "1.0.0",
            "db": "ok",
            "notices": sum(counts.values()),
        }

    # 阶段 7 接入：挂载前端构建产物（Phase 8 收尾：启用前端静态文件）
    # 如果 frontend/dist 存在，则挂载为 /static 并添加 SPA fallback
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    import pathlib

    dist_path = pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if dist_path.exists():
        app.mount("/static", StaticFiles(directory=str(dist_path)), name="static")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # 返回静态文件（如果存在），否则返回 index.html
            possible = dist_path / full_path
            index = dist_path / "index.html"
            if possible.exists() and possible.is_file():
                return FileResponse(possible)
            return FileResponse(index)

    return app


app = create_app()
