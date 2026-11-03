"""Cross-backend database detection and evidence collection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .adapters import create_adapter


def detect_database(project, work_dir: Path) -> Dict[str, Any]:
    adapter = create_adapter(project)
    try:
        identity = adapter.detect()
        prepared = None
        schema = None
        if adapter.capabilities.sql_execution:
            prepared = adapter.prepare(work_dir)
            if adapter.capabilities.schema_introspection:
                schema = adapter.inspect_schema()
        return {
            "identity": identity.as_dict(),
            "capabilities": adapter.capabilities.as_dict(),
            "prepared": prepared,
            "schema": schema,
            "tool_versions": adapter.tool_versions(),
        }
    finally:
        adapter.close()


def detect_from_path(path: Path) -> Dict[str, Any]:
    """Best-effort artifact detection that does not mutate the artifact."""
    if not path.exists():
        return {"recognized": False, "reason": "path does not exist"}
    if path.is_file():
        with path.open("rb") as handle:
            header = handle.read(16)
        if header == b"SQLite format 3\x00":
            return {
                "recognized": True,
                "backend": "sqlite",
                "location": str(path.resolve()),
                "evidence": {"file_header": "SQLite format 3"},
            }
        return {
            "recognized": False,
            "reason": "unknown file signature",
            "location": str(path.resolve()),
        }
    return {
        "recognized": False,
        "reason": "directory input requires project configuration",
        "location": str(path.resolve()),
    }
