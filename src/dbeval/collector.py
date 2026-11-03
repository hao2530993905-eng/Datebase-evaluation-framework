"""Persist normalized case results and run-level evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from .gold import GoldBundle, coverage_summary
from .models import CaseResult
from .util import write_json


def _safe_segment(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return normalized.strip("._") or "case"


class ResultCollector:
    """Write each case immediately, then emit a complete run envelope."""

    def __init__(self, run_dir: Path, project_id: str, gold: GoldBundle):
        self.run_dir = run_dir.resolve()
        self.project_id = project_id
        self.gold = gold
        self.results: List[CaseResult] = []
        self.environment: Dict[str, Any] = {}

    def set_environment(self, environment: Dict[str, Any]) -> None:
        self.environment = environment
        write_json(self.run_dir / "environment.json", environment)

    def record(self, result: CaseResult) -> None:
        self.results.append(result)
        case_dir = self.run_dir / "cases" / _safe_segment(result.case_id)
        write_json(case_dir / "result.json", result.as_dict())

    def finalize(self) -> Dict[str, Any]:
        envelope = {
            "schema_version": "1.0",
            "run_id": self.run_dir.name,
            "project_id": self.project_id,
            "gold_bundle_id": self.gold.bundle_id,
            "gold_content_hash": self.gold.content_hash(),
            "coverage": coverage_summary(self.gold),
            "results": [result.as_dict() for result in self.results],
        }
        write_json(self.run_dir / "results.json", envelope)
        return envelope
