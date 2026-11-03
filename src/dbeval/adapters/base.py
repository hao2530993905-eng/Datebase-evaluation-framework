"""Stable database adapter interface.

Adding an engine should not require changes to scoring or reporting. Adapters
own connection mechanics, health APIs, backup commands, and normalized
capability declarations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AdapterCapabilities:
    sql_execution: bool = False
    transactions: bool = False
    concurrency: bool = False
    native_backup_restore: bool = False
    internal_integrity_check: bool = False
    foreign_key_check: bool = False
    read_write_probe: bool = False
    schema_introspection: bool = False

    def as_dict(self) -> Dict[str, bool]:
        return {
            key: bool(value)
            for key, value in self.__dict__.items()
        }


@dataclass
class DatabaseIdentity:
    backend: str
    version: str
    location: str
    metadata: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "version": self.version,
            "location": self.location,
            "metadata": self.metadata,
        }


class DatabaseAdapter(ABC):
    """Factory for isolated case sessions."""

    @property
    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        raise NotImplementedError

    @abstractmethod
    def detect(self) -> DatabaseIdentity:
        raise NotImplementedError

    @abstractmethod
    def prepare(self, work_dir: Path) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def open_session(self, case_dir: Path) -> Any:
        raise NotImplementedError

    @abstractmethod
    def inspect_schema(self) -> Dict[str, Any]:
        raise NotImplementedError

    def tool_versions(self) -> Dict[str, Optional[str]]:
        return {}

    def close(self) -> None:
        return None


class DatabaseSession(ABC):
    @abstractmethod
    def execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[List[Any]]:
        raise NotImplementedError

    @abstractmethod
    def benchmark(
        self,
        sql: str,
        *,
        warmup: int,
        repeat: int,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def backup_restore(self, destination: Path) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def query_backup(
        self,
        backup_path: Path,
        sql: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[List[Any]]:
        raise NotImplementedError

    @abstractmethod
    def health_probe(self, check: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
