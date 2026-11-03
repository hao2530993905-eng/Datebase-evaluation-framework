"""Database adapter registry and plugin loader."""

import importlib
from typing import Any, Callable, Dict

from ..errors import ConfigurationError
from .base import AdapterCapabilities, DatabaseAdapter
from .sqlite import SQLiteAdapter

__all__ = ["AdapterCapabilities", "DatabaseAdapter", "SQLiteAdapter", "create_adapter"]


AdapterFactory = Callable[[Any], DatabaseAdapter]
ADAPTER_FACTORIES: Dict[str, AdapterFactory] = {}


def register_adapter(backend: str, factory: AdapterFactory) -> None:
    if not backend:
        raise ValueError("backend 不能为空")
    if not callable(factory):
        raise TypeError("adapter factory 必须可调用")
    ADAPTER_FACTORIES[backend] = factory


def create_adapter(project) -> DatabaseAdapter:
    factory = ADAPTER_FACTORIES.get(project.backend)
    plugin = project.database.get("adapter_plugin")
    if factory is None and plugin:
        try:
            imported = importlib.import_module(str(plugin))
        except ImportError as exc:
            raise ConfigurationError(f"无法加载数据库适配器插件 {plugin!r}: {exc}") from exc
        register = getattr(imported, "register", None)
        if not callable(register):
            raise ConfigurationError(f"适配器插件 {plugin!r} 必须导出 register(registry)")
        register({"register_adapter": register_adapter, "factories": ADAPTER_FACTORIES})
        factory = ADAPTER_FACTORIES.get(project.backend)
    if factory is None:
        raise ConfigurationError(
            f"数据库后端 {project.backend!r} 没有适配器；请安装/配置 adapter_plugin"
        )
    adapter = factory(project)
    if not isinstance(adapter, DatabaseAdapter):
        raise ConfigurationError("数据库适配器工厂返回值必须实现 DatabaseAdapter")
    return adapter


register_adapter("sqlite", lambda project: SQLiteAdapter(project=project))
