"""FastAPI 应用工厂。

- 路由统一挂载于 /api/v1（盘点 §5.6 映射表）
- CORS 白名单（本地开发默认放行，生产收紧）
- lifespan 拉起 scheduler（阶段 6 接入，当前占位）
- 挂载前端静态产物（阶段 7 接入）

启动：uvicorn api.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import notices, reminders, subscriptions, todos

logger = logging.getLogger(__name__)

# 前端地址白名单（生产收紧为实际部署源）
CORS_ORIGINS = [
    "http://localhost:5173",  # Vite dev server
    "http://127.0.0.1:5173",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动/关闭时执行。

    阶段 6 在此拉起 scheduler（单进程运行，app.yaml 写入权唯一归后端进程）。
    """
    logger.info("后端启动")
    yield
    logger.info("后端关闭")


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
    app.include_router(notices.router, prefix="/api/v1")

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

    # 阶段 7 接入：挂载前端构建产物
    # from fastapi.staticfiles import StaticFiles
    # app.mount("/static", StaticFiles(directory="frontend/dist"), name="static")

    return app


app = create_app()
