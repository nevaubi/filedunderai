# Financial and spreadsheet integrity audit

## Scope and source of authority

The user requested a fresh, thorough audit of backfill accuracy and financial/KPI gaps, including all available Excel and spreadsheet data. Changes must address demonstrated defects or source-verified omissions. Preserve correct observations and original evidence. Do not resume the earlier bulk normalization deletion plan.

Baseline: `f97acd5e665ee2f73f61baa07694730a3994d020`. Review branch: `audit/spreadsheet-financial-integrity-2026-11-05`.

## Evidence and implementation sequence

1. Reconcile current repository, database and public bundles. Inventory every archived XLS/XLSX file. Download originals read-only and check every byte count and SHA-256 against the database before parsing. Enumerate every worksheet, populated cell, cached formula, explicit error and existing extraction truncation.
2. Inspect the supplied source-registry workbooks and CSV files. Preserve discovery/verification status and distinguish official source pointers, actual results, analyst forecasts and excluded sources. A URL inventory is not a numerical time series.
3. Reproduce spreadsheet validation failures before fixing them: scale bypass, accidental percentage conversion, invalid cell addresses, booleans accepted as numbers, CSV numeric values and formula cells without cached values. Preserve formula, cell and format evidence. Add a publication mode that preserves all existing KPI records when a partial gap-fill batch is supplied and refuses conflicts.
4. Reproduce publication/display gaps before fixing them: omitted year-to-date groups, missing workbook text paths, percent formatting and valid 16-week fiscal quarters. Keep period kinds and source fiscal labels distinct.
5. Apply the same source-period and reported-observation safeguards to scheduled financial normalization as to reviewed repair. Unsupported source observations remain inspectable; conflicting reported-to-derived replacements require review.
6. Correct the demonstrable hard-coded first-cohort restriction in the IR backfill publisher. Keep per-issuer identity, source hash and mixed-cohort protections.
7. Build gap-fill candidates only from verified original cells and explicit period/unit/metric context. Check for existing equivalent observations and conflicts before any production mutation. Record candidates rejected or held for missing context. Preserve original and before-state evidence and verify exact readbacks for any bounded authorized writes.
8. Run the repository's financial and frontend regression suites and build. Review combined changes independently before publication. Record what changed, what was actually integrated and every remaining coverage limitation.

## Work ownership

- Financial ingestion: `etl/jobs.py`, `etl/sec.py` and financial regression tests.
- Spreadsheet extraction and KPI validation/publication: `etl/ir.py`, `etl/kpitools.py` and corresponding tests.
- Public KPI display and standardized source paths: `etl/kpistd.py`, public UI modules and corresponding tests.
- Root: source/workbook inventory, cohort publisher, integration checks and final evidence report.

## Completion evidence

The report must distinguish original documents archived, text extracted, exact numeric cells verified, database observations stored, public company tables published, standardized peer series published and historical completeness. Counts alone do not establish correctness. No claim of full financial/KPI coverage without an exhaustive source-to-observation reconciliation.
