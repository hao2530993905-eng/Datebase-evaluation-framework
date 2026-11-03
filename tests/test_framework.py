from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dbeval.config import load_project
from dbeval.errors import GoldValidationError
from dbeval.generation import generate_draft
from dbeval.gold import GoldBundle, coverage_summary, freeze_gold, load_gold, validate_gold_dict
from dbeval.metrics import DIMENSION_NAMES, INDICATORS, INDICATOR_MAP
from dbeval.models import CaseResult, Finding
from dbeval.mutation import mutation_summary
from dbeval.reporting import generate_reports
from dbeval.runner import EvaluationRunner
from dbeval.scoring import score_results
from dbeval.util import read_json, redact, write_json


def base_gold_case(case_id="DB1.test", indicator="DB1.1"):
    dimension = INDICATOR_MAP[indicator].dimension
    return {
        "id": case_id,
        "dimension": dimension,
        "indicator": indicator,
        "title": "test case",
        "type": "sql",
        "weight": 1.0,
        "steps": [
            {
                "sql": "SELECT 1",
                "expect": {"kind": "scalar", "value": 1},
            }
        ],
        "provenance": {
            "source_ids": ["rule:test"],
            "rationale": "test rule",
            "validation_status": "verified",
        },
    }


def base_gold(cases):
    return {
        "schema_version": "1.0",
        "bundle_id": "test-gold",
        "software": {"name": "test-software", "version": "1.0.0"},
        "created_at": "2026-01-01T00:00:00Z",
        "created_by": "tests",
        "dimension_weights": {name: 1.0 for name in DIMENSION_NAMES},
        "cases": cases,
    }


class MetricTests(unittest.TestCase):
    def test_indicator_catalog_weights_and_dimensions(self):
        self.assertEqual({item.dimension for item in INDICATORS}, set(DIMENSION_NAMES))
        for dimension in DIMENSION_NAMES:
            weights = [item.default_weight for item in INDICATORS if item.dimension == dimension]
            self.assertAlmostEqual(sum(weights), 1.0)


class GoldTests(unittest.TestCase):
    def test_freeze_binds_evidence_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "requirements.md"
            evidence.write_text("stock must stay non-negative\n", encoding="utf-8")
            raw = base_gold([base_gold_case()])
            raw["cases"][0]["provenance"]["source_ids"] = ["file:requirements.md#stock"]
            source = root / "gold.unfrozen.json"
            frozen = root / "gold.json"
            write_json(source, raw)
            freeze_gold(source, frozen, approved_by="unit-test")
            loaded = load_gold(frozen, require_frozen=True)
            self.assertEqual(loaded.cases[0].indicator, "DB1.1")
            self.assertIn("file:requirements.md#stock", loaded.raw["evidence_manifest"]["files"])

            evidence.write_text("stock may become negative\n", encoding="utf-8")
            with self.assertRaises(GoldValidationError):
                load_gold(frozen, require_frozen=True)

    def test_candidate_cannot_be_frozen_without_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = base_gold([base_gold_case()])
            raw["cases"][0]["provenance"]["validation_status"] = "candidate"
            source = root / "candidate.json"
            write_json(source, raw)
            with self.assertRaises(GoldValidationError):
                freeze_gold(source, root / "frozen.json", approved_by="reviewer")

    def test_signed_approval_metadata_is_authenticated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "gold.unfrozen.json"
            frozen = root / "gold.json"
            write_json(source, base_gold([base_gold_case()]))
            freeze_gold(
                source,
                frozen,
                approved_by="reviewer-a",
                signing_key="test-secret",
            )
            valid = validate_gold_dict(
                read_json(frozen),
                require_frozen=True,
                signing_key="test-secret",
                base_dir=root,
            )
            self.assertFalse([item for item in valid if not item.startswith("WARNING:")])

            tampered = read_json(frozen)
            tampered["freeze"]["approved_by"] = "reviewer-b"
            errors = validate_gold_dict(
                tampered,
                require_frozen=True,
                signing_key="test-secret",
                base_dir=root,
            )
            self.assertTrue(any("HMAC" in item for item in errors))

    def test_coverage_separate_from_quality(self):
        bundle = GoldBundle(Path("/tmp/test.json"), base_gold([base_gold_case()]))
        summary = coverage_summary(bundle)
        self.assertEqual(summary["covered_indicators"], 1)
        self.assertEqual(summary["case_count"], 1)
        self.assertLess(summary["indicator_coverage"], 0.1)


