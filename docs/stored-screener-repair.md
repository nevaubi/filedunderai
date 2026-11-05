# Rebuild the screener from stored inputs

`etl.repair_screener` prepares a local candidate and publishes it only through a
separate command requiring the exact reviewed manifest SHA-256. It keeps the
active screener universe (currently 800 companies); it does not activate the
additional 300 companies in the company directory.

The ordinary `etl.run screener` path is unsuitable for a provider-free repair:
`update_closes` requests Yahoo data for the dollar index and can request history
for missing or adjusted symbols. This command instead freezes the complete
stored close-history cache, including market/sector/factor symbols. It recomputes
all existing fields, correlations, peers and provenance from that history.

## Prepare and review

Use a long-lived worker, with the repository dependencies installed. The prior
800-company screener run took approximately 15 minutes. Preparation and later
publication each perform substantial storage reads; the five-minute Vercel
function budget is inappropriate. Read/write concurrency is capped at eight.

```bash
python -m etl.repair_screener --env-json /private/backend-env.json prepare \
  --out /private/screener-candidate --workers 8
```

The JSON environment file needs only `SUPABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY`; values are never printed. Alternatively, supply
those environment variables directly and omit `--env-json`.

Preparation permits authenticated storage reads, reads of existing filings,
news, insider transactions and macro observations, and read-only storage-list
requests for KPI/segment objects. It blocks provider requests, remote writes,
lease acquisition and `etl_runs` logging. All proposed storage writes and
`company_metrics` rows are captured under the output directory.

Financial cache version 6 is rebuilt from the stored company bundles and stored
Company Facts archives, even if the existing cache is warm. Legacy bundles have
no financial cache entry. With the November 5 snapshot, this means 366 eligible
active companies and 434 withheld legacy financial entries. Existing technical,
activity and KPI columns remain in the screener. Null financial values are not
filled from the old cache.

The normalized active entries' derived `fund` summaries and the two top-level
`div_ttm`/`bb_ttm` distribution inputs are also recomputed against the same current
quarter from their sealed company statement bundles. The metadata snapshot retains every
other identity, coverage, share-count and version field, including all staged
and legacy entries. `fundamental_changes` records the exact before/after value
and field presence for each change. The current reporting window must have four
consecutive reported EPS quarters; Honeywell's mixed corporate-action basis
remains explicitly held until recast evidence is established. Reported
historical statements are preserved.

Review `manifest.json`, the proposed public entry points and provenance before
publication. The manifest must report `ready_for_publication_review: true`:

- Exactly the active tickers, with no duplicates and all original columns.
- Exact close/date parity across stored history, current index and merged price
  statistics; serialized candidate source prices must match its index.
- Financial cache membership equals the normalized active cohort.
- Market capitalization, P/E, P/S, FCF/dividend/buyback yields, latest-quarter
  revenue growth and trailing net margin agree with the index after screener
  rounding. Gross and operating margins use different documented windows in
  the two products and are not compared as equivalent fields.
- Every referenced proof is present, verified and assigned to the same issuer.
- Calculation code hashes match at preparation start and finish.

Preparation records each input/output digest and one `aggregation_id` on both
public entry points. It does not claim that stored quotes are live.

If code changes during preparation, replay a completed capture locally. Replay
uses captured object bytes, relation rows and the captured KPI cache stamps;
it makes no network requests and needs no credentials:

```bash
python -m etl.repair_screener prepare \
  --replay-inputs /private/earlier-capture \
  --out /private/reviewed-candidate --workers 8
```

The replay manifest identifies the original capture and records zero network
reads. Publication still validates live financial and price inputs.

## Publish the reviewed candidate

After review, use the literal SHA-256 printed by preparation. Do not calculate
and approve an unreviewed manifest in the same command.

```bash
python -m etl.repair_screener --env-json /private/backend-env.json apply \
  --manifest /private/reviewed-candidate/manifest.json \
  --reviewed-sha256 REVIEWED_64_CHARACTER_SHA256 --workers 8
```

Apply takes the existing renewable `financial-aggregates` lease and fails if it
is busy. Under that lease it validates current normalized metadata, each used
company/source archive, stored close history, price-stat inputs and index price
parity. The complete original fundamentals object must still match byte-for-byte,
because the corrected snapshot replaces that whole object. It rechecks the mutable aggregate snapshots
after making the backup and immediately before publication.

All overwritten object bytes and their prior absence, complete prior
`company_metrics` rows and absent row keys, and intended after-state digests are
saved in a private compressed backup and read back before any public replacement.
An existing immutable proof with different content aborts publication. Writes
are single-attempt and bounded by the remaining lease lifetime.

The intended writes are limited to:

| Location | Change |
|---|---|
| `raw/repairs/screener/{manifest-sha}/backup.json.gz` | Verified private before/after backup |
| `public-data/sources/screener/{ticker}/{source-sha}.json` | Missing immutable proofs; identical existing proofs are verified |
| `public-data/screener/src/{ticker}.json` | Per-field provenance for the active universe |
| `raw/screener/fund.json.gz` | Version 6 normalized financial cache |
| `raw/screener/kpi.json.gz` | Stored KPI cache, when its recomputation produced a change |
| `public-data/meta/fundamentals.json` | Source-recomputed normalized `fund`, `div_ttm` and `bb_ttm`; every other field preserved |
| `public-data/screener/corr.json` | Correlation matrix |
| `company_metrics` | Upsert the active companies only |
| `public-data/index.json` | Guarded index using the pinned fundamentals/price statistics |
| `public-data/screener.json` | Main screener, published last |

All proofs are verified before mutable source files are published. The caches
and correlation matrix follow, then relational metrics, index and main screener.
Every artifact is read back; metrics are checked by ticker, field and value.
Objects are individually replaced, so there is a temporary cross-object
visibility window. The shared generation identifier permits detecting skew;
this is not a transaction across storage and Postgres.

This repair does not change facts, company bundles, raw SEC sources, price
history, the directory, provider data or `etl_runs`. The atomic local
`apply-receipt.json` is its operational record. It records the private recovery
pointer before the first backup write and preserves progress after interruption.
An existing backup blocks a blind repeat of apply.

## Recover a partial or completed publication

Review the apply receipt and use its exact backup path and compressed backup
SHA-256. Rollback is a separate leased command:

```bash
python -m etl.repair_screener --env-json /private/backend-env.json rollback \
  --backup repairs/screener/MANIFEST_SHA/backup.json.gz \
  --reviewed-backup-sha256 REVIEWED_BACKUP_SHA256 \
  --receipt /private/screener-rollback-receipt.json --workers 8
```

Rollback verifies the complete backup before writing. Each current mutable
artifact and metrics row must equal its recorded before or intended after state;
an unrelated later change aborts recovery. It restores prior bytes, removes
mutable artifacts/rows that were previously absent, and verifies readback.
Immutable proofs may remain as harmless unreferenced source objects. Index and
main screener are restored last. The rollback receipt is also written atomically,
and an interrupted rollback can be retried while the same conflict checks hold.

## Verification

```bash
python -m unittest discover -s tests/financials -p test_screener_repair.py
npm test
python -m unittest discover -s tests/financials -p 'test_*.py'
```

The focused tests exercise changed source bytes, altered local artifacts,
unreviewed manifests, output scope, missing proofs, price/date disagreement,
provider/mutation blocking, lost leases, backup-time drift, immutable-proof
collisions and conflict-checked rollback including prior absence.
