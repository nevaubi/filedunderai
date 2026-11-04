# Financial pipeline implementation report

Implemented in the existing feature worktree. No commit, push, production ETL, database mutation, credential access, model call, or live bundle repair was performed.

## Changes and evidence

- `etl/sec.py` preserves concept identity until selection. Revenue selection uses explicit accounting scope: reported consolidated `Revenues` is selected when the same filing and period includes lease income or insurance premiums; `RevenuesNetOfInterestExpense` retains bank semantics. Ordinary industrial ASC 606 revenue remains supported. No maximum-value selection or unconditional alias reorder is used. Source filing tables use the same rule.
- AMT's actual public filing was read successfully via an ordinary read-only HTTP request after the web retrieval service returned an error. `https://americantower.gcs-web.com/node/29311/html` contains dimensionless context `c-27`, 2026-07-01 through 2026-09-30. In this same context, `us-gaap:Revenues` is 2,785.4 million; `RevenueFromContractWithCustomerExcludingAssessedTax` is 202.5 million; `OperatingLeaseLeaseIncome` is 2,582.9 million. Extracted facts, context XML, source URL, and SHA256 of fetched source bytes are in `tests/financials/fixtures/amt_revenue_source.json`. The AMT regression reads this fixture.
- EPS basic/diluted and weighted diluted shares are nonadditive during quarter derivation. Direct reported quarters survive. Legacy derived EPS is excluded by screener and TTM paths.
- TTM accepts exactly four consecutive duration quarters with valid start/end dates. It rejects balances and weighted share sums. FCF requires aligned OCF/capex intervals and treats explicit zero as zero, missing as unknown. Net margin also requires aligned income and revenue periods.
- Normalization defaults to latest available vintage, accepts `vintage='original'` and `as_of='YYYY-MM-DD'`, and preserves reported/derived filing histories. Derived start is the next day after the smaller cumulative end. Every contribution is recorded, with accession, concept, filing date, and period. Same-filing derivation is preferred. Cross-filing subtraction requires the same concept and no observed subsequently conflicting smaller leg; incompatible current derivations are omitted while their original historical observations remain available.
- Share counts select one filing/context, deduplicate repeated observations, and return unknown when differing values cannot be resolved to classes. Index, company fundamentals, and screener use the same reported-outstanding market-cap function. Weighted diluted shares no longer silently replace exact cover counts. Historical valuation retains explicitly described weighted-share proxy semantics and now resolves all facts, including balances, at each historical cutoff.
- Screener extraction no longer mutates global SEC concept mappings in worker threads. Cache version increments from 4 to 5 to rebuild changed semantics and histories.
- `etl/common.py` adds renewable distributed lease support with fail-closed behavior if the additive RPC migration is unavailable. Companies/prices/index/screener aggregate publishers share the lease. Nested index publication reuses its parent's lease. Writes check lease validity; the heartbeat renews every 100 seconds on a 300-second lease. TTL is capped at 600 seconds in Python, matching the SQL migration. Root owns the matching SQL migration.
- Aggregate reads required for safe replacement propagate errors rather than turn a temporary failed read into an empty cache. Missing fetched symbols preserve their previous market and price-stat entries. All bars in the fetched price history are upserted into `prices_daily`, rather than just the last seven, so adjusted-history revisions reach the database.
- `Run` derives `ok`, `partial`, `skipped`, or `error` from failures/skips, including nested error counts. Company optional enrichment failures appear as partial health. Status publishing marks running entries older than `ETL_STALE_SECONDS` (default 7,200) as stale without pretending they completed.
- Each fetched companyfacts payload gains a content-addressed archive snapshot alongside the existing current raw path. Bundle metadata identifies the snapshot. Original inputs are not edited by normalization or preview repair.

## Shared data contract

Existing record keys `v`, `start`, `end`, `accn`, `filed`, `c`, `form`, and `derived.minus` remain compatible. Additions:

