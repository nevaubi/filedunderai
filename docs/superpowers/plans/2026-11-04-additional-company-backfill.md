# Additional Company Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prepare and publish300 additional US-listed companies with official source-backed financial values and independently tracked IR coverage.

**Architecture:** Keep immutable SEC source snapshots and exact per-value proofs. Separate approved issuer registration from activation, use additive per-company publication and preserve existing history. Extend membership semantics consistently before making broader coverage public.

**Tech Stack:** Existing Python ETL, Node/vanilla JavaScript, Supabase PostgreSQL/Storage, Vercel, native Tavily/Firecrawl tools.

**Spec:** `docs/superpowers/specs/2026-11-04-additional-company-backfill-design.md`

## Global Constraints

- Target300 additional unique CIKs outside the existing500; fund holdings establish selection only.
- Preserve existing records and explicit SP500/SP100 membership.
- No StockAnalysis authoritative sources or Tesla-specific KPI implementation.
- No automatic news or summary-model calls in the new-company facts batch.
- Archive exact official source bytes; missing/ambiguous values remain unpublished.
- No partial input to the legacy replacement KPI publisher; no full4M-row REST aggregate rebuild.
- Keep credentials out of artifacts/logs. Use only the confirmed existing Supabase project linked to Vercel.

## Review Focus

- A ticker alias or successor entity must not silently inherit another issuer's history.
- A quoted number must not pass with the wrong metric, reporting period, scale or currency.
- Previously saved SP500 screens must still select only the original index membership.
- A partial batch or acknowledgement failure must preserve prior data and resumable progress.
- Staged data must not be represented as published or as exhaustive IR history.

### Task1: Freeze cohort and registry

**Files:** `etl/universe.py`, new `etl/additional_companies.json`, focused registry tests; source evidence under the batch artifact directory.

**Interface:** Preserve existing4-tuples. Export explicit `SP500` membership plus approved additions. Keep pending additions inactive until read-back succeeds.

- [x] Obtain official holdings, SEC and exchange snapshots; resolve300 unique new issuers and record exclusions. Publication revision1 substitutes TREX for OZK after the official SEC Company Facts endpoint returned404; the original frozen cohort is preserved.
- [x] Add failing tests for duplicate ticker/CIK, original membership preservation and pending-versus-active additions.
- [x] Implement validated registry loading and explicit membership publication in `jobs.build_index`.

### Task2: Honest index and screener semantics

**Files:** `public/pages/screenspec.js`, `public/pages/screener.js`, `api/screen.js`, relevant `public/app.js` labels, screener/assistant tests; metadata producers `etl/screener.py`, `etl/technicals.py`, `etl/sectors.py`.

**Interface:** `all`, `sp500`, `sp100`; explicit `sp500` row metadata; compatible interpretation of legacy500-company bundles. Actual SPY benchmark labels remain unchanged.

- [x] Add failing tests proving new companies appear under all and are excluded from SP500/SP100; preserve saved explicit SP500 specs.
- [x] Update selectors, labels, shared validation and API schema together.
- [x] Keep S&P-labelled analytics restricted to actual members or label covered-universe statistics accurately.

### Task3: Strict SEC provenance and facts-only import

**Files:** new `etl/source_provenance.py` and tests; `etl/jobs.py`, `etl/run.py`, necessary source-fetch support.

**Interface:** `validate_statement_provenance(snapshot, statements, expected_cik, snapshot_sha256)` returns source-proof metadata or raises before publication. Explicit flags disable optional enrichment and unarchived gap fill.

- [x] Add failing source-proof tests for wrong issuer/concept/unit/period/value, missing source legs, invalid derivation arithmetic and source-record locators.
- [x] Validate exact reported records and recursively validate derived inputs; retain original fiscal/context fields.
- [x] Add facts-only mode, wire budgets, and test that no news/summary calls occur.
- [x] Stage immutable raw sources, bundles and relational rows with a resumable journal; distinguish staging from live publication.

### Task4: Bounded collection, publication and verification

**Files:** operator batch module/receipt and data artifacts; final verification document.

- [x] Fetch a representative source pilot, inspect source proofs and reporting contexts, then complete the approved 300-company collection.
- [x] Use Tavily/Firecrawl to discover and retrieve official IR sources; preserve raw provider output, original URLs, timestamps, checked files and explicit limitations.
- [x] Independently audit identities and all promoted financial proofs; reconcile every stored field in all 652,811 fact rows and verify preservation of existing records.
- [x] Publish core financial data, prices and IR documents through the confirmed existing Supabase project, with leases, source/object readbacks and resumable checkpoints.
- [x] Bootstrap exactly 800 IR coverage entries using scoped grouped counts; preserve the original 500 entries and KPI/segment count timestamps.
- [x] Merge and deploy the approved active registry while the price publisher holds the aggregate lease; preserve SP500=500 and the exact legacy SP100 set of 102 tickers.
- [x] Pass the release suites and production build: 193 Python tests, 186 JavaScript tests, 1,698 parity cases and 27 built assets.
- [x] Verify the production browser: 800/500/102 membership filters, OKTA search, exact FY2026 revenue/source, original IR release and correct price-return label; retain the screenshot and verified receipt.
- [x] Finish the feed/final-verification tail after the execution-service interruption, retaining every original gate and the checkpoint/recovery hash links; complete the serialized cutover and final 800-company/source readbacks; verify 498 preserved existing fund entries, sourced ABT/BLK filing updates and unchanged legacy BK metadata in the successful activation receipt.
- [x] Publish and read back the content-addressed final audit archive, then record its manifest hash and completion time.

