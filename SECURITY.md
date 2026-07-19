# Security Policy

## Supported version

Security fixes are applied to the current `main` branch. This portfolio case study has no deployed service and does not connect to a live content provider.

## Report a vulnerability

Please use the repository's **Security** tab to submit a private security advisory. Include the affected file, a minimal reproduction, the potential impact, and any suggested remediation. Do not disclose a suspected vulnerability in a public issue before it has been reviewed.

## Data safety

The committed fixture is entirely synthetic. Do not add exports from live systems, personal identifiers, provider-specific IDs, credentials, permissions, local paths, or confidential filenames to issues, tests, fixtures, or pull requests.

The audit is read-only, but its report repeats selected catalog labels and identifiers. Treat reports created from non-synthetic inputs according to the source data's classification and do not commit them by default.

## Scope notes

- Duplicate candidates require human review; the tool performs no deletion or merge.
- CSV formula evaluation is out of scope because the tool parses values as text and does not open them in a spreadsheet engine.
- Availability or policy defects in external data sources are out of scope because this project has no external integration.