class ScoringTests(unittest.TestCase):
    def test_environment_error_excluded_but_marks_incomplete(self):
        cases = [base_gold_case("DB2.a", "DB2.1"), base_gold_case("DB2.b", "DB2.2"), base_gold_case("DB2.c", "DB2.3")]
        bundle = GoldBundle(Path("/tmp/test.json"), base_gold(cases))
        results = [
            CaseResult("DB2.a", "DB2", "DB2.1", "failed", status="failed", point_score=0.0, weight=1.0),
            CaseResult("DB2.b", "DB2", "DB2.2", "error", status="environment_error", weight=1.0),
            CaseResult("DB2.c", "DB2", "DB2.3", "passed", status="passed", point_score=1.0, weight=1.0),
        ]
        scored = score_results(
            {"dimension_weights": {name: 1.0 for name in DIMENSION_NAMES}},
            bundle,
            results,
        )
        db2 = next(item for item in scored["dimensions"] if item["id"] == "DB2")
        self.assertAlmostEqual(db2["score"], 46.15)
        self.assertEqual(db2["status"], "incomplete")
        self.assertEqual(scored["execution"]["execution_completeness"], 0.6667)
        self.assertFalse(scored["score_complete"])

    def test_critical_security_finding_applies_score_cap(self):
        cases = [base_gold_case("DB7.a", "DB7.1")]
        bundle = GoldBundle(Path("/tmp/test.json"), base_gold(cases))
        result = CaseResult(
            "DB7.a",
            "DB7",
            "DB7.1",
            "security",
            status="passed",
            point_score=1.0,
            findings=[
                Finding(
                    id="SEC-1",
                    severity="critical",
                    title="Authentication bypass",
                )
            ],
        )
        scored = score_results(
            {"dimension_weights": {name: 1.0 for name in DIMENSION_NAMES}},
            bundle,
            [result],
        )
        db7 = next(item for item in scored["dimensions"] if item["id"] == "DB7")
        self.assertEqual(db7["raw_score_before_hard_gate"], 100.0)
        self.assertEqual(db7["score_cap"], 20.0)
        self.assertEqual(db7["score"], 20.0)

    def test_duplicate_finding_uses_highest_severity_once(self):
        cases = [base_gold_case("DB7.a", "DB7.1"), base_gold_case("DB7.b", "DB7.2")]
        bundle = GoldBundle(Path("/tmp/test.json"), base_gold(cases))
        results = [
            CaseResult(
                "DB7.a",
                "DB7",
                "DB7.1",
                "security",
                status="passed",
                point_score=1.0,
                findings=[Finding("SEC-1", "low", "duplicate")],
            ),
            CaseResult(
                "DB7.b",
                "DB7",
                "DB7.2",
                "security",
                status="passed",
                point_score=1.0,
                findings=[Finding("SEC-1", "high", "duplicate")],
            ),
        ]
        scored = score_results(
            {"dimension_weights": {name: 1.0 for name in DIMENSION_NAMES}},
            bundle,
            results,
        )
        db7 = next(item for item in scored["dimensions"] if item["id"] == "DB7")
        self.assertEqual(len(db7["findings"]), 1)
        self.assertEqual(db7["score_cap"], 40.0)