- Each statement row: `vintage`, `as_of`, and `filing_history: {annual: [...], quarterly: [...]}`.
- Each record: `accessions` (all contributing accessions), `available_at` (latest input filing date), `vintage`, `validation_status`, `selection_reason`.
- Derived records: `derived.from` and `derived.minus` contain complete input records; `derived.basis` distinguishes same-filing and sequential-filing derivation.
- A derived record can carry `superseded_at` if a subsequent incompatible restatement invalidates that derived basis. As-of consumers must exclude it when `superseded_at <= cutoff`; prior historical cutoffs retain it.
- The history contains the accounting-selected concept per period/accession, so a subsequent consumer cannot accidentally resurrect AMT's component alias.
- `profile.sharesOutstanding`: `val`, `end`, `accn`, `filed`, `available_at`, `form`, `basis`. `basis` is `reported_outstanding` or `ambiguous_share_classes`.
- Fundamentals/index entry: `shares_metadata`, retaining the legacy numeric `shares` field in the fundamentals cache for compatibility.
- Screener compact source arrays retain their first eight positions; position eight adds provenance metadata. Existing first ten positions of `Prov.refs` remain unchanged, followed by metadata.

## Offline repair/rebuild preview

```bash
python -m etl.repair \
  --bundle /path/to/public-company.json \
  --companyfacts /path/to/archived-companyfacts.json.gz \
  --output /tmp/company-review
```

Optional: `--prices` (price stats or public daily price bundle), `--vintage original`, `--as-of YYYY-MM-DD`.

Produces `candidate-company.json` and `review.json` with period-level before/after values, concepts/accessions, computed fundamentals, share changes, source-file SHA256 hashes, and limitations. Existing outputs are not overwritten. No network/API/storage/database client is called. Public-only input is supported by recovering reported observations, but the review explicitly warns that missing concepts, original vintages, and share-class identity cannot be reconstructed from already reduced bundles. Archived companyfacts is the preferred input. Publishing remains a separate reviewed step owned by root.

## Verification

Initial focused regression run before implementation: **10 tests, 9 failures**. Failures reproduced AMT component revenue, bank component revenue, off-by-one quarter start, cumulative EPS subtraction, unaligned FCF, missing capex default, duplicated share summation, invalid TTM sums, and earliest-filing selection. Ordinary industrial revenue passed.

A later pipeline-test import initially failed because this runtime lacked `bs4`. Installed the repository's already pinned `requirements.txt`; no dependency manifest changed.

Final commands and results:

```text
python -m unittest discover -s tests/financials -v
Ran 23 tests in 0.016s
OK

python tests/technicals/test_parity.py
series 0: 600 bars, 74 events identical (12 types)
series 1: 3000 bars, 443 events identical (14 types)
series 2: 4700 bars, 810 events identical (16 types)
series 3: 2500 bars, 371 events identical (14 types)
parity ok, 1698 events

python -m compileall -q etl
(exit 0, no output)

node --test tests/technicals/indicators.test.cjs
tests 17
pass 17
fail 0
cancelled 0
skipped 0

 git diff --check -- etl tests/financials
(exit 0, no output)
```

Tests cover live-source AMT fixture, industrial/bank/insurer scopes, original/latest/as-of vintages, derived provenance and invalidation, EPS, missing/zero/aligned capex, nonadditive TTM rejection, share duplication/ambiguity, exact/proxy market cap, screener as-of history, health/stale detection, lease exclusion/failure, preserved failed symbols, entire fetched price revisions, strict cache reads, and repair no-network/input preservation.

## Modified/added files

- `etl/sec.py`
- `etl/fundamentals.py`
- `etl/screener.py`
- `etl/common.py`
- `etl/jobs.py`
- `etl/repair.py` (new)
- `tests/financials/test_financials.py` (new)
- `tests/financials/test_pipeline.py` (new)
- `tests/financials/fixtures/amt_revenue_source.json` (new)
- This report.

## Operational limits and release notes

