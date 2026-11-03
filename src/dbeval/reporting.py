"""JSON, Markdown, and JUnit report generation."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import json
from pathlib import Path
from typing import Any, Dict, List

from .gold import GoldBundle, coverage_summary
from .runner import RunArtifacts
from .scoring import score_results
from .util import sha256_file, write_json


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _check_detail(check) -> str:
    expected = json.dumps(check.expected, ensure_ascii=False, default=str)
    actual = json.dumps(check.actual, ensure_ascii=False, default=str)
    return f"`{check.name}` 预期 {expected}，实际 {actual}"


def write_markdown_report(path: Path, artifacts: RunArtifacts, scored: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append("# 数据库评测报告")
    lines.append("")
    lines.append(f"- 运行 ID：`{artifacts.run_id}`")
    lines.append(f"- 项目：`{artifacts.project.project_id}`")
    lines.append(f"- Gold：`{artifacts.gold.bundle_id}`")
    lines.append(f"- Gold 内容哈希：`{artifacts.gold.content_hash()}`")
    lines.append(f"- 数据库：`{artifacts.environment['database_identity']['backend']}`")
    lines.append(f"- 开始时间：{artifacts.environment['started_at']}")
    lines.append("")
    lines.append("## 结论摘要")
    lines.append("")
    lines.append(f"- 数据库质量总分：**{_fmt(scored['total_score'])} / 100**")
    lines.append(f"- 评分完整性：`{scored['quality_status']}`")
    lines.append(
        f"- 用例执行完整度：`{scored['execution']['evaluated_case_count']} / "
        f"{scored['execution']['applicable_case_count']}`"
    )
    lines.append(
        f"- Gold 指标覆盖率：`{scored['coverage']['covered_indicators']} / "
        f"{scored['coverage']['expected_indicators']}` "
        f"({scored['coverage']['indicator_coverage'] * 100:.1f}%)"
    )
    lines.append("")
    lines.append("> 覆盖率与执行质量分开报告。缺失测试不会被计为通过，也不会被伪装成软件失败。")
    lines.append("")
    lines.append("## 一级维度")
    lines.append("")
    lines.append("| 维度 | 名称 | 得分 | 状态 | 指标覆盖 |")
    lines.append("| --- | --- | ---: | --- | ---: |")
    for dimension in scored["dimensions"]:
        lines.append(
            f"| {dimension['id']} | {dimension['name']} | {_fmt(dimension['score'])} | "
            f"{dimension['status']} | {dimension['indicator_coverage'] * 100:.0f}% |"
        )
    lines.append("")
    lines.append("## 指标结果")
    lines.append("")
    lines.append("| 指标 | 名称 | 得分 | 用例 | 状态 |")
    lines.append("| --- | --- | ---: | ---: | --- |")
    for dimension in scored["dimensions"]:
        for indicator in dimension["indicators"]:
            lines.append(
                f"| {indicator['id']} | {indicator['name']} | {_fmt(indicator['score'])} | "
                f"{indicator['case_count']} | {indicator['status']} |"
            )
    lines.append("")
    lines.append("## 安全门控")
    lines.append("")
    capped = [item for item in scored["dimensions"] if item["score_cap"] is not None]
    if capped:
        for item in capped:
            lines.append(
                f"- `{item['id']}` 原始分 {_fmt(item['raw_score_before_hard_gate'])}，"
                f"因存在最高严重级别漏洞，应用上限 {_fmt(item['score_cap'])}。"
            )
    else:
        lines.append("本次没有安全结果触发分数上限。")
    lines.append("")
    lines.append("## 执行分类")
    lines.append("")
    for status, count in scored["execution"]["status_counts"].items():
        lines.append(f"- `{status}`：{count}")
    lines.append("")
    lines.append("## 用例明细")
    lines.append("")
    lines.append("| 用例 | 维度 | 状态 | 分值 | 耗时 ms |")
    lines.append("| --- | --- | --- | ---: | ---: |")
    for result in artifacts.results:
        lines.append(
            f"| `{result.case_id}` | {result.dimension}/{result.indicator} | {result.status} | "
            f"{_fmt(result.point_score)} | {result.duration_ms:.1f} |"
        )
        if result.error_message:
            lines.append(f"  - 错误：{result.error_message}")
        for check in result.checks:
            if check.status == "failed":
                lines.append(f"  - 断言失败：{_check_detail(check)}")
        for finding in result.findings:
            lines.append(
                f"  - 漏洞 `{finding.id}` [{finding.severity}] {finding.title}"
            )
    lines.append("")
    lines.append("## 可追溯信息")
    lines.append("")
    lines.append("```json")
    lines.append(
        __import__("json").dumps(
            {
                "input_hashes": artifacts.environment.get("input_hashes", {}),
                "prepared_database": artifacts.environment.get("prepared_database", {}),
                "tool_versions": artifacts.environment.get("tool_versions", {}),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    lines.append("```")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_junit_report(path: Path, artifacts: RunArtifacts) -> None:
    failures = sum(1 for item in artifacts.results if item.status == "failed")
    errors = sum(
        1
        for item in artifacts.results
        if item.status in {"environment_error", "tool_error", "framework_error"}
    )
    skipped = sum(
        1 for item in artifacts.results if item.status in {"skipped", "not_applicable"}
    )
    root = ET.Element(
        "testsuites",
        {
            "name": f"dbeval:{artifacts.project.project_id}",
            "tests": str(len(artifacts.results)),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": str(skipped),
        },
    )
    suite = ET.SubElement(
        root,
        "testsuite",
        {
            "name": artifacts.gold.bundle_id,
            "tests": str(len(artifacts.results)),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": str(skipped),
        },
    )
    for result in artifacts.results:
        testcase = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"{result.dimension}.{result.indicator}",
                "name": f"{result.case_id} {result.title}",
                "time": f"{result.duration_ms / 1000.0:.6f}",
            },
        )
        details = result.error_message or " | ".join(
            _check_detail(item) for item in result.checks if item.status == "failed"
        )
        if result.status == "failed":
            node = ET.SubElement(testcase, "failure", {"type": "product_assertion"})
            node.text = details or "Gold assertion failed"
        elif result.status in {"environment_error", "tool_error", "framework_error"}:
            node = ET.SubElement(testcase, "error", {"type": result.status})
            node.text = details
        elif result.status in {"skipped", "not_applicable"}:
            node = ET.SubElement(testcase, "skipped")
            node.text = result.applicability_reason or "skipped"
        if result.measurements:
            measurements = ET.SubElement(testcase, "system-out")
            measurements.text = "".join(
                f"{measurement.name}={measurement.value}{measurement.unit}\n"
                for measurement in result.measurements
            )
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def generate_reports(
    artifacts: RunArtifacts,
    output_dir: Path,
) -> Dict[str, Any]:
    coverage = coverage_summary(artifacts.gold)
    scored = score_results(artifacts.project.raw, artifacts.gold, artifacts.results)
    report = {
        "schema_version": "1.0",
        "run_id": artifacts.run_id,
        "project_id": artifacts.project.project_id,
        "gold": {
            "bundle_id": artifacts.gold.bundle_id,
            "content_hash": artifacts.gold.content_hash(),
            "file_sha256": sha256_file(artifacts.gold.path),
            "coverage": coverage,
        },
        "environment": artifacts.environment,
        "score": scored,
        "cases": [item.as_dict() for item in artifacts.results],
    }
    write_json(output_dir / "report.json", report)
    write_markdown_report(output_dir / "report.md", artifacts, scored)
    write_junit_report(output_dir / "results.junit.xml", artifacts)
    write_json(output_dir / "scores.json", scored)
    return report
