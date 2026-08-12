"""配置存储层：ConfigStore。

职责：
  1. 从 config/app.yaml 加载应用主配置（providers / models / active_school）
  2. 从 config/schools/<active_school>.yaml 加载学校数据源
  3. 写入配置时自动备份到 .bak 并按需原子替换
  4. 维护 version 计数器，供 embedding / agent 缓存检测配置是否变更
  5. 配置损坏时自动 fallback 到 config/app.yaml.bak，再 fallback 到 defaults.py
  6. 不直接保存 API key，只保存环境变量名；启动时加载 .env

单例：整个 Streamlit 进程只应有一个实例，通过 get_instance() 获取。
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from config.defaults import DEFAULT_CONFIG
from config.schema import (
    AppConfig,
    CrawlConfig,
    ModelProfile,
    ModelsConfig,
    ProviderConfig,
    SchoolConfig,
    SourceConfig,
)

logger = logging.getLogger(__name__)


class ConfigStore:
    """配置单一数据源。"""

    _instance: Optional["ConfigStore"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, config_dir: Optional[Path] = None) -> "ConfigStore":
        """获取 ConfigStore 单例。第一次调用时初始化。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    if config_dir is None:
                        config_dir = Path(__file__).parent
                    cls._instance = cls(config_dir)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """主要用于测试。"""
        cls._instance = None

    def __init__(self, config_dir: Path):
        self._config_dir = Path(config_dir)
        self._app_yaml = self._config_dir / "app.yaml"
        self._schools_dir = self._config_dir / "schools"
        self._schools_dir.mkdir(parents=True, exist_ok=True)

        self._version = 0
        self._data: AppConfig = self._load_app_config()
        self._load_dotenv()

    # ---------- 读取 API ----------

    @property
    def version(self) -> int:
        return self._version

    @property
    def app_config(self) -> AppConfig:
        return self._data

    def get_model(self, task: str) -> tuple[ProviderConfig, str]:
        """获取指定任务的 (provider_config, model_name)。

        Args:
            task: "extraction" | "qa" | "todo" | "embedding"
        """
        profile = getattr(self._data.models, task)
        provider = self._data.providers.get(profile.provider)
        if provider is None:
            raise RuntimeError(f"任务 {task} 引用的 provider '{profile.provider}' 不存在")
        return provider, profile.model

    def get_provider(self, name: str) -> ProviderConfig:
        if name not in self._data.providers:
            raise KeyError(f"供应商 {name} 不存在")
        return self._data.providers[name]

    def get_provider_names(self) -> list[str]:
        return list(self._data.providers.keys())

    def get_model_names(self) -> dict[str, str]:
        return {
            "extraction": self._data.models.extraction.model,
            "qa": self._data.models.qa.model,
            "todo": self._data.models.todo.model,
            "embedding": self._data.models.embedding.model,
        }

    def get_school(self) -> SchoolConfig:
        """加载当前活跃学校的数据源配置。"""
        path = self._schools_dir / f"{self._data.active_school}.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"学校配置文件不存在: {path}\n"
                f"请在 config/schools/ 下创建 {self._data.active_school}.yaml"
            )
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return SchoolConfig(**raw)

    def get_crawl(self) -> CrawlConfig:
        return self._data.crawl

    def get_active_school_code(self) -> str:
        return self._data.active_school

    def get_api_key(self, provider_name: str) -> str:
        """按供应商配置的环境变量名，从 os.environ 读取 API key。"""
        provider = self.get_provider(provider_name)
        if not provider.api_key_env:
            return ""
        return os.environ.get(provider.api_key_env, "").strip()

    def get_api_key_status(self, provider_name: str) -> bool:
        """返回该供应商的 API key 是否已配置。"""
        return bool(self.get_api_key(provider_name))

    def export_for_ui(self) -> dict:
        """导出给 UI 使用的配置（api_key 用状态标记代替，不泄露）。"""
        providers = {}
        for name, p in self._data.providers.items():
            providers[name] = {
                "name": p.name,
                "base_url": p.base_url,
                "api_key_env": p.api_key_env,
                "api_key_status": self.get_api_key_status(name),
            }
        return {
            "active_school": self._data.active_school,
            "models": self._data.models.model_dump(),
            "providers": providers,
            "crawl": self._data.crawl.model_dump(),
        }

    def get_disk_info(self) -> dict:
        """获取配置文件的磁盘信息。"""
        info = {"path": str(self._app_yaml), "exists": self._app_yaml.exists()}
        if info["exists"]:
            stat = self._app_yaml.stat()
            info["last_modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
        return info

    def check_embedding_model_changed(self, previous_model: str) -> bool:
        """比较当前 embedding 模型与传入的历史值。"""
        _, _, current_model = self.get_model("embedding")
        return current_model != previous_model

    # ---------- 写入 API ----------

    def save_models(self, models: ModelsConfig) -> dict:
        """保存模型配置。"""
        new_data = AppConfig(
            active_school=self._data.active_school,
            models=models,
            providers=self._data.providers,
            crawl=self._data.crawl,
        )
        return self._save_app_config(new_data)

    def save_providers(self, providers: dict[str, ProviderConfig]) -> dict:
        """保存供应商配置。"""
        new_data = AppConfig(
            active_school=self._data.active_school,
            models=self._data.models,
            providers=providers,
            crawl=self._data.crawl,
        )
        return self._save_app_config(new_data)

    def save_crawl(self, crawl: CrawlConfig) -> dict:
        """保存全局抓取参数。"""
        new_data = AppConfig(
            active_school=self._data.active_school,
            models=self._data.models,
            providers=self._data.providers,
            crawl=crawl,
        )
        return self._save_app_config(new_data)

    def save_sources(self, school_code: str, school_config: SchoolConfig) -> dict:
        """保存学校数据源配置。"""
        path = self._schools_dir / f"{school_code}.yaml"
        self._backup_and_write(path, school_config.model_dump())
        return {"ok": True, "path": str(path)}

    def force_reload(self) -> dict:
        """强制从磁盘重新加载 app.yaml。"""
        self._data = self._load_app_config()
        self._version += 1
        return {"ok": True, "version": self._version}

    # ---------- 内部加载 / 保存 ----------

    def _load_app_config(self) -> AppConfig:
        """加载 app.yaml，失败时 fallback 到 .bak → defaults。"""
        # 第一层：app.yaml
        try:
            if self._app_yaml.exists():
                with open(self._app_yaml, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                config = AppConfig(**raw)
                logger.info("已从 %s 加载配置", self._app_yaml)
                return config
        except Exception as e:
            logger.warning("app.yaml 加载失败: %s，尝试回退到备份", e)

        # 第二层：app.yaml.bak
        bak = self._app_yaml.with_suffix(".yaml.bak")
        try:
            if bak.exists():
                with open(bak, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                config = AppConfig(**raw)
                logger.warning("已从备份 %s 加载配置", bak)
                return config
        except Exception as e:
            logger.warning("备份配置加载失败: %s", e)

        # 第三层：硬编码默认
        logger.warning("使用内置默认配置")
        return DEFAULT_CONFIG

    def _save_app_config(self, config: AppConfig) -> dict:
        """保存 app.yaml 并更新内存中的配置。"""
        old_dict = self._data.model_dump()
        new_dict = config.model_dump()
        if old_dict == new_dict:
            return {"ok": True, "changed": False, "message": "配置无变化"}

        self._backup_and_write(self._app_yaml, new_dict)
        self._data = config
        self._version += 1
        logger.info("配置已保存，version=%d", self._version)
        return {"ok": True, "changed": True, "version": self._version}

    def _backup_and_write(self, path: Path, data: dict) -> None:
        """原子化写入：先写 .tmp → 旧文件备份为 .bak → .tmp 重命名为目标。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            # 简短头部注释，说明文件用途
            if path.name == "app.yaml":
                f.write("# 校园通知智能助手 - 主配置\n")
                f.write("# API Key 不在本文件保存，请通过 api_key_env 引用 .env 中的环境变量\n\n")
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

        if path.exists():
            bak = path.with_suffix(path.suffix + ".bak")
            try:
                shutil.copy2(str(path), str(bak))
            except Exception as e:
                logger.warning("备份失败（继续写入）: %s", e)

        os.replace(str(tmp), str(path))

    def _load_dotenv(self) -> None:
        """加载项目根目录 .env 到 os.environ。

        ConfigStore 作为统一配置入口，首次实例化时触发。
        """
        dotenv_path = self._config_dir.parent / ".env"
        if dotenv_path.exists():
            load_dotenv(dotenv_path)
            logger.debug("已加载 .env: %s", dotenv_path)

    # ---------- 便捷工厂方法（供 UI / 服务调用） ----------

    @staticmethod
    def build_models_config(
        extraction: dict,
        qa: dict,
        todo: dict,
        embedding: dict,
    ) -> ModelsConfig:
        return ModelsConfig(
            extraction=ModelProfile(**extraction),
            qa=ModelProfile(**qa),
            todo=ModelProfile(**todo),
            embedding=ModelProfile(**embedding),
        )

    @staticmethod
    def build_school_config(name: str, code: str, sources: list[dict]) -> SchoolConfig:
        return SchoolConfig(
            name=name,
            code=code,
            sources=[SourceConfig(**s) for s in sources],
        )

    @staticmethod
    def build_providers_config(providers: dict[str, dict]) -> dict[str, ProviderConfig]:
        return {k: ProviderConfig(**v) for k, v in providers.items()}