1. Deploy the additive lease RPC migration before running aggregate jobs. Missing RPCs block publication explicitly. No live SQL verification was performed in this task. The lease serializes cooperating publishers; it does not provide a multi-object Storage transaction or fencing against already in-flight requests from a worker paused beyond its expiry. Lost/expired leases block subsequent guarded writes. Old deployed workers must be drained during rollout.
2. Full fetched-window price upserts increase database work. Existing job partitions/time budgets should be sized accordingly. History older than the fetched two-year window is unchanged; the change only guarantees synchronization of the fetched window.
3. Existing production bundles remain unmodified. Repair/rebuild is needed to populate histories, corrected starts/revenue/FCF, and `shares_metadata`. Old unlabelled share totals now yield unknown exact market cap rather than being trusted as validated counts. There is no silent weighted-share fallback.
4. Companyfacts does not reliably expose share-class dimensions. Ambiguous multi-class counts remain unknown, and no guessed Class A/Class B conversion is applied. Explicit dimension-aware total/class support is future work. Post-filing corporate actions need a consistent split/date policy before claiming exact current capitalization from old cover counts.
5. Date availability is daily filing-date resolution. An actual backtest should use subsequent trading-session execution and historical universe controls. Historical weighted-share valuation remains a proxy, identified in definitions; it is not exact class market capitalization.
6. Cross-filing same-concept derivation can establish observed consistency, not prove that all accounting-policy changes were disclosed in the retained facts. Such derivations are labeled distinctly and revoked if an incompatible smaller cumulative revision appears. Same-filing inputs provide stronger evidence. Unsupported or incompatible observations remain unknown.
7. Public-only repair cannot invent consolidated concepts missing from the old reduced data. The report makes that limitation explicit. The actual AMT concept relationship is verified; no authorized live repair has been performed here.

## Independent analyst engine review (read-only)

Reviewed root-owned `public/lib/analyst.js`, `tests/analyst/analyst.test.mjs`, and the additive monitoring/lease migration. No root-owned files were edited. Findings were delivered to root for fixes and regression tests:

1. **High: backtest events outside price coverage executed years late.** An event published 2020-05-01 with prices beginning 2026-01-02 generated a trade entering 2026-01-02. Require price coverage around publication and reject missing-session gaps; absence of price history cannot stand for delayed execution.
2. **High: FCF and margins joined mismatched duration windows.** Quarter OCF 100 (July–September) and nine-month capex 20 (January–September) produced FCF 80. Exact same-company start/end intervals are required; capex magnitude must use the same sign convention as ETL.
3. **High: old subtracted EPS leaked through analyst series.** A legacy EPS quarter derived as 4/3 − 1 remained visible as 1/3. Reject derived basic/diluted EPS and weighted share observations in the public analyst path.
4. **High: insufficient insider price coverage became a false clear.** A 252-session-high rule with only one price before a transaction returned `clear`. Return unknown when coverage is insufficient; context may show a limited-window value only with explicit coverage.
5. **High: withdrawn guidance was scored as an active promise.** Filtering withdrawal points before choosing the latest observation resurrected an old range and produced a 100% hit rate. The latest withdrawal must be preserved and excluded from scoring.
6. **High: an explicitly empty watchlist matched all companies.** `inScope({scope:'watchlist'}, {ticker:'NOT_WATCHED'}, [])` returned true. An empty scope must match none; omitted context can be handled separately if needed.
7. **Medium: default current reports lacked a publication cutoff.** Explicit as-of filtering works, including `superseded_at`, but undefined as-of accepted future-filed values while the report described its as-of as today. Apply today's cutoff consistently to ordinary and extra series.
8. **Medium: duration comparability and reaction horizon.** Growth checked units and end offsets but not duration compatibility, allowing partial transition periods into normal comparisons. The event-study horizon 1 exited the second trading session after entry, despite its one-session earnings-reaction use; clarify or fix the session convention and its test.

Root acknowledged the first four and is implementing regressions; the remaining findings were also sent immediately. Recheck root's final analyst tests before delivery. The existing explicit `superseded_at <= asOf` exclusion is correct. Revenue anchoring gives one coherent report period, but mismatched/missing metrics should remain unknown and older coverage must remain visibly dated.

Migration review: lease RPC argument names and boolean responses match the Python client. Acquiring/renewing an owned lock and rejecting a competing owner are covered by root's local PostgreSQL/PGlite test. Table RLS and explicit function EXECUTE revocations prevent anon/authenticated lease access. No blocker found in the lease SQL. Adjusted Python TTL cap from 900 to 600 seconds to match the SQL validation. No live migration was run.

Independent local migration verification completed:

```text
node --test tests/analyst/schema.test.mjs
monitoring migration enforces owner isolation, column permissions and service-only leases: passed
tests 1; pass 1; fail 0
```

The TTL-cap alignment was followed by `python -m unittest discover -s tests/financials -q`: 23 tests, OK.
