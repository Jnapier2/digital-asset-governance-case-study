# Digital Asset Governance Audit

[![CI](https://github.com/Jnapier2/digital-asset-governance-case-study/actions/workflows/ci.yml/badge.svg)](https://github.com/Jnapier2/digital-asset-governance-case-study/actions/workflows/ci.yml)

Digital Asset Governance Audit turns catalog-quality questions into reviewable evidence. The deterministic, read-only Python tool reports schema health, provenance coverage, confidence distribution, and duplicate candidates, then produces a human review queue without changing source records. It uses only the Python standard library and includes a synthetic fixture.

## Fixture results

Running the included fixture produces these deterministic sample results:

- **8 of 8 records pass schema and value validation.**
- **75% have documented provenance** (`verified` or `partial`); 50% are fully verified.
- **100% have source-reference and review-date coverage; 75% have SHA-256 coverage.**
- **Mean confidence is 0.74**, with four high-, two medium-, and two low-confidence records.
- The review queue contains **one exact-name group, two normalized-name groups, and one exact-checksum group**.

These are fixture results, not claims about a production catalog. The sample includes incomplete provenance, missing checksums, and duplicate candidates so each audit path has deterministic coverage.

## Audit questions

Asset inventories can combine authoritative records with partial imports, inconsistent filenames, and uncertain provenance. The audit answers three questions: which records satisfy the schema, which fields have evidence coverage, and which records require duplicate review.

The pipeline is read-only:

```mermaid
flowchart LR
    A["Synthetic CSV catalog"] --> B["Schema and value validation"]
    A --> C["Coverage, provenance, and confidence metrics"]
    A --> D["Exact-name, normalized-name, and checksum matching"]
    B --> E["Text or JSON audit report"]
    C --> E
    D --> E
    E --> F["Human stewardship review"]
```

The tool never changes the catalog. Duplicate matches are candidates for review, not automatic deletion decisions.

Catalog validity and stewardship priority are intentionally separate: low-confidence rows and duplicate candidates can enter review without converting an otherwise valid dataset into a failed run. This keeps automation useful while leaving uncertain identity decisions with a person.

## Run it

Python 3.11 or newer is recommended; there are no third-party runtime dependencies.

```powershell
python catalog_audit.py tests/fixtures/synthetic_catalog.csv
python catalog_audit.py tests/fixtures/synthetic_catalog.csv --format json
python -m unittest discover -s tests -v
```

The command exits with `0` when validation passes, `1` when schema or row validation fails, and `2` when the file cannot be audited. Low confidence and duplicate candidates remain review signals and do not make an otherwise valid catalog fail.

## Audit model

The CSV contract requires:

| Area | Fields and rules |
|---|---|
| Identity | Unique safe `asset_id`; label-only `artifact_name` with no path |
| Type and source | Controlled `asset_type` and `source_system`; opaque safe `source_reference` |
| Stewardship | Role-based `owner_role`; controlled `classification`; ISO review date |
| Provenance | `verified`, `partial`, or `unknown` |
| Confidence | Numeric score from 0 through 1; high is `>= 0.85`, medium is `>= 0.60`, lower scores enter review |
| Integrity | Optional 64-character SHA-256 digest |

Normalization applies Unicode NFKC, case folding, extension removal, punctuation-to-space conversion, and whitespace collapsing. It deliberately preserves version numbers and dates to limit false positives.

## Data boundary

The included dataset is a synthetic fixture. It contains no live catalog rows or operational identifiers and covers only the fields required to exercise validation, metrics, and match generation.

## Project structure

```text
catalog_audit.py                 # Audit library and command-line interface
tests/test_catalog_audit.py      # Validation, metric, and matching tests
tests/fixtures/synthetic_catalog.csv
.github/workflows/ci.yml         # Compile, test, and fixture audit
```

## Design limits

- Name matching is deterministic and conservative; it is not fuzzy or semantic entity resolution.
- Checksum matching can only assess records that already have checksums.
- The stated confidence thresholds are specific to this audit model, not universal DAM standards.
- The tool audits one CSV snapshot and does not connect to a live provider or write remediations.

## License

Copyright © 2026 Gateway Information Group LLC. All rights reserved. See [LICENSE.md](LICENSE.md).
