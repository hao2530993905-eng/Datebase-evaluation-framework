"""Evidence-first evaluation runner with isolated case databases."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
import traceback
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from . import __version__
from .adapters import create_adapter
from .collector import ResultCollector
from .config import ProjectConfig
from .errors import DBEvalError, EnvironmentError, GoldValidationError
from .gold import GoldBundle, load_gold
from .models import CaseResult, CaseSpec, CheckResult, Finding, Measurement
from .util import (
    canonical_json,
    expand_environment,
    json_safe,
    redact,
    sha256_file,
    utc_now,
)

PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z_]+)\}")


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class CaseSignal(Exception):
    def __init__(self, status: str, message: str, evidence: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.evidence = evidence or {}


@dataclass
class RunArtifacts:
    run_id: str
    run_dir: Path
    project: ProjectConfig
    gold: GoldBundle
    results: List[CaseResult]
    environment: Dict[str, Any]


def _safe_segment(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return normalized.strip("._") or "run"


def _new_run_directory(output_root: Path, project_id: str) -> Tuple[str, Path]:
    timestamp = utc_now().replace(":", "").replace("-", "")
    base = _safe_segment(project_id)
    candidate = f"{base}-{timestamp}"
    run_dir = output_root / "runs" / candidate
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = output_root / "runs" / f"{candidate}-{suffix}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir.name, run_dir


def _software_binding(project: ProjectConfig, gold: GoldBundle) -> None:
    project_software = project.raw["software"]
    gold_software = gold.raw["software"]
    if project_software["name"] != gold_software.get("name"):
        raise GoldValidationError(
            f"Gold 与项目软件不匹配: {gold_software.get('name')!r} != {project_software['name']!r}"
        )
    project_version = str(project_software.get("version", ""))
    gold_version = str(gold_software.get("version", ""))
    if project_version and gold_version and project_version != gold_version:
        raise GoldValidationError(
            f"Gold 与项目版本不匹配: {gold_version!r} != {project_version!r}"
        )
    expected_artifact_hash = gold_software.get("artifact_sha256")
    if expected_artifact_hash:
        artifact_value = project_software.get("artifact_path")
        if not artifact_value:
            raise GoldValidationError("Gold 绑定了 artifact_sha256，但项目未提供 software.artifact_path")
        artifact_path = (project.root / str(artifact_value)).resolve()
        if not artifact_path.is_file():
            raise GoldValidationError(f"被测软件制品不存在: {artifact_path}")
        actual_hash = sha256_file(artifact_path)
        if actual_hash != expected_artifact_hash:
            raise GoldValidationError(
                f"被测软件制品哈希不匹配: Gold={expected_artifact_hash}, actual={actual_hash}"
            )


def _resolve_applicability(
    spec: CaseSpec,
    project: ProjectConfig,
    capabilities: Mapping[str, bool],
) -> Tuple[bool, str]:
    raw = spec.raw
    for capability in raw.get("requires_capabilities", []):
        if not capabilities.get(str(capability), False):
            return False, f"执行器不支持能力 {capability}"
    for feature in raw.get("requires_features", []):
        if not project.features.get(str(feature), False):
            return False, f"项目声明不具备功能 {feature}"
    applies_if = raw.get("applies_if")
    if isinstance(applies_if, dict):
        feature = str(applies_if.get("feature", ""))
        expected = bool(applies_if.get("equals", True))
        if project.features.get(feature, False) != expected:
            return False, f"显式适用性规则不满足: feature {feature} != {expected}"
    if raw.get("skip_reason"):
        return False, str(raw["skip_reason"])
    return True, ""


def _normalized_rows(rows: Iterable[Iterable[Any]], unordered: bool = False) -> List[List[Any]]:
    values = [list(row) for row in rows]
    if unordered:
        values.sort(key=lambda row: canonical_json(json_safe(row)))
    return values


def _matches(actual: Any, expected: Any) -> bool:
    return canonical_json(json_safe(actual)) == canonical_json(json_safe(expected))


def _assertion(
    name: str,
    expectation: Mapping[str, Any],
    occurrence: Mapping[str, Any],
) -> CheckResult:
    kind = str(expectation.get("kind", ""))
    expected = expectation.get("value")
    rows = occurrence.get("rows", [])
    actual: Any
    passed = False
    if kind in {"success", "query_success"}:
        passed = bool(occurrence.get("success", True))
        actual = passed
    elif kind == "affected_rows":
        actual = occurrence.get("affected_rows")
        passed = _matches(actual, expected)
    elif kind == "row_count":
        actual = len(rows)
        passed = _matches(actual, expected)
    elif kind == "scalar":
        actual = rows[0][0] if rows and rows[0] else None
        passed = _matches(actual, expected)
    elif kind == "rows_equal":
        actual = _normalized_rows(rows)
        passed = _matches(actual, expected)
    elif kind == "rows_equal_unordered":
        actual = _normalized_rows(rows, unordered=True)
        passed = _matches(actual, _normalized_rows(expected or [], unordered=True))
    elif kind == "contains_rows":
        actual = _normalized_rows(rows, unordered=True)
        required = _normalized_rows(expected or [], unordered=True)
        passed = all(item in actual for item in required)
    elif kind == "json_path":
        data = occurrence.get("json")
        path = str(expectation.get("path", ""))
        actual = data
        try:
            for segment in [item for item in path.split(".") if item]:
                actual = actual[int(segment)] if isinstance(actual, list) else actual[segment]
            passed = _matches(actual, expected)
        except (KeyError, IndexError, TypeError, ValueError):
            actual = None
            passed = False
    elif kind == "not_success":
        passed = not bool(occurrence.get("success", False))
        actual = passed
    elif kind == "health_pass":
        actual = occurrence.get("passed")
        passed = bool(actual) == bool(expected)
    elif kind == "no_findings":
        minimum = str(expectation.get("min_severity", "low"))
        order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        threshold = order.get(minimum, 1)
        findings = occurrence.get("findings", [])
        actual = findings
        passed = all(order.get(str(item.get("severity")), 5) < threshold for item in findings)
        expected = {"max_severity": "below " + minimum}
    else:
        actual = occurrence.get("actual")
        passed = False
    return CheckResult(
        name=name,
        status="passed" if passed else "failed",
        expected=expected,
        actual=json_safe(actual),
        duration_ms=float(occurrence.get("duration_ms", 0.0)),
    )


def _error_assertion(
    name: str,
    expectation: Mapping[str, Any],
    error_text: str,
    duration_ms: float,
) -> CheckResult:
    kind = str(expectation.get("kind"))
    expected = expectation.get("value")
    if kind == "error_contains":
        passed = str(expected).lower() in error_text.lower()
    elif kind == "any_error":
        passed = True
        expected = "any database error"
    else:
        passed = False
    return CheckResult(
        name=name,
        status="passed" if passed else "failed",
        expected=expected,
        actual=error_text,
        duration_ms=duration_ms,
    )


def _status_from_checks(checks: List[CheckResult]) -> str:
    return "failed" if any(check.status == "failed" for check in checks) else "passed"


def _ramp_score(raw_scoring: Mapping[str, Any], measurements: List[Measurement]) -> Optional[float]:
    mode = raw_scoring.get("mode", "pass_fail")
    if mode != "ramp":
        return None
    metric = str(raw_scoring["metric"])
    values = {item.name: item.value for item in measurements}
    if metric not in values:
        return None
    value = float(values[metric])
    target = float(raw_scoring["target"])
    floor = float(raw_scoring["floor"])
    direction = str(raw_scoring.get("direction", "lower_is_better"))
    if target == floor:
        return 1.0 if value == target else 0.0
    if direction == "lower_is_better":
        score = (floor - value) / (floor - target)
    elif direction == "higher_is_better":
        score = (value - floor) / (target - floor)
    else:
        raise ValueError(f"未知 scoring.direction: {direction}")
    return round(max(0.0, min(1.0, score)), 6)


def _parse_findings(data: Any) -> List[Finding]:
    if not isinstance(data, dict):
        return []
    source = data.get("findings", [])
    if not isinstance(source, list):
        return []
    findings: List[Finding] = []
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "info")).lower()
        if severity not in {"info", "low", "medium", "high", "critical"}:
            severity = "info"
        findings.append(
            Finding(
                id=str(item.get("id", f"finding-{index + 1}")),
                severity=severity,
                title=str(item.get("title", "Unnamed finding")),
                evidence=str(item.get("evidence", "")),
                location=str(item.get("location", "")),
            )
        )
    return findings


def _parse_measurements(data: Any) -> List[Measurement]:
    if not isinstance(data, dict) or not isinstance(data.get("measurements"), list):
        return []
    result: List[Measurement] = []
    for item in data["measurements"]:
        if not isinstance(item, dict):
            continue
        try:
            result.append(
                Measurement(
                    name=str(item["name"]),
                    value=float(item["value"]),
                    unit=str(item.get("unit", "")),
                    sample_count=int(item.get("sample_count", 1)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


class EvaluationRunner:
    def __init__(self, project: ProjectConfig, output_root: Path):
        self.project = project
        self.output_root = output_root.resolve()
        self.adapter = create_adapter(project)

    def _placeholder_values(self, session: Any, case_dir: Path) -> Dict[str, str]:
        values = {
            "DB_PATH": str(session.path) if hasattr(session, "path") else "",
            "WORK_DIR": str(case_dir),
            "CASE_DIR": str(case_dir),
            "PROJECT_ROOT": str(self.project.root),
        }
        if hasattr(session, "export_environment"):
            values.update(session.export_environment())
        return values

    def _expand_placeholders(self, value: str, variables: Mapping[str, str]) -> str:
        expanded = expand_environment(value)

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in variables:
                raise EnvironmentError(f"命令占位符未定义: ${{{key}}}")
            return variables[key]

        return PLACEHOLDER_PATTERN.sub(replace, expanded)

    def _command_case(
        self,
        spec: CaseSpec,
        result: CaseResult,
        session: Any,
        case_dir: Path,
    ) -> None:
        raw = spec.raw
        variables = self._placeholder_values(session, case_dir)
        try:
            argv = [self._expand_placeholders(str(item), variables) for item in raw["command"]]
        except DBEvalError as exc:
            raise CaseSignal("environment_error", str(exc)) from exc
        cwd_mode = raw.get("cwd", "case_dir")
        cwd = self.project.root if cwd_mode == "project_root" else case_dir
        environment = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
            if key in os.environ
        }
        environment.update(variables)
        environment.update({str(k): str(v) for k, v in raw.get("environment", {}).items()})
        timeout = float(raw.get("timeout_seconds", self.project.execution.get("case_timeout_seconds", 30)))
        stdout_path = case_dir / "command.stdout"
        stderr_path = case_dir / "command.stderr"
        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd),
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CaseSignal("tool_error", f"工具不存在: {argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(redact(_as_text(exc.stdout), "stdout"), encoding="utf-8")
            stderr_path.write_text(redact(_as_text(exc.stderr), "stderr"), encoding="utf-8")
            raise CaseSignal(
                "tool_error",
                f"外部工具超时（{timeout}s）",
                {"stdout_path": stdout_path.name, "stderr_path": stderr_path.name},
            ) from exc
        except OSError as exc:
            raise CaseSignal("tool_error", f"外部工具启动失败: {exc}") from exc

        safe_stdout = redact(_as_text(completed.stdout), "stdout")
        safe_stderr = redact(_as_text(completed.stderr), "stderr")
        stdout_path.write_text(safe_stdout, encoding="utf-8")
        stderr_path.write_text(safe_stderr, encoding="utf-8")
        result.evidence.update(
            {
                "command_template": redact(raw["command"]),
                "exit_code": completed.returncode,
                "stdout_path": stdout_path.name,
                "stderr_path": stderr_path.name,
                "stdout": safe_stdout[-8000:],
                "stderr": safe_stderr[-8000:],
            }
        )

        expect = raw["expect"]
        kind = str(expect.get("kind"))
        occurrence: Dict[str, Any] = {
            "success": completed.returncode in raw.get("success_exit_codes", [0]),
        }
        parsed_json: Any = None
        if kind in {"json_path", "no_findings"} or raw.get("parse_json"):
            try:
                parsed_json = json.loads(_as_text(completed.stdout))
            except json.JSONDecodeError as exc:
                raise CaseSignal("tool_error", f"外部工具 JSON 输出无效: {exc}") from exc
            occurrence["json"] = parsed_json
            result.findings = _parse_findings(parsed_json)
            result.measurements.extend(_parse_measurements(parsed_json))

        if kind == "exit_code":
            check = _assertion("command.exit_code", expect, {"actual": completed.returncode})
            # _assertion's generic branch has no matching kind for exit_code.
            check.status = "passed" if _matches(completed.returncode, expect.get("value")) else "failed"
            check.expected = expect.get("value")
            check.actual = completed.returncode
        elif kind == "junit":
            try:
                root = ET.fromstring(_as_text(completed.stdout))
            except ET.ParseError as exc:
                raise CaseSignal("tool_error", f"JUnit 输出无效: {exc}") from exc
            suites = [root] if root.tag.endswith("testsuite") else list(root)
            failures = 0
            errors = 0
            tests = 0
            for suite in suites:
                tests += int(suite.attrib.get("tests", 0))
                failures += int(suite.attrib.get("failures", 0))
                errors += int(suite.attrib.get("errors", 0))
            passed = failures == 0 and errors == 0
            check = CheckResult(
                "command.junit",
                "passed" if passed else "failed",
                {"failures": 0, "errors": 0},
                {"tests": tests, "failures": failures, "errors": errors},
            )
            result.measurements.append(Measurement("junit_tests", tests, "tests"))
        elif kind in {"json_path", "no_findings"}:
            check = _assertion("command.expectation", expect, occurrence)
        else:
            raise CaseSignal("framework_error", f"不支持的命令期望类型: {kind}")
        result.checks.append(check)

    def _sql_case(self, spec: CaseSpec, result: CaseResult, session: Any) -> None:
        for index, step in enumerate(spec.raw.get("steps", [])):
            sql = str(step["sql"])
            params = step.get("params", {})
            expect = step.get("expect")
            started = time.perf_counter()
            try:
                occurrence = session.execute(sql, params)
                duration = (time.perf_counter() - started) * 1000.0
                occurrence["duration_ms"] = duration
                occurrence["success"] = True
                if isinstance(expect, dict) and expect.get("kind") in {
                    "error_contains",
                    "any_error",
                }:
                    check = CheckResult(
                        name=f"step.{index + 1}",
                        status="failed",
                        expected=expect,
                        actual="语句成功执行，但 Gold 预期失败",
                        duration_ms=duration,
                    )
                elif isinstance(expect, dict):
                    check = _assertion(f"step.{index + 1}", expect, occurrence)
                else:
                    check = CheckResult(
                        name=f"step.{index + 1}",
                        status="passed",
                        expected="SQL success",
                        actual="SQL success",
                        duration_ms=duration,
                    )
                result.evidence.setdefault("steps", []).append(
                    {"index": index + 1, "sql": sql, "result": json_safe(occurrence)}
                )
            except Exception as exc:  # Database driver errors are product observations.
                duration = (time.perf_counter() - started) * 1000.0
                error_text = f"{type(exc).__name__}: {exc}"
                result.evidence.setdefault("steps", []).append(
                    {"index": index + 1, "sql": sql, "error": error_text}
                )
                if isinstance(expect, dict) and expect.get("kind") in {
                    "error_contains",
                    "any_error",
                }:
                    check = _error_assertion(f"step.{index + 1}", expect, error_text, duration)
                else:
                    check = CheckResult(
                        name=f"step.{index + 1}",
                        status="failed",
                        expected=expect or "SQL success",
                        actual=error_text,
                        duration_ms=duration,
                    )
            result.checks.append(check)
            if check.status == "failed":
                break

    def _benchmark_case(self, spec: CaseSpec, result: CaseResult, session: Any) -> None:
        raw = spec.raw
        metrics = session.benchmark(
            str(raw["sql"]),
            warmup=int(raw.get("warmup", 0)),
            repeat=int(raw.get("repeat", 10)),
            params=raw.get("params", {}),
        )
        result.evidence["benchmark"] = json_safe(metrics)
        units = {
            "mean_latency_ms": "ms",
            "p50_latency_ms": "ms",
            "p95_latency_ms": "ms",
            "p99_latency_ms": "ms",
            "max_latency_ms": "ms",
            "throughput_ops_s": "operations/s",
            "elapsed_seconds": "s",
            "error_count": "errors",
        }
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and key in units:
                result.measurements.append(
                    Measurement(key, float(value), units[key], int(metrics.get("repeat", 1)))
                )
        if int(metrics.get("error_count", 0)) > 0:
            result.checks.append(
                CheckResult(
                    "benchmark.errors",
                    "failed",
                    expected=0,
                    actual=metrics.get("error_count"),
                )
            )
        else:
            result.checks.append(
                CheckResult("benchmark.errors", "passed", expected=0, actual=0)
            )
        for metric_name, threshold in raw.get("thresholds", {}).items():
            actual = float(metrics.get(metric_name))
            passed = True
            if "max" in threshold:
                passed = passed and actual <= float(threshold["max"])
            if "min" in threshold:
                passed = passed and actual >= float(threshold["min"])
            result.checks.append(
                CheckResult(
                    f"threshold.{metric_name}",
                    "passed" if passed else "failed",
                    expected=threshold,
                    actual=actual,
                )
            )

    def _health_case(self, spec: CaseSpec, result: CaseResult, session: Any) -> None:
        occurrence = session.health_probe(str(spec.raw["check"]))
        result.evidence["health_probe"] = json_safe(occurrence)
        result.measurements.append(
            Measurement("health_duration_ms", float(occurrence.get("duration_ms", 0)), "ms")
        )
        result.checks.append(
            _assertion(
                "health.expectation",
                spec.raw["expect"],
                occurrence,
            )
        )

    def _backup_restore_case(self, spec: CaseSpec, result: CaseResult, session: Any, case_dir: Path) -> None:
        raw = spec.raw
        destination = case_dir / "backup.sqlite3"
        started = time.perf_counter()
        try:
            backup = session.backup_restore(destination)
        except Exception as exc:
            raise CaseSignal("framework_error", f"备份/恢复适配器失败: {exc}") from exc
        restore_seconds = time.perf_counter() - started
        result.evidence["backup"] = json_safe(backup)
        result.measurements.append(Measurement("backup_restore_seconds", restore_seconds, "s"))
        result.measurements.append(Measurement("backup_restore_ms", restore_seconds * 1000.0, "ms"))
        result.checks.append(
            CheckResult(
                "backup.integrity",
                "passed" if backup.get("status") == "ok" else "failed",
                expected="ok",
                actual=backup.get("status"),
            )
        )
        try:
            rows = session.query_backup(
                destination,
                str(raw["verification_sql"]),
                raw.get("verification_params", {}),
            )
        except Exception as exc:
            result.checks.append(
                CheckResult(
                    "backup.verification_query",
                    "failed",
                    expected="query success",
                    actual=f"{type(exc).__name__}: {exc}",
                )
            )
            return
        result.checks.append(
            _assertion(
                "backup.data",
                raw["expect"],
                {"rows": rows, "success": True},
            )
        )
        rpo_sql = raw.get("rpo_check_sql")
        if rpo_sql:
            live_rows = session.query(str(rpo_sql), raw.get("rpo_params", {}))
            result.measurements.append(
                Measurement("rpo_row_delta", abs(len(live_rows) - len(rows)), "rows")
            )
            result.checks.append(
                CheckResult(
                    "recovery.rpo_data_consistency",
                    "passed" if _matches(live_rows, rows) else "failed",
                    expected=json_safe(live_rows),
                    actual=json_safe(rows),
                )
            )
        if "rto_max_seconds" in raw:
            maximum = float(raw["rto_max_seconds"])
            result.checks.append(
                CheckResult(
                    "recovery.rto",
                    "passed" if restore_seconds <= maximum else "failed",
                    expected={"max_seconds": maximum},
                    actual=restore_seconds,
                )
            )

    def _concurrency_case(self, spec: CaseSpec, result: CaseResult, session: Any) -> None:
        raw = spec.raw
        occurrence = session.concurrent_workers(
            str(raw["worker_sql"]),
            workers=int(raw["workers"]),
            iterations=int(raw["iterations"]),
            params=raw.get("worker_params", {}),
        )
        result.evidence["concurrency"] = json_safe(occurrence)
        result.measurements.extend(
            [
                Measurement("concurrency_throughput_ops_s", float(occurrence["throughput_ops_s"]), "operations/s"),
                Measurement("concurrency_elapsed_seconds", float(occurrence["elapsed_seconds"]), "s"),
            ]
        )
        tolerated = int(raw.get("tolerated_errors", 0))
        result.checks.append(
            CheckResult(
                "concurrency.errors",
                "passed" if int(occurrence["failure_count"]) <= tolerated else "failed",
                expected={"max_errors": tolerated},
                actual=occurrence["failure_count"],
            )
        )
        rows = session.query(str(raw["verification_sql"]), raw.get("verification_params", {}))
        result.checks.append(
            _assertion(
                "concurrency.verification",
                raw["expect"],
                {"rows": rows, "success": True},
            )
        )

    def _execute_case(self, spec: CaseSpec, run_dir: Path) -> CaseResult:
        case_dir = run_dir / "cases" / _safe_segment(spec.id)
        case_dir.mkdir(parents=True, exist_ok=False)
        result = CaseResult(
            case_id=spec.id,
            dimension=spec.dimension,
            indicator=spec.indicator,
            title=spec.title,
            status="passed",
            weight=spec.weight,
        )
        applicable, reason = _resolve_applicability(
            spec, self.project, self.adapter.capabilities.as_dict()
        )
        if not applicable:
            result.status = "not_applicable"
            result.applicable = False
            result.applicability_reason = reason
            result.point_score = None
            return result

        session = None
        started = time.perf_counter()
        try:
            try:
                session = self.adapter.open_session(case_dir)
            except EnvironmentError:
                raise
            except Exception as exc:
                raise CaseSignal("environment_error", f"无法创建隔离数据库: {exc}") from exc

            setup = spec.raw.get("setup", [])
            for index, step in enumerate(setup):
                try:
                    session.execute(str(step["sql"]), step.get("params", {}))
                except Exception as exc:
                    raise CaseSignal(
                        "environment_error",
                        f"Gold setup step {index + 1} 失败，无法建立测试初始状态: {exc}",
                    ) from exc

            if spec.case_type == "sql":
                self._sql_case(spec, result, session)
            elif spec.case_type == "benchmark":
                self._benchmark_case(spec, result, session)
            elif spec.case_type == "health":
                self._health_case(spec, result, session)
            elif spec.case_type == "backup_restore":
                self._backup_restore_case(spec, result, session, case_dir)
            elif spec.case_type == "concurrency":
                self._concurrency_case(spec, result, session)
            elif spec.case_type == "command":
                self._command_case(spec, result, session, case_dir)
            else:
                raise CaseSignal("framework_error", f"不支持的 case 类型: {spec.case_type}")

            result.status = _status_from_checks(result.checks)
            if result.status == "passed":
                result.point_score = 1.0
            else:
                result.point_score = 0.0
            ramp = _ramp_score(spec.raw.get("scoring", {}), result.measurements)
            if ramp is not None:
                result.point_score = ramp
        except CaseSignal as signal:
            result.status = signal.status
            result.error_kind = signal.status
            result.error_message = str(signal)
            result.evidence.update(signal.evidence)
            result.point_score = None
        except EnvironmentError as exc:
            result.status = "environment_error"
            result.error_kind = "environment"
            result.error_message = str(exc)
            result.point_score = None
        except Exception as exc:
            result.status = "framework_error"
            result.error_kind = "framework"
            result.error_message = f"{type(exc).__name__}: {exc}"
            result.evidence["traceback"] = traceback.format_exc()[-12000:]
            result.point_score = None
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            result.duration_ms = (time.perf_counter() - started) * 1000.0
        return result

    def run(self, gold_path: Path) -> RunArtifacts:
        require_frozen = bool(self.project.execution.get("require_frozen_gold", True))
        signing_key_env = str(self.project.execution.get("signing_key_env", "DBEVAL_GOLD_KEY"))
        gold = load_gold(
            gold_path,
            require_frozen=require_frozen,
            signing_key_env=signing_key_env,
        )
        _software_binding(self.project, gold)
        run_id, run_dir = _new_run_directory(self.output_root, self.project.project_id)
        collector = ResultCollector(run_dir, self.project.project_id, gold)
        environment = {
            "framework_version": __version__,
            "started_at": utc_now(),
            "finished_at": None,
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "project": self.project.public_snapshot(),
            "gold_bundle_id": gold.bundle_id,
            "gold_content_hash": gold.content_hash(),
            "gold_evidence_manifest": gold.raw.get("evidence_manifest"),
            "capabilities": self.adapter.capabilities.as_dict(),
            "database_identity": self.adapter.detect().as_dict(),
            "tool_versions": self.adapter.tool_versions(),
            "input_hashes": {
                "project_config": sha256_file(self.project.path),
                "gold": sha256_file(gold.path),
            },
        }
        try:
            environment["prepared_database"] = self.adapter.prepare(run_dir / "environment")
            if self.adapter.capabilities.schema_introspection:
                environment["schema"] = self.adapter.inspect_schema()
        except Exception:
            environment["finished_at"] = utc_now()
            environment["fatal_error"] = traceback.format_exc()[-12000:]
            collector.set_environment(environment)
            raise

        collector.set_environment(environment)
        for spec in gold.cases:
            result = self._execute_case(spec, run_dir)
            collector.record(result)
        environment["finished_at"] = utc_now()
        environment["case_count"] = len(collector.results)
        collector.set_environment(environment)
        collector.finalize()
        return RunArtifacts(
            run_id=run_id,
            run_dir=run_dir,
            project=self.project,
            gold=gold,
            results=collector.results,
            environment=environment,
        )
