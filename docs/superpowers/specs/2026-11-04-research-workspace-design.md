# Filed Under research accuracy and workspace design

## Intent

Implement the user's requested data corrections, professional UI, evidence-backed assistant, Office-like research editors, watchlist monitoring, earnings changes, standing orders, thesis tracking, and evidence-based idea generation. Preserve the existing vanilla JavaScript/D3 application and public research data. API-account troubleshooting and general access-policy redesign are deferred at the user's request.

## Decisions

- Extend the existing application with focused modules rather than change frameworks. This preserves established charts, source drawers, routes, and local research.
- Correct financial definitions before deriving new signals. Unknown values remain unknown; flows, balances, per-share metrics, ratios, filing vintages, and estimates have explicit semantics.
- A structured evidence record contains stable identity, company/metric, exact numeric value and unit, period start/end, filing accession/date, concept, derivation inputs, availability date, and validation status. Citation rendering resolves the record, never a mutable latest lookup.
- New analysis uses deterministic calculations with model interpretation on top. Forecasts are labeled assumptions; unavailable consensus is not invented. Backtests use availability cutoffs and next-session execution, with sample size and methodology disclosed. Do not imply historical constituent coverage or causal relationships not present in data.
- Research workspace uses Word/Excel-style ribbons, document page, formula bar and grid, selection-aware editing, formatting, history/undo, imports/exports, and preview/apply/reject for AI edits. Native Microsoft Office runtime and complete file-format parity are not assumed.
- Existing localStorage data is migrated without deletion. Core research remains usable without signing in. Durable monitoring/email uses verified account ownership, additive private tables, opt-in preferences and an outbox. No actual notification is sent during implementation.
- Use the live guidance/calls datasets where available without merging unrelated branches blindly. Coverage and source gaps remain visible.

## Feature acceptance

1. Consolidated AMT revenue resolves to 2,785.4 million for Q3 2026 rather than 202.5 million non-lease revenue; fixtures also protect ordinary industrial revenue and accounting-model differences.
2. TTM rejects instant balances and share-count sums; capex missing is distinct from zero; EPS subtraction is prohibited; latest/restated and as-of selection are explicit.
3. Citations work in tables and for ampersand metric names. Unknown citation IDs are visibly unresolved. Structured results remain valid and explicitly report truncation. Numerical claims can be checked against their records.
4. Earnings changes compare aligned periods and give every reported number an inspectable evidence record. Morning briefs summarize new filings/releases for the watchlist and do not pretend stale data is new.
5. Standing orders compile into reviewable deterministic rules. Insufficient data yields unknown, not a false pass. Thesis status is derived from explicit measurable checks and preserves user edits.
6. Signals, pairs and earnings setups show inputs, observation dates, sample sizes, conditions invalidating an idea, time horizon and source documents. Model forecasts are distinct from externally sourced expectations. Disclosure changes and guidance outcomes preserve their text/fact evidence; inferred exposure is labeled.
7. The worksheet supports multiple sheets, range selection/paste, formulas, recalculation, number formatting and undo. The document supports paragraph/text formatting, tables, headings, document layout, export and selection-aware AI proposals. No inert ribbon buttons or fabricated collaboration/persistence states.
8. Tests cover financial failures, evidence/citation integrity, rule semantics, model calculations, workbook/history, and email idempotency. Browser QA verifies actual interactions and responsive layout.

## Release

Work on an isolated feature branch; review and test before integrating. Remove implicit production DDL from application builds. Prepare additive monitoring migrations and verify ownership policies before use. Any delivery provider or deployment-access limitation is reported precisely while shipping the functional code that does not depend on it.
