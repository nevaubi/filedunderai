# Financial and spreadsheet integrity

This change follows the 2026-11-05 source audit. It preserves original source files and distinguishes successful extraction, validated observations, and published coverage. An extracted workbook is not a certification of its financial interpretation.

## Source audit

All 227 archived XLS/XLSX originals across 32 companies were downloaded and checked against their stored byte counts and SHA-256 hashes. Independent parsing covered 2,044 worksheets in the 207 readable originals. The other 20 are corrupt CW ownership filings and remain held.

The independent inventory contains 470,704 recorded cell nodes: 465,976 nonempty cached values, 4,725 empty or whitespace values, and three formulas without cached results. Complete extraction preserves every nonempty coordinate, including 1,650 populated cells beyond the old row limit in ten COP workbooks. The source workbooks themselves contain 1,198 error cells. Those errors and missing caches remain explicit; they are not replaced by calculated guesses.

The staged refresh admits 204 documents and holds 23. Eighty-eight documents preserve all existing populated values exactly. A further 116 have separate evidence for 27,591 source-precision restorations: each old extracted number equals the original rounded to six decimals, while the replacement equals the independently read original. These changes affect extracted source text, not financial facts or KPI observations. Three JNJ workbooks remain held because six differences would change an existing zero into a tiny nonzero cached value.

The 53,917 pre-audit workbook-backed KPI observations were separately compared with their original anchors. Numeric agreement alone does not establish the correct unit, period, or metric. Sixteen initially flagged Citi observations use documented trillions-to-dollars scaling; 23 Apollo outflows follow an explicit positive-outflow convention. Another 462 small numeric differences remain unchanged pending precision/context review.

## Extraction and source display

Workbook extraction retains coordinates beyond the old per-sheet rectangle, within the existing global cell, sheet, archive and deadline limits. Actual OOXML data bounds prevent formatting-only tails from exhausting that budget. The larger archive-member allowance is restricted to recognized drawing XML; worksheet/shared-string and total-archive limits remain enforced.

Cell metadata records number formats, source errors, formula text, and whether a cached formula result exists. Sheet metadata records original dimensions and visibility. XLS exposes cached values and formats; formula-definition inspection is explicitly unavailable for that legacy format. The viewer exposes error/cache counts and source metadata without inventing values or silently applying format conversions.

KPI validation rejects invalid A1 addresses, booleans, nonfinite values, unsupported scales, and uncached formula results. True Excel percentage formats establish a factor of 100; quoted/escaped percent suffixes retain their literal percentage-point values. A supplied scale cannot override that source evidence.

Year-to-date KPI values have separate cumulative tables. Published fiscal labels and workbook text paths survive publication and source navigation. Dollars per barrel retain cents and an explicit unit. Valid sixteen-week fiscal quarters can contribute to a four-quarter USD total under the existing continuity and full-year guards.

## Partial additions

Use the explicitly additive mode for a reviewed partial extraction:

```sh
python -m etl.kpitools validate /path/to/reviewed-additions.json
python -m etl.kpitools publish-additive /path/to/reviewed-additions.json
```

The additive publisher validates the entire submitted candidate, requires explicit compatible units, checks conflicts under the existing aggregate lease, inserts new keys without upsert, and verifies existing rows remain unchanged. It preserves unrelated public content and publishes only the submitted validated keys. Unrelated database-only observations are not automatically republished.

The original `publish` mode remains a whole-company replacement path and must not be used for a partial gap-fill batch.

## Bounded workbook refresh

`etl.repair_workbooks` stages remote reads for explicit document IDs and expected metadata. Apply requires the exact sealed plan, exact eligible IDs, and any separately sealed precision evidence.

```sh
python -m etl.repair_workbooks stage \
  --snapshot /path/to/document-snapshot.json \
  --root /path/to/stage \
  --originals /path/to/hash-verified-originals \
  --ids DOCUMENT_ID

python -m etl.repair_workbooks apply \
  --plan /path/to/stage/plan.json \
  --precision-evidence /path/to/stage/precision/approval.json \
  --ids DOCUMENT_ID
```

The default remains strict populated-cell containment. Precision evidence cannot approve a missing coordinate, a source mismatch, an arbitrary scale/sign change, or a change in zero status. Apply backs up the plan, prior checkpoint/manifest, precision evidence and independent source snapshots to immutable paths before pointer updates. New text paths contain their full content hash. Originals and old text objects remain intact.

Each company uses its existing IR lease. Only selected document text pointers, extraction timestamps/page counts, selected checkpoint outcomes/tasks, and that company's manifest/coverage change. Other document records and tasks stay identical. A durable intent ties deterministic before/after states to the plan, permitting an exact same-plan retry after a partial pointer update or checkpoint-before-manifest interruption. Unrelated drift requires a new review.

## Exact KPI corrections

`etl.repair_kpi_evidence` separates source-backed planning, transaction rendering, and public finalization:

```sh
python -m etl.repair_kpi_evidence stage /path/to/reviewed-manifest.json --out /path/to/stage.json
python -m etl.repair_kpi_evidence sql /path/to/stage.json \
  --lease-owner ACTUAL_HELD_OWNER --out /path/to/reviewed-transaction.sql
```

The same live `financial-aggregates` lease must span preflight, immutable evidence archiving, separately reviewed SQL execution, and finalization. Offline test-owner SQL is intentionally unusable in production.

The transaction checks complete exact before rows and definitions, source metadata, the immutable archive, destination collisions and exact affected counts. Definition-unit changes require verification of every historical row under that metric. Only explicit value/unit/period/label/quote/timestamp changes are allowed; source identities and cell coordinates cannot move. Short lock timeouts refuse concurrent conflicts. The exact after-state is checked before commit.

Database and object storage are not one transaction. Finalization requires the committed reviewed after-state and a public object equal to the reviewed before or after bytes; it preserves unrelated content and can safely resume. Rollback SQL/public restoration use the same exact-state protections. The utility refuses affected standardized numeric series or mapped-unit changes pending a separate mapping repair. Unaffected standardized files remain checksum guarded.

The audited correction scopes are 35 MPC dollar-per-barrel observations, 174 DIS fiscal-date moves including two independently proved treasury-stock unit labels, 84 MOS gross-margin unit/scale corrections, and three EXR counts parsed from footnote markers. MOS period keys are retained, not claimed independently re-audited. Each scope has its own complete source evidence and before/after record; it does not authorize similar-looking changes elsewhere.

## Financial and publication safeguards

Scheduled normalization now applies supported-period validation consistently and preflights unsafe replacements of directly reported facts. An equal later derived result preserves the newest matching direct observation, without hiding an intervening reported revision. Older, same-date changed, or reported-to-derived replacements require review. A failed fact write cannot advertise a successful new company bundle.

These guards do not make an ordinary full company refresh an additive history merge. A bounded repair must still inspect the entire proposed public difference. No broad historical financial normalization or legacy EPS/share deletion is authorized by this audit.

Standardized KPI model requests respect the remaining proposal budget and reserve time for publication. Runs with outstanding work are partial, and a no-work publication does not advance timestamps. These are scheduling/reporting safeguards; existing canonical mappings still require source-level review.

## Verification

The frozen implementation passed 405 Python financial tests, 227 JavaScript tests, 1,698 technical-parity event comparisons, and the Node 22 production build (35 fingerprinted assets). The exact four correction transactions were also executed against disposable PostgreSQL fixtures with their captured before rows: apply, rollback, stale-row refusal, missing lease/archive refusal, unrelated-row preservation, and Disney destination-collision refusal passed. Independent review covered original tables, workbook precision deltas, preservation and interrupted-write recovery.
