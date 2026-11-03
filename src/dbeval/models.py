"""Shared typed records used by adapters, runner, scorer, and reporters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


SUCCESS_STATUSES = {"passed", "failed", "skipped", "not_applicable"}
ALL_STATUSES = SUCCESS_STATUSES | {"environment_error", "tool_error", "framework_error"}


@dataclass
class CheckResult:
    name: str
    status: str
    expected: Any = None
    actual: Any = None
    message: str = ""
    duration_ms: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
            "duration_ms": round(self.duration_ms, 3),
        }


@dataclass
class Measurement:
    name: str
    value: float
    unit: str
    sample_count: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": round(self.value, 6),
            "unit": self.unit,
            "sample_count": self.sample_count,
        }


@dataclass
class Finding:
    id: str
    severity: str
    title: str
    evidence: str = ""
    location: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "title": self.title,
            "evidence": self.evidence,
            "location": self.location,
        }


@dataclass
class CaseResult:
    case_id: str
    dimension: str
    indicator: str
    title: str
    status: str
    point_score: Optional[float] = None
    weight: float = 1.0
    applicable: bool = True
    applicability_reason: str = ""
    checks: List[CheckResult] = field(default_factory=list)
    measurements: List[Measurement] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    error_kind: str = ""
    error_message: str = ""
    duration_ms: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "dimension": self.dimension,
            "indicator": self.indicator,
            "title": self.title,
            "status": self.status,
            "point_score": self.point_score,
            "weight": self.weight,
            "applicable": self.applicable,
            "applicability_reason": self.applicability_reason,
            "checks": [item.as_dict() for item in self.checks],
            "measurements": [item.as_dict() for item in self.measurements],
            "findings": [item.as_dict() for item in self.findings],
            "evidence": self.evidence,
            "error_kind": self.error_kind,
            "error_message": self.error_message,
            "duration_ms": round(self.duration_ms, 3),
        }


@dataclass
class CaseSpec:
    id: str
    dimension: str
    indicator: str
    title: str
    case_type: str
    raw: Dict[str, Any]
    weight: float = 1.0
    required: bool = True
