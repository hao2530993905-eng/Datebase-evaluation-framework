"""Deterministic scoring for DB1-DB8.

Missing tests are reported as coverage gaps, not silently treated as passes.
When an indicator has no evaluable result, its quality is undefined (null).
Environment and tool failures are excluded from the quality denominator but
remain visible in execution completeness.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from .gold import GoldBundle, coverage_summary
from .metrics import DIMENSION_INDICATORS, DIMENSION_NAMES, INDICATOR_MAP
from .models import CaseResult


SECURITY_CAPS = {
    "critical": 20.0,
    "high": 40.0,
    "medium": 65.0,
    "low": 85.0,
}
ERROR_STATUSES = {"environment_error", "tool_error", "framework_error"}
SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def _deduplicate_findings(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for finding in findings:
        key = str(
            finding.get("id")
            or f"{finding.get('title', '')}|{finding.get('location', '')}"
        )
        previous = unique.get(key)
        if previous is None or SEVERITY_ORDER.get(
            str(finding.get("severity")), -1
        ) > SEVERITY_ORDER.get(str(previous.get("severity")), -1):
            unique[key] = finding
    return list(unique.values())


def score_results(
    project: Dict[str, Any],
    bundle: GoldBundle,
    results: Iterable[CaseResult],
) -> Dict[str, Any]:
    result_list = list(results)
    by_indicator: Dict[str, List[CaseResult]] = {
        indicator_id: [] for indicator_id in INDICATOR_MAP
    }
    for result in result_list:
        by_indicator.setdefault(result.indicator, []).append(result)

    indicator_scores: Dict[str, Dict[str, Any]] = {}
    for indicator_id, indicator in INDICATOR_MAP.items():
        cases = by_indicator.get(indicator_id, [])
        evaluable = [
            item
            for item in cases
            if item.applicable
            and item.status in {"passed", "failed"}
            and item.point_score is not None
        ]
        observed_weight = sum(item.weight for item in evaluable)
        quality = None
        if observed_weight > 0:
            quality = (
                sum(item.weight * float(item.point_score) for item in evaluable)
                / observed_weight
                * 100.0
            )
        error_cases = [item for item in cases if item.status in ERROR_STATUSES]
        not_applicable = [item for item in cases if item.status == "not_applicable"]
        if not cases:
            status = "missing"
        elif quality is None and error_cases:
            status = "execution_incomplete"
        elif quality is None and not_applicable:
            status = "not_applicable"
        elif quality is None:
            status = "not_evaluated"
        else:
            status = "evaluated"
        indicator_scores[indicator_id] = {
            "id": indicator.id,
            "dimension": indicator.dimension,
            "name": indicator.name,
            "description": indicator.description,
            "default_weight": indicator.default_weight,
            "score": _round(quality),
            "status": status,
            "case_count": len(cases),
            "evaluated_case_count": len(evaluable),
            "error_case_count": len(error_cases),
            "not_applicable_count": len(not_applicable),
            "observed_case_weight": round(observed_weight, 6),
        }

    dimension_scores: Dict[str, Dict[str, Any]] = {}
    for dimension in DIMENSION_NAMES:
        indicators = DIMENSION_INDICATORS[dimension]
        available = [
            indicator_scores[item.id]
            for item in indicators
            if indicator_scores[item.id]["score"] is not None
        ]
        total_available_weight = sum(item["default_weight"] for item in available)
        raw_score = None
        if total_available_weight > 0:
            raw_score = (
                sum(
                    item["default_weight"] * float(item["score"])
                    for item in available
                )
                / total_available_weight
            )

        findings = _deduplicate_findings(
            [
                finding.as_dict()
                for result in result_list
                if result.dimension == dimension
                for finding in result.findings
            ]
        )
        cap_applied = None
        if dimension == "DB7" and raw_score is not None:
            for severity in ("critical", "high", "medium", "low"):
                if any(item.get("severity") == severity for item in findings):
                    cap_applied = SECURITY_CAPS[severity]
                    break
        final_score = raw_score
        if final_score is not None and cap_applied is not None:
            final_score = min(final_score, cap_applied)

        covered_indicators = len(available)
        dimension_error_count = sum(
            1
            for result in result_list
            if result.dimension == dimension and result.status in ERROR_STATUSES
        )
        if final_score is None:
            dimension_status = (
                "execution_incomplete" if dimension_error_count else "not_evaluated"
            )
        elif covered_indicators < len(indicators):
            dimension_status = "incomplete"
        else:
            dimension_status = "evaluated"
        dimension_scores[dimension] = {
            "id": dimension,
            "name": DIMENSION_NAMES[dimension],
            "score": _round(final_score),
            "raw_score_before_hard_gate": _round(raw_score),
            "score_cap": cap_applied,
            "status": dimension_status,
            "indicator_count": len(indicators),
            "evaluated_indicator_count": covered_indicators,
            "indicator_coverage": round(covered_indicators / len(indicators), 4),
            "execution_error_count": dimension_error_count,
            "findings": findings,
            "indicators": [indicator_scores[item.id] for item in indicators],
        }

    configured_weights = {
        str(key): float(value) for key, value in project.get("dimension_weights", {}).items()
    }
    if not configured_weights:
        configured_weights = {dimension: 1.0 for dimension in DIMENSION_NAMES}
    available_dimensions = [
        value for value in dimension_scores.values() if value["score"] is not None
    ]
    available_weight = sum(configured_weights[item["id"]] for item in available_dimensions)
    total_score = None
    if available_weight > 0:
        total_score = (
            sum(
                configured_weights[item["id"]] * float(item["score"])
                for item in available_dimensions
            )
            / available_weight
        )

    status_counts = Counter(result.status for result in result_list)
    finding_severity = Counter(
        finding.severity for result in result_list for finding in result.findings
    )
    evaluated_cases = sum(
        1
        for result in result_list
        if result.applicable and result.status in {"passed", "failed"}
    )
    applicable_cases = sum(1 for result in result_list if result.applicable)
    error_cases = sum(1 for result in result_list if result.status in ERROR_STATUSES)
    all_indicators_evaluated = all(
        item["score"] is not None for item in indicator_scores.values()
    )
    all_dimensions_evaluated = all(
        item["score"] is not None for item in dimension_scores.values()
    )
    coverage = coverage_summary(bundle)
    return {
        "schema_version": "1.0",
        "scoring": {
            "dimension_aggregation": "configured_weighted_macro_average",
            "indicator_aggregation": "verified_case_weighted_average",
            "missing_test_policy": "quality_null_not_zero_and_not_pass",
            "execution_error_policy": "excluded_from_quality_denominator",
            "security_hard_caps": SECURITY_CAPS,
        },
        "total_score": _round(total_score),
        "score_complete": bool(all_dimensions_evaluated and all_indicators_evaluated),
        "quality_status": "complete"
        if all_dimensions_evaluated and all_indicators_evaluated
        else "incomplete",
        "coverage": coverage,
        "execution": {
            "case_count": len(result_list),
            "applicable_case_count": applicable_cases,
            "evaluated_case_count": evaluated_cases,
            "execution_completeness": round(evaluated_cases / applicable_cases, 4)
            if applicable_cases
            else 0.0,
            "status_counts": dict(sorted(status_counts.items())),
            "error_case_count": error_cases,
            "findings_by_severity": dict(sorted(finding_severity.items())),
        },
        "dimensions": [dimension_scores[item] for item in DIMENSION_NAMES],
    }
