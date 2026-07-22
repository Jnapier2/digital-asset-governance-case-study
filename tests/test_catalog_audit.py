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


if __name__ == "__main__":
    unittest.main()
