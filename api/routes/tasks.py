"""异步任务模块（阶段 4）：POST /tasks 提交 → GET /tasks/{id} 轮询进度。

任务类型注册表见 api/tasks/workers.py；执行器为 TaskManager（lifespan 创建，
挂在 app.state.task_manager）。任务结果（含错误）通过 GET /tasks/{id} 轮询获得。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import require_auth
from api.schemas import TaskCreateRequest, TaskCreateResult, TaskView
from api.tasks.workers import WORKERS

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_auth)],
)


def get_task_manager(request: Request):
    """从 app.state 获取 TaskManager（lifespan 注入；测试可手动设置）。"""
    manager = getattr(request.app.state, "task_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="任务管理器未初始化")
    return manager


@router.post("", status_code=202, response_model=TaskCreateResult)
def create_task(request: Request, body: TaskCreateRequest) -> TaskCreateResult:
    """提交一个异步任务，返回 task_id（前端轮询 GET /tasks/{id} 直到完成）。"""
    if body.type not in WORKERS:
        raise HTTPException(status_code=400, detail=f"未知任务类型: {body.type}")
    manager = get_task_manager(request)
    task_id = manager.submit(body.type, body.params)
    return TaskCreateResult(task_id=task_id, type=body.type, status="queued")


@router.get("/{task_id}", response_model=TaskView)
def get_task(request: Request, task_id: int) -> TaskView:
    """查询任务状态 / 进度 / 结果（轮询点）。"""
    task = get_task_manager(request).get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return TaskView(**task)


@router.get("", response_model=list[TaskView])
def list_tasks(
    request: Request,
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[TaskView]:
    """最近任务列表（可按状态过滤，按 id 倒序）。"""
    tasks = get_task_manager(request).list(status=status, limit=limit)
    return [TaskView(**t) for t in tasks]
