from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import catalog_audit


FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_catalog.csv"


class CatalogAuditFixtureTests(unittest.TestCase):
    def test_synthetic_fixture_has_expected_metrics(self) -> None:
        report = catalog_audit.audit_catalog(FIXTURE)

        self.assertTrue(report["schema"]["valid"])
        self.assertEqual(report["validation"]["issue_count"], 0)
        self.assertEqual(
            report["metrics"]["records"],
            {"total": 8, "valid": 8, "valid_rate": 1.0},
        )
        self.assertEqual(report["metrics"]["provenance"]["documented_rate"], 0.75)
        self.assertEqual(report["metrics"]["coverage"]["checksum_sha256"]["rate"], 0.75)
        self.assertEqual(report["metrics"]["confidence"]["mean"], 0.74)
        self.assertEqual(
            report["metrics"]["confidence"]["counts"],
            {"high": 4, "medium": 2, "low": 2},
        )
        self.assertEqual(
            [item["asset_id"] for item in report["metrics"]["confidence"]["review_queue"]],
            ["ASSET-006", "ASSET-004"],
        )

    def test_fixture_detects_each_duplicate_candidate_type(self) -> None:
        report = catalog_audit.audit_catalog(FIXTURE)
        duplicates = report["duplicate_candidates"]

        self.assertEqual(len(duplicates["exact_name"]), 1)
        self.assertEqual(
            duplicates["exact_name"][0]["asset_ids"],
            ("ASSET-003", "ASSET-004"),
        )
        self.assertEqual(len(duplicates["normalized_name"]), 2)
        self.assertEqual(len(duplicates["exact_checksum"]), 1)
        self.assertEqual(
            duplicates["exact_checksum"][0]["asset_ids"],
            ("ASSET-001", "ASSET-002"),
        )

    def test_text_summary_is_outcome_oriented(self) -> None:
        report = catalog_audit.audit_catalog(FIXTURE)
        summary = catalog_audit.render_text(report)

        self.assertIn("Records: 8 total; 8 valid (100.0%)", summary)
        self.assertIn("Provenance documented: 6/8 (75.0%)", summary)
        self.assertIn(
            "Prioritized review queue: 7 assets; high 2, medium 5, low 0",
            summary,
        )
        self.assertIn("Validation issues: 0", summary)

    def test_review_queue_prioritizes_identity_and_provenance_risk(self) -> None:
        report = catalog_audit.audit_catalog(FIXTURE)
        queue = report["review_queue"]

        self.assertEqual(queue["counts"], {"high": 2, "medium": 5, "low": 0})
        self.assertEqual(
            [item["asset_id"] for item in queue["items"]],
            [
                "ASSET-004",
                "ASSET-006",
                "ASSET-001",
                "ASSET-002",
                "ASSET-003",
                "ASSET-007",
                "ASSET-008",
            ],
        )
        highest_risk = queue["items"][0]
        self.assertEqual(highest_risk["priority"], "high")
        self.assertEqual(
            highest_risk["reason_codes"],
            [
                "low_confidence",
                "unknown_provenance",
                "missing_checksum",
                "duplicate_exact_name",
            ],
        )
        self.assertIn("before any reuse", highest_risk["recommended_next_step"])


