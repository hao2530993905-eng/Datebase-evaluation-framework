"""Command-line interface for the Database Evaluation Framework."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import List, Optional

from .config import load_project
from .detection import detect_database
from .errors import ConfigurationError, DBEvalError, EnvironmentError, GoldValidationError, ToolError
from .generation import generate_draft
from .gold import freeze_gold, validate_gold_dict
from .metrics import INDICATORS
from .mutation import mutation_summary
from .reporting import generate_reports
from .runner import EvaluationRunner
from .util import read_json, write_json


def _project(value: str):
    return load_project(Path(value).resolve())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbeval",
        description="可审计、可复现的数据库评测框架",
    )
    parser.add_argument("--version", action="version", version="dbeval 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_project = subparsers.add_parser("validate-project", help="校验项目配置")
    validate_project.add_argument("project")

    validate_gold = subparsers.add_parser("validate-gold", help="校验 Gold 结构和来源")
    validate_gold.add_argument("gold")
    validate_gold.add_argument("--require-frozen", action="store_true")
    validate_gold.add_argument("--signing-key-env", default="DBEVAL_GOLD_KEY")

    freeze = subparsers.add_parser("freeze-gold", help="冻结已经独立验证的 Gold")
    freeze.add_argument("input")
    freeze.add_argument("output")
    freeze.add_argument("--approved-by", required=True)
    freeze.add_argument("--signing-key-env", default="DBEVAL_GOLD_KEY")

    detect = subparsers.add_parser("detect", help="识别数据库、版本和能力")
    detect.add_argument("project")
    detect.add_argument("--output")

    generate = subparsers.add_parser("generate-gold", help="生成候选 Gold 草稿")
    generate.add_argument("project")
    generate.add_argument("--output", required=True)
    generate.add_argument("--model", help="可选的实体契约 JSON")

    run = subparsers.add_parser("run", help="执行 Gold 并生成完整报告")
    run.add_argument("project")
    run.add_argument("--gold", required=True)
    run.add_argument("--output", default="evaluation-output")
    run.add_argument("--fail-under", type=float)
    run.add_argument("--require-complete", action="store_true")
    run.add_argument(
        "--min-execution-completeness",
        type=float,
        default=1.0,
        help="质量门控要求的最低用例执行完整度，默认 1.0",
    )

    mutation = subparsers.add_parser("mutation-report", help="汇总 Gold 变异测试结果")
    mutation.add_argument("input")
    mutation.add_argument("--output")

    metrics = subparsers.add_parser("metrics", help="输出指标目录")
    metrics.add_argument("--output")
    return parser


def _validate_project(args) -> int:
    project = _project(args.project)
    print(
        json.dumps(
            {
                "status": "ok",
                "project_id": project.project_id,
                "backend": project.backend,
                "root": str(project.root),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _validate_gold(args) -> int:
    raw = read_json(Path(args.gold).resolve())
    signing_key = os.environ.get(args.signing_key_env) or None
    messages = validate_gold_dict(
        raw,
        require_frozen=args.require_frozen,
        signing_key=signing_key,
        base_dir=Path(args.gold).resolve().parent,
    )
    errors = [item for item in messages if not item.startswith("WARNING:")]
    warnings = [item.removeprefix("WARNING: ") for item in messages if item.startswith("WARNING:")]
    output = {
        "status": "invalid" if errors else "ok",
        "errors": errors,
        "warnings": warnings,
        "bundle_id": raw.get("bundle_id"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 2 if errors else 0


def _freeze_gold(args) -> int:
    signing_key = os.environ.get(args.signing_key_env) or None
    raw = freeze_gold(
        Path(args.input),
        Path(args.output),
        approved_by=args.approved_by,
        signing_key=signing_key,
    )
    print(
        json.dumps(
            {
                "status": "frozen",
                "output": str(Path(args.output).resolve()),
                "content_hash": raw["freeze"]["content_hash"],
                "signature_algorithm": raw["freeze"]["signature_algorithm"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _detect(args) -> int:
    project = _project(args.project)
    with tempfile.TemporaryDirectory(prefix="dbeval-detect-") as temporary:
        result = detect_database(project, Path(temporary))
    if args.output:
        write_json(Path(args.output).resolve(), result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _generate(args) -> int:
    project = _project(args.project)
    report = generate_draft(
        project,
        Path(args.output).resolve(),
        model_path=Path(args.model).resolve() if args.model else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _run(args) -> int:
    project = _project(args.project)
    artifacts = EvaluationRunner(project, Path(args.output).resolve()).run(Path(args.gold))
    report = generate_reports(artifacts, artifacts.run_dir)
    scored = report["score"]
    print(
        json.dumps(
            {
                "status": "completed",
                "run_id": artifacts.run_id,
                "total_score": scored["total_score"],
                "quality_status": scored["quality_status"],
                "indicator_coverage": scored["coverage"]["indicator_coverage"],
                "execution_completeness": scored["execution"]["execution_completeness"],
                "report_json": str((artifacts.run_dir / "report.json").resolve()),
                "report_markdown": str((artifacts.run_dir / "report.md").resolve()),
                "junit": str((artifacts.run_dir / "results.junit.xml").resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    failed_gate = False
    if args.require_complete and not scored["score_complete"]:
        failed_gate = True
        print("质量门控失败：评分不完整。", file=sys.stderr)
    if scored["execution"]["execution_completeness"] < args.min_execution_completeness:
        failed_gate = True
        print(
            "质量门控失败：用例执行完整度低于要求 "
            f"{args.min_execution_completeness:.2%}。",
            file=sys.stderr,
        )
    if args.fail_under is not None:
        if scored["total_score"] is None or scored["total_score"] < args.fail_under:
            failed_gate = True
            print(
                f"质量门控失败：总分低于 {args.fail_under:.2f}。",
                file=sys.stderr,
            )
    return 1 if failed_gate else 0


def _mutation(args) -> int:
    result = mutation_summary(read_json(Path(args.input).resolve()))
    if args.output:
        write_json(Path(args.output).resolve(), result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _metrics(args) -> int:
    value = {
        "schema_version": "1.0",
        "indicators": [
            {
                "id": item.id,
                "dimension": item.dimension,
                "name": item.name,
                "description": item.description,
                "default_weight": item.default_weight,
            }
            for item in INDICATORS
        ],
    }
    if args.output:
        write_json(Path(args.output).resolve(), value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        handlers = {
            "validate-project": _validate_project,
            "validate-gold": _validate_gold,
            "freeze-gold": _freeze_gold,
            "detect": _detect,
            "generate-gold": _generate,
            "run": _run,
            "mutation-report": _mutation,
            "metrics": _metrics,
        }
        return handlers[args.command](args)
    except (ConfigurationError, GoldValidationError) as exc:
        print(f"配置或 Gold 错误：{exc}", file=sys.stderr)
        return 2
    except EnvironmentError as exc:
        print(f"评测环境错误：{exc}", file=sys.stderr)
        return 3
    except ToolError as exc:
        print(f"工具执行错误：{exc}", file=sys.stderr)
        return 4
    except DBEvalError as exc:
        print(f"评测错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"未处理错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        if os.environ.get("DBEVAL_DEBUG"):
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
