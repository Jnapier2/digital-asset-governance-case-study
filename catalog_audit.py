"""Audit a synthetic digital-asset catalog using only the Python standard library.

The tool validates a compact CSV schema, calculates coverage and provenance
metrics, handles confidence scores explicitly, and identifies duplicate
candidates for human review. It never mutates the input catalog.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from statistics import fmean
from typing import Iterable, Mapping, Sequence


EXPECTED_COLUMNS = (
    "asset_id",
    "artifact_name",
    "asset_type",
    "source_system",
    "source_reference",
    "owner_role",
    "provenance_status",
    "confidence",
    "classification",
    "last_reviewed",
    "checksum_sha256",
)

REQUIRED_FIELDS = EXPECTED_COLUMNS[:-1]

ALLOWED_VALUES = {
    "asset_type": {
        "archive",
        "code",
        "document",
        "image",
        "presentation",
        "spreadsheet",
        "video",
    },
    "source_system": {"archive_import", "department_share", "managed_repository"},
    "provenance_status": {"partial", "unknown", "verified"},
    "classification": {"internal", "public", "restricted"},
}

ALLOWED_OWNER_ROLES = frozenset(
    {"Experience", "Governance", "Operations", "Strategy"}
)

HIGH_CONFIDENCE_MIN = 0.85
MEDIUM_CONFIDENCE_MIN = 0.60
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$")
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class ValidationIssue:
    """A precise, machine-readable validation finding."""

    row_number: int
    field: str
    code: str
    message: str


@dataclass(frozen=True)
class DuplicateGroup:
    """Records that deserve review under one matching rule."""

    match_key: str
    asset_ids: tuple[str, ...]
    artifact_names: tuple[str, ...]


def normalize_artifact_name(value: str) -> str:
    """Return a conservative comparison key for a filename-like label.

    The normalization removes the final extension, applies Unicode NFKC and
    case folding, treats punctuation/underscores as separators, and collapses
    whitespace. It intentionally does not remove version numbers or dates.
    """

    name = Path(value.strip()).stem
    normalized = unicodedata.normalize("NFKC", name).casefold()
    normalized = re.sub(r"[\W_]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def read_catalog(path: Path) -> tuple[list[dict[str, str]], list[ValidationIssue]]:
    """Read *path* and return rows plus file/schema-level issues."""

    issues: list[ValidationIssue] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return [], [
                ValidationIssue(1, "schema", "missing_header", "CSV header is missing.")
            ]

        actual = tuple(reader.fieldnames)
        missing = [name for name in EXPECTED_COLUMNS if name not in actual]
        unexpected = [name for name in actual if name not in EXPECTED_COLUMNS]
        for name in missing:
            issues.append(
                ValidationIssue(
                    1,
                    name,
                    "missing_column",
                    f"Required column {name!r} is missing.",
                )
            )
        for name in unexpected:
            issues.append(
                ValidationIssue(
                    1,
                    name,
                    "unexpected_column",
                    f"Unexpected column {name!r} indicates schema drift.",
                )
            )

        rows: list[dict[str, str]] = []
        for row_number, raw_row in enumerate(reader, start=2):
            if None in raw_row:
                issues.append(
                    ValidationIssue(
                        row_number,
                        "schema",
                        "extra_values",
                        "Row has more values than the header defines.",
                    )
                )
            row = {
                column: (raw_row.get(column) or "").strip()
                for column in EXPECTED_COLUMNS
            }
            rows.append(row)

        if not rows:
            issues.append(
                ValidationIssue(
                    1,
                    "catalog",
                    "empty_catalog",
                    "Catalog must contain at least one data record.",
                )
            )

    return rows, issues


def validate_rows(rows: Sequence[Mapping[str, str]]) -> list[ValidationIssue]:
    """Validate values and candidate-key uniqueness for catalog rows."""

    issues: list[ValidationIssue] = []
    first_asset_id_row: dict[str, int] = {}

    for row_number, row in enumerate(rows, start=2):
        for field in REQUIRED_FIELDS:
            if not row.get(field, "").strip():
                issues.append(
                    ValidationIssue(
                        row_number,
                        field,
                        "required",
                        f"{field!r} must not be blank.",
                    )
                )

        asset_id = row.get("asset_id", "")
        if asset_id:
            if not SAFE_IDENTIFIER.fullmatch(asset_id):
                issues.append(
                    ValidationIssue(
                        row_number,
                        "asset_id",
                        "invalid_identifier",
                        "Asset ID must be 3-64 safe identifier characters.",
                    )
                )
            if asset_id in first_asset_id_row:
                issues.append(
                    ValidationIssue(
                        row_number,
                        "asset_id",
                        "duplicate_identifier",
                        f"Asset ID first appeared on row {first_asset_id_row[asset_id]}.",
                    )
                )
            else:
                first_asset_id_row[asset_id] = row_number

        artifact_name = row.get("artifact_name", "")
        if artifact_name and ("/" in artifact_name or "\\" in artifact_name):
            issues.append(
                ValidationIssue(
                    row_number,
                    "artifact_name",
                    "path_not_allowed",
                    "Artifact name must be a label, not a filesystem path.",
                )
            )

        source_reference = row.get("source_reference", "")
        if source_reference and not SAFE_IDENTIFIER.fullmatch(source_reference):
            issues.append(
                ValidationIssue(
                    row_number,
                    "source_reference",
                    "invalid_reference",
                    "Source reference must be an opaque safe identifier.",
                )
            )

        owner_role = row.get("owner_role", "")
        if owner_role and owner_role not in ALLOWED_OWNER_ROLES:
            issues.append(
                ValidationIssue(
                    row_number,
                    "owner_role",
                    "invalid_owner_role",
                    "Expected one of the controlled role labels: "
                    f"{', '.join(sorted(ALLOWED_OWNER_ROLES))}.",
                )
            )

        for field, allowed in ALLOWED_VALUES.items():
            value = row.get(field, "")
            if value and value not in allowed:
                issues.append(
                    ValidationIssue(
                        row_number,
                        field,
                        "invalid_enum",
                        f"Expected one of: {', '.join(sorted(allowed))}.",
                    )
                )

        confidence = row.get("confidence", "")
        if confidence:
            try:
                score = float(confidence)
            except ValueError:
                issues.append(
                    ValidationIssue(
                        row_number,
                        "confidence",
                        "invalid_number",
                        "Confidence must be a number from 0 through 1.",
                    )
                )
            else:
                if not 0.0 <= score <= 1.0:
                    issues.append(
                        ValidationIssue(
                            row_number,
                            "confidence",
                            "out_of_range",
                            "Confidence must be between 0 and 1 inclusive.",
                        )
                    )

        reviewed = row.get("last_reviewed", "")
        if reviewed:
            try:
                date.fromisoformat(reviewed)
            except ValueError:
                issues.append(
                    ValidationIssue(
                        row_number,
                        "last_reviewed",
                        "invalid_date",
                        "Review date must use ISO format YYYY-MM-DD.",
                    )
                )

        checksum = row.get("checksum_sha256", "")
        if checksum and not SHA256.fullmatch(checksum):
            issues.append(
                ValidationIssue(
                    row_number,
                    "checksum_sha256",
                    "invalid_checksum",
                    "Checksum must be blank or a 64-character SHA-256 hex digest.",
                )
            )

    return issues


def _duplicate_groups(
    rows: Sequence[Mapping[str, str]],
    key_field: str,
    *,
    transform=lambda value: value,
    normalized_only: bool = False,
) -> list[DuplicateGroup]:
    grouped: defaultdict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        raw_value = row.get(key_field, "").strip()
        if raw_value:
            match_key = transform(raw_value)
            if match_key:
                grouped[match_key].append(row)

    findings: list[DuplicateGroup] = []
    for match_key in sorted(grouped):
        matches = grouped[match_key]
        if len(matches) < 2:
            continue
        names = tuple(row.get("artifact_name", "") for row in matches)
        if normalized_only and len(set(names)) == 1:
            continue
        findings.append(
            DuplicateGroup(
                match_key=match_key,
                asset_ids=tuple(row.get("asset_id", "") for row in matches),
                artifact_names=names,
            )
        )
    return findings


def duplicate_candidates(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, list[DuplicateGroup]]:
    """Find exact, normalized-only, and checksum duplicate candidates."""

    return {
        "exact_name": _duplicate_groups(rows, "artifact_name"),
        "normalized_name": _duplicate_groups(
            rows,
            "artifact_name",
            transform=normalize_artifact_name,
            normalized_only=True,
        ),
        "exact_checksum": _duplicate_groups(
            rows,
            "checksum_sha256",
            transform=str.casefold,
        ),
    }


def build_metrics(
    rows: Sequence[Mapping[str, str]], issues: Sequence[ValidationIssue]
) -> dict[str, object]:
    """Calculate compact, decision-oriented catalog metrics."""

    total = len(rows)
    rows_with_issues = {issue.row_number for issue in issues if issue.row_number >= 2}
    valid_records = total - len(rows_with_issues)

    coverage_counts = {
        "source_reference": sum(bool(row.get("source_reference", "")) for row in rows),
        "checksum_sha256": sum(bool(row.get("checksum_sha256", "")) for row in rows),
        "last_reviewed": sum(bool(row.get("last_reviewed", "")) for row in rows),
    }
    coverage = {
        field: {"count": count, "rate": _rate(count, total)}
        for field, count in coverage_counts.items()
    }

    provenance_counts = {
        status: sum(row.get("provenance_status") == status for row in rows)
        for status in ("verified", "partial", "unknown")
    }
    documented = provenance_counts["verified"] + provenance_counts["partial"]

    scored: list[tuple[str, float]] = []
    for row in rows:
        try:
            score = float(row.get("confidence", ""))
        except ValueError:
            continue
        if 0.0 <= score <= 1.0:
            scored.append((row.get("asset_id", ""), score))

    confidence_counts = {
        "high": sum(score >= HIGH_CONFIDENCE_MIN for _, score in scored),
        "medium": sum(
            MEDIUM_CONFIDENCE_MIN <= score < HIGH_CONFIDENCE_MIN
            for _, score in scored
        ),
        "low": sum(score < MEDIUM_CONFIDENCE_MIN for _, score in scored),
    }
    review_queue = [
        {"asset_id": asset_id, "confidence": score}
        for asset_id, score in sorted(scored, key=lambda item: (item[1], item[0]))
        if score < MEDIUM_CONFIDENCE_MIN
    ]

    return {
        "records": {
            "total": total,
            "valid": valid_records,
            "valid_rate": _rate(valid_records, total),
        },
        "coverage": coverage,
        "provenance": {
            "counts": provenance_counts,
            "documented_count": documented,
            "documented_rate": _rate(documented, total),
            "verified_rate": _rate(provenance_counts["verified"], total),
        },
        "confidence": {
            "thresholds": {
                "high_min": HIGH_CONFIDENCE_MIN,
                "medium_min": MEDIUM_CONFIDENCE_MIN,
            },
            "scored_count": len(scored),
            "mean": round(fmean(score for _, score in scored), 3) if scored else None,
            "counts": confidence_counts,
            "review_queue": review_queue,
        },
    }


def audit_catalog(path: Path) -> dict[str, object]:
    """Run the complete, read-only audit for one CSV catalog."""

    rows, schema_issues = read_catalog(path)
    issues = schema_issues + validate_rows(rows)
    duplicates = duplicate_candidates(rows)

    return {
        "catalog": path.name,
        "schema": {
            "valid": not any(issue.row_number == 1 for issue in issues),
            "expected_columns": list(EXPECTED_COLUMNS),
        },
        "metrics": build_metrics(rows, issues),
        "duplicate_candidates": {
            name: [asdict(group) for group in groups]
            for name, groups in duplicates.items()
        },
        "validation": {
            "issue_count": len(issues),
            "issues": [asdict(issue) for issue in issues],
        },
    }


def render_text(report: Mapping[str, object]) -> str:
    """Render a concise stakeholder-facing summary."""

    metrics = report["metrics"]
    records = metrics["records"]
    coverage = metrics["coverage"]
    provenance = metrics["provenance"]
    confidence = metrics["confidence"]
    counts = confidence["counts"]
    duplicate_counts = {
        name: len(groups)
        for name, groups in report["duplicate_candidates"].items()
    }

    return "\n".join(
        (
            f"Catalog audit: {report['catalog']}",
            (
                f"Records: {records['total']} total; {records['valid']} valid "
                f"({records['valid_rate']:.1%})"
            ),
            f"Schema: {'valid' if report['schema']['valid'] else 'invalid'}",
            (
                "Provenance documented: "
                f"{provenance['documented_count']}/{records['total']} "
                f"({provenance['documented_rate']:.1%}); "
                f"verified {provenance['counts']['verified']}, "
                f"partial {provenance['counts']['partial']}, "
                f"unknown {provenance['counts']['unknown']}"
            ),
            (
                "Coverage: source reference "
                f"{coverage['source_reference']['rate']:.1%}; SHA-256 "
                f"{coverage['checksum_sha256']['rate']:.1%}; review date "
                f"{coverage['last_reviewed']['rate']:.1%}"
            ),
            (
                f"Confidence: mean {confidence['mean']}; high {counts['high']}, "
                f"medium {counts['medium']}, low {counts['low']}; "
                f"review queue {len(confidence['review_queue'])}"
            ),
            (
                "Duplicate candidate groups: exact name "
                f"{duplicate_counts['exact_name']}; normalized name "
                f"{duplicate_counts['normalized_name']}; exact checksum "
                f"{duplicate_counts['exact_checksum']}"
            ),
            f"Validation issues: {report['validation']['issue_count']}",
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit a digital-asset catalog without changing it."
    )
    parser.add_argument("catalog", type=Path, help="CSV catalog to audit")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = audit_catalog(args.catalog)
    except (OSError, csv.Error) as exc:
        print(f"Unable to audit catalog: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 1 if report["validation"]["issue_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