class CatalogAuditRuleTests(unittest.TestCase):
    def write_csv(self, rows: list[dict[str, str]], columns=None) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "catalog.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns or catalog_audit.EXPECTED_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def valid_row(self) -> dict[str, str]:
        return {
            "asset_id": "TEST-001",
            "artifact_name": "Synthetic Record.pdf",
            "asset_type": "document",
            "source_system": "managed_repository",
            "source_reference": "TEST-REF-001",
            "owner_role": "Governance",
            "provenance_status": "verified",
            "confidence": "0.90",
            "classification": "internal",
            "last_reviewed": "2026-07-01",
            "checksum_sha256": "f" * 64,
        }

    def test_missing_column_is_schema_drift(self) -> None:
        columns = catalog_audit.EXPECTED_COLUMNS[:-1]
        row = self.valid_row()
        row.pop("checksum_sha256")
        report = catalog_audit.audit_catalog(self.write_csv([row], columns))

        self.assertFalse(report["schema"]["valid"])
        issue_codes = {issue["code"] for issue in report["validation"]["issues"]}
        self.assertIn("missing_column", issue_codes)

    def test_invalid_confidence_and_uncontrolled_owner_are_rejected(self) -> None:
        row = self.valid_row()
        row["confidence"] = "1.20"
        row["owner_role"] = "NamedOwner@invalid"
        report = catalog_audit.audit_catalog(self.write_csv([row]))
        issue_codes = {issue["code"] for issue in report["validation"]["issues"]}

        self.assertIn("out_of_range", issue_codes)
        self.assertIn("invalid_owner_role", issue_codes)
        self.assertEqual(report["metrics"]["confidence"]["scored_count"], 0)

    def test_person_name_cannot_be_used_as_owner_role(self) -> None:
        row = self.valid_row()
        row["owner_role"] = "Jerry Napier"
        report = catalog_audit.audit_catalog(self.write_csv([row]))

        self.assertIn(
            "invalid_owner_role",
            {issue["code"] for issue in report["validation"]["issues"]},
        )

    def test_header_only_catalog_is_rejected(self) -> None:
        report = catalog_audit.audit_catalog(self.write_csv([]))

        self.assertFalse(report["schema"]["valid"])
        self.assertEqual(
            report["metrics"]["records"],
            {"total": 0, "valid": 0, "valid_rate": 0.0},
        )
        self.assertIn(
            "empty_catalog",
            {issue["code"] for issue in report["validation"]["issues"]},
        )

    def test_artifact_paths_are_rejected(self) -> None:
        row = self.valid_row()
        row["artifact_name"] = "folder/Synthetic Record.pdf"
        report = catalog_audit.audit_catalog(self.write_csv([row]))

        self.assertIn(
            "path_not_allowed",
            {issue["code"] for issue in report["validation"]["issues"]},
        )

    def test_normalization_handles_unicode_case_and_separators(self) -> None:
        self.assertEqual(
            catalog_audit.normalize_artifact_name("Launch—Brief_v2.DOCX"),
            "launch brief v2",
        )

    def test_partial_provenance_without_other_signals_is_low_priority(self) -> None:
        row = self.valid_row()
        row["provenance_status"] = "partial"
        report = catalog_audit.audit_catalog(self.write_csv([row]))

        self.assertEqual(
            report["review_queue"],
            {
                "counts": {"high": 0, "medium": 0, "low": 1},
                "items": [
                    {
                        "asset_id": "TEST-001",
                        "priority": "low",
                        "reason_codes": ["partial_provenance"],
                        "recommended_next_step": (
                            "Complete the provenance record during the next "
                            "stewardship review."
                        ),
                    }
                ],
            },
        )


