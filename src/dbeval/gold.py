"""Gold bundle validation, coverage analysis, and cryptographic freezing."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .errors import GoldValidationError
from .metrics import DIMENSION_NAMES, INDICATOR_MAP
from .models import CaseSpec
from .util import canonical_hash, canonical_json, read_json, utc_now, write_json
from .util import ensure_within, sha256_file


SUPPORTED_CASE_TYPES = {
    "sql",
    "benchmark",
    "health",
    "backup_restore",
    "concurrency",
    "command",
}
SEVERITIES = {"info", "low", "medium", "high", "critical"}


@dataclass
class GoldBundle:
    path: Path
    raw: Dict[str, Any]

    @property
    def bundle_id(self) -> str:
        return str(self.raw["bundle_id"])

    @property
    def cases(self) -> List[CaseSpec]:
        result: List[CaseSpec] = []
        for raw_case in self.raw["cases"]:
            result.append(
                CaseSpec(
                    id=str(raw_case["id"]),
                    dimension=str(raw_case["dimension"]),
                    indicator=str(raw_case["indicator"]),
                    title=str(raw_case["title"]),
                    case_type=str(raw_case["type"]),
                    raw=raw_case,
                    weight=float(raw_case.get("weight", 1.0)),
                    required=bool(raw_case.get("required", True)),
                )
            )
        return result

    @property
    def dimension_weights(self) -> Dict[str, float]:
        return {str(key): float(value) for key, value in self.raw["dimension_weights"].items()}

    def content_hash(self) -> str:
        return canonical_hash(self.raw, excluded_keys={"freeze"})

    def public_snapshot(self) -> Dict[str, Any]:
        return self.raw


def _expectation_is_explicit(expect: Any) -> bool:
    if not isinstance(expect, dict):
        return False
    return "kind" in expect and "value" in expect


def _freeze_approval_payload(freeze: Dict[str, Any]) -> bytes:
    return canonical_json(
        {
            "content_hash": freeze["content_hash"],
            "approved_by": freeze["approved_by"],
            "approved_at": freeze["approved_at"],
        }
    ).encode("utf-8")


def build_evidence_manifest(raw: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    source_ids = sorted(
        {
            str(source_id)
            for case in raw.get("cases", [])
            if isinstance(case, dict)
            for source_id in (
                case.get("provenance", {}).get("source_ids", [])
                if isinstance(case.get("provenance"), dict)
                else []
            )
            if str(source_id).startswith("file:")
        }
    )
    files: Dict[str, str] = {}
    for source_id in source_ids:
        relative = source_id.removeprefix("file:").split("#", 1)[0]
        path = ensure_within(base_dir / relative, [base_dir], f"Gold 证据 {source_id}")
        if not path.is_file():
            raise GoldValidationError(f"Gold 证据文件不存在: {source_id}")
        files[source_id] = sha256_file(path)
    return {"algorithm": "sha256", "files": files}


def validate_gold_dict(
    raw: Dict[str, Any],
    *,
    require_frozen: bool = False,
    signing_key: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> List[str]:
    errors: List[str] = []
    warnings: List[str] = []
    if raw.get("schema_version") != "1.0":
        errors.append("schema_version 必须为 '1.0'")
    for key in ("bundle_id", "software", "created_at", "created_by"):
        if not raw.get(key):
            errors.append(f"缺少 {key}")

    software = raw.get("software")
    if not isinstance(software, dict) or not software.get("name"):
        errors.append("software.name 缺失")

    weights = raw.get("dimension_weights")
    if not isinstance(weights, dict) or set(weights) != set(DIMENSION_NAMES):
        errors.append("dimension_weights 必须恰好包含 DB1-DB8")
    elif any(not isinstance(value, (int, float)) or value < 0 for value in weights.values()):
        errors.append("dimension_weights 必须是非负数")
    elif sum(weights.values()) <= 0:
        errors.append("dimension_weights 权重之和必须大于 0")

    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases 必须是非空数组")
        cases = []

    seen_ids: Set[str] = set()
    indicator_totals: Dict[str, float] = {}
    for index, case in enumerate(cases):
        location = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{location} 必须是对象")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{location}.id 缺失")
        elif case_id in seen_ids:
            errors.append(f"case id 重复: {case_id}")
        else:
            seen_ids.add(case_id)

        dimension = case.get("dimension")
        indicator = case.get("indicator")
        if dimension not in DIMENSION_NAMES:
            errors.append(f"{location}.dimension 无效: {dimension!r}")
        if indicator not in INDICATOR_MAP:
            errors.append(f"{location}.indicator 无效: {indicator!r}")
        elif dimension != INDICATOR_MAP[indicator].dimension:
            errors.append(f"{case_id} 的 indicator 不属于 {dimension}")

        if not case.get("title"):
            errors.append(f"{location}.title 缺失")
        case_type = case.get("type")
        if case_type not in SUPPORTED_CASE_TYPES:
            errors.append(f"{location}.type 不支持: {case_type!r}")
        weight = case.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or weight <= 0:
            errors.append(f"{location}.weight 必须大于 0")
        elif indicator in INDICATOR_MAP:
            indicator_totals[str(indicator)] = indicator_totals.get(str(indicator), 0.0) + float(weight)

        provenance = case.get("provenance")
        if not isinstance(provenance, dict):
            errors.append(f"{location}.provenance 必须是对象")
        else:
            source_ids = provenance.get("source_ids")
            if not isinstance(source_ids, list) or not source_ids:
                errors.append(f"{case_id} 缺少 provenance.source_ids")
            if not provenance.get("rationale"):
                errors.append(f"{case_id} 缺少 provenance.rationale")
            if provenance.get("validation_status") not in {"verified", "frozen"}:
                errors.append(f"{case_id} provenance.validation_status 必须为 verified 或 frozen")

        if case_type == "sql":
            steps = case.get("steps")
            if not isinstance(steps, list) or not steps:
                errors.append(f"{case_id} 的 SQL case 需要非空 steps")
            elif any(not isinstance(step, dict) or not step.get("sql") for step in steps):
                errors.append(f"{case_id} 的每个 SQL step 都需要 sql")
        elif case_type == "benchmark":
            if not case.get("sql") or not case.get("scoring"):
                errors.append(f"{case_id} benchmark 需要 sql 和 scoring")
        elif case_type == "health":
            if case.get("check") not in {"integrity", "foreign_keys", "read_write"}:
                errors.append(f"{case_id} health.check 无效")
        elif case_type == "backup_restore":
            if not case.get("verification_sql"):
                errors.append(f"{case_id} backup_restore 需要 verification_sql")
        elif case_type == "concurrency":
            for key in ("worker_sql", "verification_sql", "workers", "iterations"):
                if key not in case:
                    errors.append(f"{case_id} concurrency 缺少 {key}")
        elif case_type == "command":
            command = case.get("command")
            if not isinstance(command, list) or not command:
                errors.append(f"{case_id} command 必须是非空数组")
            if not case.get("expect"):
                errors.append(f"{case_id} command 需要显式 expect")

        if case_type in {"sql", "health", "backup_restore", "concurrency"}:
            # SQL cases may use expect_error; all others need a concrete oracle.
            if case_type == "sql" and case.get("expect_error"):
                pass
            elif case_type == "sql" and not case.get("oracle"):
                step_expectations = [
                    step.get("expect") for step in case.get("steps", []) if isinstance(step, dict)
                ]
                if not any(_expectation_is_explicit(item) for item in step_expectations):
                    errors.append(f"{case_id} 没有程序可验证的 Gold 断言")
            elif case_type in {"health", "backup_restore", "concurrency"} and not case.get("expect"):
                errors.append(f"{case_id} 缺少显式 expect")

        findings = case.get("findings", [])
        if findings and not isinstance(findings, list):
            errors.append(f"{case_id}.findings 必须是数组")
        else:
            for finding in findings:
                if not isinstance(finding, dict) or finding.get("severity") not in SEVERITIES:
                    errors.append(f"{case_id} finding severity 无效")

    if not errors and not indicator_totals:
        errors.append("Gold 没有任何指标")

    freeze = raw.get("freeze")
    evidence_manifest = raw.get("evidence_manifest")
    if base_dir is not None:
        try:
            actual_manifest = build_evidence_manifest(raw, base_dir.resolve())
        except GoldValidationError as exc:
            errors.append(str(exc))
            actual_manifest = None
        if actual_manifest is not None:
            recorded = evidence_manifest.get("files") if isinstance(evidence_manifest, dict) else None
            if evidence_manifest is not None and recorded != actual_manifest["files"]:
                errors.append("Gold 证据文件哈希不匹配或清单缺失")
            elif evidence_manifest is None and require_frozen:
                errors.append("冻结 Gold 缺少 evidence_manifest")
    if require_frozen:
        if not isinstance(freeze, dict) or freeze.get("status") != "frozen":
            errors.append("Gold 未冻结，不能用于正式评测")
        elif freeze.get("content_hash") != canonical_hash(raw, excluded_keys={"freeze"}):
            errors.append("Gold 内容哈希不匹配，冻结后被修改")
        elif not freeze.get("approved_by"):
            errors.append("冻结 Gold 缺少 approved_by")
        elif not isinstance(evidence_manifest, dict):
            errors.append("冻结 Gold 缺少 evidence_manifest")
        elif signing_key and not freeze.get("signature"):
            errors.append("配置了 Gold 签名密钥，但冻结记录缺少 signature")
        elif signing_key:
            expected = hmac.new(
                signing_key.encode("utf-8"),
                _freeze_approval_payload(raw["freeze"]),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected, str(raw["freeze"].get("signature", ""))):
                errors.append("Gold HMAC 签名无效")
    elif freeze and isinstance(freeze, dict) and freeze.get("content_hash"):
        if freeze.get("content_hash") != canonical_hash(raw, excluded_keys={"freeze"}):
            errors.append("Gold 冻结哈希与当前内容不一致")

    # Coverage is deliberately not part of a quality score. This warning only
    # exposes indicator gaps to the evaluator.
    present_indicators = {item.get("indicator") for item in cases if isinstance(item, dict)}
    missing = sorted(set(INDICATOR_MAP) - present_indicators)
    if missing:
        warnings.append(f"Gold 未覆盖指标: {', '.join(missing)}")

    return errors + [f"WARNING: {item}" for item in warnings]


def load_gold(
    path: Path,
    *,
    require_frozen: bool = False,
    signing_key_env: str = "DBEVAL_GOLD_KEY",
) -> GoldBundle:
    raw = read_json(path.resolve())
    signing_key = os.environ.get(signing_key_env) or None
    messages = validate_gold_dict(
        raw,
        require_frozen=require_frozen,
        signing_key=signing_key,
        base_dir=path.resolve().parent,
    )
    errors = [item for item in messages if not item.startswith("WARNING:")]
    if errors:
        raise GoldValidationError("; ".join(errors))
    return GoldBundle(path=path.resolve(), raw=raw)


def freeze_gold(
    input_path: Path,
    output_path: Path,
    *,
    approved_by: str,
    signing_key: Optional[str] = None,
) -> Dict[str, Any]:
    if not approved_by.strip():
        raise GoldValidationError("approved_by 不能为空")
    raw = read_json(input_path.resolve())
    raw.pop("freeze", None)
    raw.pop("evidence_manifest", None)
    errors = [
        item
        for item in validate_gold_dict(raw, base_dir=input_path.resolve().parent)
        if not item.startswith("WARNING:")
    ]
    if errors:
        raise GoldValidationError("; ".join(errors))
    raw["evidence_manifest"] = build_evidence_manifest(raw, input_path.resolve().parent)
    post_errors = [
        item
        for item in validate_gold_dict(raw, base_dir=input_path.resolve().parent)
        if not item.startswith("WARNING:")
    ]
    if post_errors:
        raise GoldValidationError("; ".join(post_errors))
    content_hash = canonical_hash(raw, excluded_keys={"freeze"})
    signature = None
    freeze_record = {
        "status": "frozen",
        "content_hash": content_hash,
        "approved_by": approved_by,
        "approved_at": utc_now(),
        "signature_algorithm": "none",
        "signature": None,
    }
    if signing_key:
        signature = hmac.new(
            signing_key.encode("utf-8"),
            _freeze_approval_payload(freeze_record),
            hashlib.sha256,
        ).hexdigest()
        freeze_record["signature_algorithm"] = "hmac-sha256"
        freeze_record["signature"] = signature
    raw["freeze"] = freeze_record
    write_json(output_path.resolve(), raw)
    return raw


def coverage_summary(bundle: GoldBundle) -> Dict[str, Any]:
    covered = {case.indicator for case in bundle.cases}
    dimensions: Dict[str, Any] = {}
    for dimension in DIMENSION_NAMES:
        expected = {
            indicator.id for indicator in INDICATOR_MAP.values() if indicator.dimension == dimension
        }
        actual = covered & expected
        dimensions[dimension] = {
            "covered": len(actual),
            "expected": len(expected),
            "ratio": round(len(actual) / len(expected), 4),
            "missing_indicators": sorted(expected - actual),
        }
    return {
        "indicator_coverage": round(len(covered) / len(INDICATOR_MAP), 4),
        "covered_indicators": len(covered),
        "expected_indicators": len(INDICATOR_MAP),
        "case_count": len(bundle.cases),
        "dimensions": dimensions,
    }
