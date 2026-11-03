"""Gold mutation audit separate from product quality."""

from __future__ import annotations

from typing import Any, Dict

from .errors import ConfigurationError


VALID_MUTATION_STATUSES = {
    "killed",
    "survived",
    "equivalent",
    "invalid",
    "uncovered",
}


def mutation_summary(raw: Dict[str, Any]) -> Dict[str, Any]:
    if raw.get("schema_version") != "1.0":
        raise ConfigurationError("变异报告 schema_version 必须为 '1.0'")
    mutants = raw.get("mutants")
    if not isinstance(mutants, list):
        raise ConfigurationError("mutation report 需要 mutants 数组")
    counts = {key: 0 for key in VALID_MUTATION_STATUSES}
    details = []
    for index, mutant in enumerate(mutants):
        if not isinstance(mutant, dict) or not mutant.get("id"):
            raise ConfigurationError(f"mutants[{index}] 缺少 id")
        status = str(mutant.get("status", ""))
        if status not in VALID_MUTATION_STATUSES:
            raise ConfigurationError(f"{mutant['id']} 的变异状态无效: {status}")
        counts[status] += 1
        details.append(
            {
                "id": mutant["id"],
                "operator": mutant.get("operator", ""),
                "location": mutant.get("location", ""),
                "status": status,
                "killed_by": mutant.get("killed_by", []),
            }
        )
    evaluable = counts["killed"] + counts["survived"] + counts["uncovered"]
    mutation_score = counts["killed"] / evaluable if evaluable else None
    return {
        "schema_version": "1.0",
        "mutation_score": round(mutation_score, 4) if mutation_score is not None else None,
        "evaluable_mutants": evaluable,
        "counts": counts,
        "mutants": details,
        "interpretation": (
            "Mutation score is a Gold sensitivity metric and is not included in the product quality score."
        ),
    }
