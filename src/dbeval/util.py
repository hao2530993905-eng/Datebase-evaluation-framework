"""Stable serialization, hashing, path, and redaction helpers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from .errors import ConfigurationError


SECRET_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|credential|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)((?:password|passwd|secret|token|api[_-]?key|private[_-]?key)"
    r"(?:[\"']?\s*[:=]\s*[\"']?))([^\"'\s,;}]+)"
)
ENV_PATTERN = re.compile(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Mapping[str, Any], excluded_keys: Iterable[str] = ()) -> str:
    excluded = set(excluded_keys)
    payload = {key: item for key, item in value.items() if key not in excluded}
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"JSON 格式错误 {path}:{exc.lineno}:{exc.colno}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"JSON 顶层必须是对象: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    os.replace(temporary, path)


def resolve_path(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def ensure_within(path: Path, roots: Iterable[Path], label: str) -> Path:
    resolved = path.resolve()
    resolved_roots = [root.resolve() for root in roots]
    if not any(resolved == root or root in resolved.parents for root in resolved_roots):
        raise ConfigurationError(f"{label} 超出允许目录: {resolved}")
    return resolved


def expand_environment(value: str, environ: Optional[Mapping[str, str]] = None) -> str:
    source = environ if environ is not None else os.environ

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in source:
            raise ConfigurationError(f"缺少配置所需环境变量: {name}")
        return source[name]

    return ENV_PATTERN.sub(replace, value)


def redact(value: Any, key: str = "") -> Any:
    if SECRET_KEY_PATTERN.search(key):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {item_key: redact(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        # Redact credentials embedded in URLs and DSNs.
        redacted = re.sub(r"(://[^:/@\s]+:)[^@\s]+(@)", r"\1***REDACTED***\2", value)
        return SECRET_ASSIGNMENT_PATTERN.sub(r"\1***REDACTED***", redacted)
    return value


def relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def json_safe(value: Any) -> Any:
    """Convert adapter values to deterministic JSON-safe primitives."""
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return repr(value)


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return float(ordered[index])
