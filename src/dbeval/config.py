"""Project configuration validation and secret-safe loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import ConfigurationError
from .metrics import DIMENSION_NAMES
from .util import expand_environment, read_json, redact, resolve_path


SUPPORTED_BACKENDS = {"sqlite", "postgresql", "mysql", "custom"}


@dataclass
class ProjectConfig:
    path: Path
    root: Path
    raw: Dict[str, Any]

    @property
    def project_id(self) -> str:
        return str(self.raw["project_id"])

    @property
    def database(self) -> Dict[str, Any]:
        return self.raw["database"]

    @property
    def backend(self) -> str:
        return str(self.database["backend"])

    @property
    def database_path(self) -> Optional[Path]:
        value = self.database.get("path")
        return resolve_path(self.root, expand_environment(str(value))) if value else None

    @property
    def execution(self) -> Dict[str, Any]:
        return self.raw.get("execution", {})

    @property
    def features(self) -> Dict[str, bool]:
        return {str(key): bool(value) for key, value in self.raw.get("features", {}).items()}

    def public_snapshot(self) -> Dict[str, Any]:
        return redact(self.raw)


def _require_string(value: Dict[str, Any], key: str, location: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ConfigurationError(f"{location}.{key} 必须是非空字符串")
    return item


def load_project(path: Path, environ: Optional[Dict[str, str]] = None) -> ProjectConfig:
    source = path.resolve()
    raw = read_json(source)
    if raw.get("schema_version") != "1.0":
        raise ConfigurationError("项目 schema_version 必须为 '1.0'")
    _require_string(raw, "project_id", "project")

    software = raw.get("software")
    if not isinstance(software, dict):
        raise ConfigurationError("project.software 必须是对象")
    _require_string(software, "name", "project.software")

    database = raw.get("database")
    if not isinstance(database, dict):
        raise ConfigurationError("project.database 必须是对象")
    backend = _require_string(database, "backend", "project.database")
    if backend not in SUPPORTED_BACKENDS:
        raise ConfigurationError(
            f"不支持的数据库后端 {backend!r}，可选值: {', '.join(sorted(SUPPORTED_BACKENDS))}"
        )

    if backend == "sqlite" and not database.get("path") and not database.get("initialize"):
        raise ConfigurationError("SQLite 项目必须提供 database.path 或 database.initialize")

    weights = raw.get("dimension_weights", {dimension: 1.0 for dimension in DIMENSION_NAMES})
    if set(weights) != set(DIMENSION_NAMES):
        raise ConfigurationError("dimension_weights 必须恰好包含 DB1-DB8")
    if any(not isinstance(item, (int, float)) or item < 0 for item in weights.values()):
        raise ConfigurationError("dimension_weights 必须是非负数")
    if sum(weights.values()) <= 0:
        raise ConfigurationError("dimension_weights 权重之和必须大于 0")

    execution = raw.get("execution", {})
    if not isinstance(execution, dict):
        raise ConfigurationError("execution 必须是对象")
    timeout = execution.get("case_timeout_seconds", 30)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigurationError("execution.case_timeout_seconds 必须大于 0")

    # Ensure configured local files resolve under the project root unless absolute.
    initialize = database.get("initialize", {})
    if initialize:
        if not isinstance(initialize, dict):
            raise ConfigurationError("database.initialize 必须是对象")
        for key in ("schema", "seed"):
            value = initialize.get(key)
            if value and not resolve_path(source.parent, expand_environment(str(value), environ)).is_file():
                raise ConfigurationError(f"初始化文件不存在: {value}")

    return ProjectConfig(path=source, root=source.parent, raw=raw)
