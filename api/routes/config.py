"""配置模块：models/providers/sources 读写 + disk/reload + 连通性测试（盘点 §5.6 配置映射表）。

写入权语义（§5.8）：app.yaml / schools/*.yaml 的写入唯一归后端 API 进程；
ConfigStore 单例内已加写锁串行化「读-改-写」，_backup_and_write 的原子写机制不变。
CLI / scheduler 只读配置（阶段 6 并入后与 API 同进程，天然遵守唯一写者约定）。

失败语义：PUT 返回 config_service 统一结构 HTTP 200 + {"ok": false, "error": ...}，
schema 级校验错误由 FastAPI 自动返回 422。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import require_auth
from api.schemas import (
    ConfigMutationResult,
    ConfigView,
    DiskInfo,
    ModelsView,
    ProviderView,
    ReloadResult,
    TestModelRequest,
    TestModelResult,
    TestSourceRequest,
    TestSourceResult,
)
from config.schema import ModelsConfig, ProviderConfig, SchoolConfig, SourceConfig
from services.config_service import (
    force_reload_config,
    get_config_disk_info,
    get_config_for_ui,
    get_models_for_ui,
    get_providers_for_ui,
    get_sources_for_ui,
    test_model_connection,
    test_source_url,
    update_models,
    update_providers,
    update_sources,
)

router = APIRouter(
    prefix="/config",
    tags=["config"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=ConfigView)
def get_config() -> dict:
    """完整配置视图（api_key 不泄露，仅状态标记）。"""
    return get_config_for_ui()


@router.get("/models", response_model=ModelsView)
def get_models() -> dict:
    """各任务模型配置。"""
    return get_models_for_ui()


@router.put("/models", response_model=ConfigMutationResult)
def put_models(body: ModelsConfig) -> dict:
    """保存模型配置（body 复用 config.schema.ModelsConfig，校验由 schema 完成）。"""
    return update_models(body.model_dump())


@router.get("/providers", response_model=dict[str, ProviderView])
def get_providers() -> dict:
    """供应商列表（含 API key 状态）。"""
    return get_providers_for_ui()


@router.put("/providers", response_model=ConfigMutationResult)
def put_providers(body: dict[str, ProviderConfig]) -> dict:
    """保存供应商配置（body 复用 config.schema.ProviderConfig）。"""
    return update_providers({k: v.model_dump() for k, v in body.items()})


@router.get("/sources", response_model=SchoolConfig)
def get_sources() -> dict:
    """当前学校数据源配置。"""
    return get_sources_for_ui()


@router.put("/sources", response_model=ConfigMutationResult)
def put_sources(body: list[SourceConfig]) -> dict:
    """保存当前学校数据源配置（body 复用 config.schema.SourceConfig）。"""
    return update_sources([s.model_dump() for s in body])


@router.get("/disk", response_model=DiskInfo)
def get_disk() -> dict:
    """配置文件磁盘信息（路径 / 是否存在 / 最后修改时间）。"""
    return get_config_disk_info()


@router.post("/reload", response_model=ReloadResult)
def reload_config() -> dict:
    """强制从磁盘重新加载 app.yaml（version 递增）。"""
    return force_reload_config()


@router.post("/test-source", response_model=TestSourceResult)
def test_source(body: TestSourceRequest) -> dict:
    """测试数据源 URL 可达性并统计发现的链接数（长耗时，前端 loading 态）。"""
    return test_source_url(body.url, timeout=body.timeout)


@router.post("/test-model", response_model=TestModelResult)
def test_model(body: TestModelRequest) -> dict:
    """测试模型连接可用性（发送最小 chat completion，长耗时，前端 loading 态）。"""
    return test_model_connection(body.provider, body.model, timeout=body.timeout)