class CatalogInputBoundaryTests(unittest.TestCase):
    """Synthetic CSV-contract regressions; never changes the input catalog."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "catalog.csv"
        with FIXTURE.open(encoding="utf-8", newline="") as handle:
            self.row = next(csv.DictReader(handle))

    def write(self, columns=None, values=None) -> Path:
        columns = list(catalog_audit.EXPECTED_COLUMNS) if columns is None else columns
        values = [self.row[key] for key in columns] if values is None else values
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerow(values)
        return self.path

    def codes(self):
        return {i["code"] for i in catalog_audit.audit_catalog(self.path)["validation"]["issues"]}

    def test_duplicate_header_does_not_silently_select_last_value(self):
        cols = list(catalog_audit.EXPECTED_COLUMNS) + ["confidence"]
        self.write(cols, list(self.row.values()) + ["0.10"])
        original = self.path.read_bytes()
        report = catalog_audit.audit_catalog(self.path)
        self.assertFalse(report["schema"]["valid"])
        self.assertIn("duplicate_column", self.codes())
        self.assertEqual(report["metrics"]["confidence"]["scored_count"], 0)
        self.assertEqual(self.path.read_bytes(), original)

    def test_duplicate_optional_column_is_also_rejected(self):
        cols = list(catalog_audit.EXPECTED_COLUMNS) + ["checksum_sha256"]
        self.write(cols, list(self.row.values()) + [""])
        self.assertIn("duplicate_column", self.codes())

    def test_missing_optional_field_is_not_explicit_blank(self):
        self.write(values=list(self.row.values())[:-1])
        self.assertIn("missing_values", self.codes())
        self.assertEqual(catalog_audit.audit_catalog(self.path)["metrics"]["records"]["valid"], 0)

    def test_explicit_blank_optional_field_remains_valid(self):
        self.row["checksum_sha256"] = ""
        self.write()
        self.assertEqual(self.codes(), set())

    def test_dates_require_documented_calendar_format(self):
        for value in ("20260701", "2026-W27-3", "2026-W27", "2026-7-1", "2026-07-01T00:00:00"):
            with self.subTest(value=value):
                self.row["last_reviewed"] = value
                self.write()
                self.assertIn("invalid_date", self.codes())

    def test_calendar_validity_and_leap_days_are_checked(self):
        for value, valid in (("2024-02-29", True), ("2026-02-29", False), ("2026-13-01", False)):
            with self.subTest(value=value):
                self.row["last_reviewed"] = value
                self.write()
                self.assertEqual("invalid_date" not in self.codes(), valid)

    def test_reordered_unique_columns_remain_supported(self):
        self.write(list(reversed(catalog_audit.EXPECTED_COLUMNS)))
        self.assertEqual(self.codes(), set())

    def test_bom_and_quoted_commas_and_newlines_remain_supported(self):
        self.row["artifact_name"] = "Synthetic, report\ncontinued.pdf"
        self.write()
        self.path.write_bytes(b"\xef\xbb\xbf" + self.path.read_bytes())
        rows, issues = catalog_audit.read_catalog(self.path)
        self.assertEqual(issues, [])
        self.assertEqual(rows[0]["artifact_name"], self.row["artifact_name"])
        self.assertEqual(self.codes(), set())

    def test_unterminated_quoted_field_returns_clean_cli_error(self):
        import contextlib
        import io
        self.write()
        self.path.write_bytes(self.path.read_bytes() + b'"unterminated')
        captured, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(err):
            code = catalog_audit.main([str(self.path), "--format", "json"])
        self.assertEqual(code, 2)
        self.assertEqual(captured.getvalue(), "")
        self.assertNotIn("Traceback", err.getvalue())

    def test_invalid_encoding_returns_clean_cli_error(self):
        import contextlib
        import io
        self.path.write_bytes(b"private-fixture-marker \xff")
        captured, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(err):
            code = catalog_audit.main([str(self.path)])
        self.assertEqual(code, 2)
        self.assertEqual(captured.getvalue(), "")
        self.assertNotIn("private-fixture-marker", err.getvalue())
        self.assertIn("UTF-8", err.getvalue())

    def test_duplicate_header_returns_validation_exit_code(self):
        import contextlib
        import io
        self.write(list(catalog_audit.EXPECTED_COLUMNS) + ["confidence"], list(self.row.values()) + ["0.1"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(catalog_audit.main([str(self.path)]), 1)

    def test_extra_values_remain_rejected(self):
        self.write(values=list(self.row.values()) + ["unexpected"])
        self.assertIn("extra_values", self.codes())

    def test_input_bytes_are_unchanged_after_success_and_rejection(self):
        for value in ("0.5", "nan", "1e400"):
            with self.subTest(value=value):
                self.row["confidence"] = value
                self.write()
                original = self.path.read_bytes()
                catalog_audit.audit_catalog(self.path)
                self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