All publication, browser and final-audit checklist items have successful completion receipts.

## Execution record

- The user explicitly confirmed 300 new companies and continuing on the existing Supabase project. Vercel access was restored after reconnection; production identity and the existing Supabase binding were verified.
- Publication cohort: `additional-300-mdy-2026-11-03-r1`; exact manifest SHA-256 `610ea67ae73465dfc064f7a9022ebcc3d3afe3f05399c0a4e46cd127c2dc6b65`. All 600 SEC submissions/Company Facts response bodies were archived before normalization. TREX replaces unavailable OZK; the original cohort is preserved.
- Period ends after filing dates and quarterly candidates longer than 111 days are explicitly quarantined with their original context and reasons. CAVA's official 16-week first quarter supports the 111-day limit; the 119-day VAL transition and 114-day SCI anomaly remain excluded. Reconstructed filing tables use only verified source records. The final source-quality record contains 168 candidate occurrences across eight companies.
- All 300 stages passed, containing 652,811 selected financial values. Seven pilot companies passed exact relational readback; the full live fingerprint reconciliation then matched every stored field in all 652,811 rows. Shared filings preserve their owners, with 243 explicit skipped-reference receipts. Original 500-company membership metadata was backed up, stamped and read back before activation.
- Core publication completed at `2026-11-05T03:00:34Z`: 300 companies, 652,811 facts, 74,339 filing rows, 900 SEC document records and 1,500 object readbacks. Price publication completed at `2026-11-05T03:23:44.090581+00:00`: all 300 companies and exact relational readbacks of 708,210 bars. IR publication and independent metadata reconciliation cover 307 documents for all 300 additions; IR history remains partial and this batch promotes no IR-derived numeric KPIs.
- Coverage publication completed at `2026-11-05T03:25:58Z`: exactly 800 entries, preserving the original 500 entries, 3,944,368 KPI values and the existing KPI/segment count timestamps. SEC and price originals are separate verified objects; the three research archive shards are supporting evidence rather than a complete container for those originals.
- PR 15 merged as production commit `0166ef66f323bd451fd102636b75ec6f5858d88e`. Deployment `dpl_GucXyizgRrf9dbZdBkovuV1d3mi6` reached READY at `2026-11-05T03:22:39.624Z`; CI run `41961031825` succeeded with 193 Python tests, 186 JavaScript tests and 1,698 parity cases. The 27-asset build passed.
- Actual release order: the active registry deployed while the price publisher held the shared aggregate lease. Deployment readiness preceded final price and coverage receipts. Public aggregate cutover began only after all per-company receipts and coverage completed. The 305-second drain expired at `2026-11-05T03:27:44.624Z`, before retry run `0571f493-d129-4602-a0d4-23eadb0df1b4` began at `2026-11-05T03:31:54.637785+00:00`. A fresh marker binds the already-READY deployment to that run; no claim is made that one activation lease spanned deployment.
- The first cutover attempt stopped before public aggregate writes on preserved legacy BK cache metadata. Baseline evidence confirms that metadata is unchanged; the corrected guard preserves it outside the public 800-company set. Recovery cache verification passed at 04:10:06 UTC: all 300 new funds match, 498 original fund entries are preserved, and 497 unchanged-input companies match across 72 stable financial fields. The three recorded exceptions are sourced newer ABT/BLK filing inputs and KVUE, whose prior null screener price is restored from the saved $19.31 close on 2026-10-30, updating seven dependent financial fields. Existing index quote changes are zero. Final source readbacks, feed publication and the activation receipt also passed; their completion is recorded below.
- Initial browser checks passed at `2026-11-05T03:54:49.202461+00:00`: All tracked 800, SP500 500, legacy SP100 102, OKTA search, FY2026 revenue of USD 2,919,000,000 matched to its original SEC source, original Q2 FY2027 IR release and the OKTA price-return label. The final verified browser receipt is dated `2026-11-05T04:04:25.093425+00:00` and adds an actual post-publication source-drawer check: OKTA TTM revenue growth +11.2%, with eight filed quarterly inputs [805, 765, 761, 742] versus [728, 688, 682, 665] in USD millions, matching 3,073 / 2,763 − 1. There were no failures; All tracked 800 was restored. Evidence: `browser-verification.json` and the unchanged `docs/verification/filedunder-800.jpg`.
- The execution service interrupted runner 11312 around 03:57 UTC after screener run 223 completed successfully with 800 rows and all 800 source files, but before feed/final verification completed. A read-only same-project probe passed at `2026-11-05T03:59:14Z`. The interrupted checkpoint, deployment marker and failure record remain preserved. The original activation helper is unchanged; tail recovery uses the normal lease after expiry, a fresh deployment attestation and separate helper/checkpoint hashes under the same logical run. Recovery UUID: `7bb436b4-14ed-4b7d-8934-185003160b4f`.
- Activation completed: `Published and verified`, completed at `2026-11-05T04:16:49Z`. The published/verified activation receipt confirms all 800 company/source readbacks and the original preservation gates.
- Final audit completed: `Published and verified`, completed at `2026-11-05T04:21:46.486344+00:00`; manifest `backfills/additional-300-mdy-2026-11-03-r1/final-audit/manifests/2c1e95980b714dbc801864211953ba7ce3447401eaa7a3c848ce983ab5fd3027.json`, SHA-256 `2c1e95980b714dbc801864211953ba7ce3447401eaa7a3c848ce983ab5fd3027`. All 39 evidence objects and the manifest passed exact byte/hash readback.