class MutationTests(unittest.TestCase):
    def test_mutation_quality_is_separate(self):
        result = mutation_summary(
            {
                "schema_version": "1.0",
                "mutants": [
                    {"id": "M1", "status": "killed", "killed_by": ["DB1.pk"]},
                    {"id": "M2", "status": "survived"},
                    {"id": "M3", "status": "equivalent"},
                    {"id": "M4", "status": "uncovered"},
                ],
            }
        )
        self.assertEqual(result["mutation_score"], 0.3333)
        self.assertEqual(result["evaluable_mutants"], 3)


class RedactionTests(unittest.TestCase):
    def test_credentials_are_redacted_from_logs_and_urls(self):
        text = 'password="hunter2" token: abc123 postgres://user:secret@db/test'
        result = redact(text, "stdout")
        self.assertNotIn("hunter2", result)
        self.assertNotIn("abc123", result)
        self.assertNotIn("secret@", result)
        self.assertIn("***REDACTED***", result)


class EndToEndTests(unittest.TestCase):
    def test_isolated_sqlite_run_and_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "schema.sql").write_text(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE products (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    stock INTEGER NOT NULL CHECK (stock >= 0)
                );
                """,
                encoding="utf-8",
            )
            (root / "seed.sql").write_text(
                "INSERT INTO products(id, name, stock) VALUES (1, 'Keyboard', 5);\n",
                encoding="utf-8",
            )
            write_json(
                root / "project.json",
                {
                    "schema_version": "1.0",
                    "project_id": "unit-sqlite",
                    "software": {"name": "test-software", "version": "1.0.0"},
                    "database": {
                        "backend": "sqlite",
                        "initialize": {"schema": "schema.sql", "seed": "seed.sql"},
                    },
                    "features": {},
                    "dimension_weights": {name: 1.0 for name in DIMENSION_NAMES},
                    "execution": {
                        "require_frozen_gold": True,
                        "case_timeout_seconds": 10,
                        "busy_timeout_seconds": 2,
                    },
                },
            )
            cases = [
                base_gold_case("DB1.table", "DB1.1"),
                {
                    **base_gold_case("DB2.update", "DB2.1"),
                    "steps": [
                        {
                            "sql": "UPDATE products SET stock = stock - 2 WHERE id = 1",
                            "expect": {"kind": "affected_rows", "value": 1},
                        },
                        {
                            "sql": "SELECT stock FROM products WHERE id = 1",
                            "expect": {"kind": "scalar", "value": 3},
                        },
                    ],
                },
                {
                    "id": "DB8.integrity",
                    "dimension": "DB8",
                    "indicator": "DB8.1",
                    "title": "integrity",
                    "type": "health",
                    "check": "integrity",
                    "expect": {"kind": "health_pass", "value": True},
                    "provenance": {
                        "source_ids": ["rule:sqlite-integrity"],
                        "rationale": "native health rule",
                        "validation_status": "verified",
                    },
                },
            ]
            gold_source = root / "gold.unfrozen.json"
            gold_path = root / "gold.json"
            write_json(gold_source, base_gold(cases))
            freeze_gold(gold_source, gold_path, approved_by="unit-test")
            project = load_project(root / "project.json")
            artifacts = EvaluationRunner(project, root / "output").run(gold_path)
            report = generate_reports(artifacts, artifacts.run_dir)
            self.assertEqual(report["score"]["execution"]["execution_completeness"], 1.0)
            self.assertEqual(report["score"]["execution"]["status_counts"], {"passed": 3})
            self.assertTrue((artifacts.run_dir / "report.json").is_file())
            self.assertTrue((artifacts.run_dir / "report.md").is_file())
            self.assertTrue((artifacts.run_dir / "results.junit.xml").is_file())
            generated = generate_draft(project, root / "generated")
            self.assertGreater(generated["candidate_case_count"], 0)
            self.assertTrue(generated["requires_human_verification"])

            # Every case uses an isolated copy; the clean template still has stock=5.
            template = artifacts.run_dir / "environment" / "template.sqlite3"
            connection = sqlite3.connect(str(template))
            try:
                self.assertEqual(
                    connection.execute("SELECT stock FROM products WHERE id = 1").fetchone()[0],
                    5,
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
