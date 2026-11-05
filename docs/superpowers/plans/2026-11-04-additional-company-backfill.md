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

- [x] Fetch a representative source pilot, inspect its source proofs and reporting contexts, then continue300 in bounded batches.
- [x] Use Tavily/Firecrawl to discover and read official IR sources; retain raw output, URLs, timestamps and explicit failures.
- [ ] Independently audit staged counts, identity, all promoted-value proofs and unchanged existing-company fingerprints.
- [ ] Publish only through authorized credentials, under leases, with source/object read-back and journal checkpoints.
- [ ] Bootstrap new coverage entries with grouped counts, publish expanded indexes, then activate scheduled registry additions.
- [ ] Run relevant full suites, build and final read-back checks. Commit code/receipts and report exact completed versus pending work.

## Execution record

- User explicitly confirmed 300 new companies and continuing on the existing Supabase project. Vercel access was restored after reconnection; project identity and authenticated Supabase read were verified.
- Publication cohort: `additional-300-mdy-2026-11-03-r1`; exact manifest SHA-256 `610ea67ae73465dfc064f7a9022ebcc3d3afe3f05399c0a4e46cd127c2dc6b65`. All600 SEC submissions/Company Facts response bodies were archived locally before normalization.
- Ruling: Period ends after filing dates and quarterly candidates longer than111days are explicitly quarantined with full context and a reason; they are preserved in the immutable source and company quality record, never silently relabeled or promoted. The strict source-proof gate still validates every retained point. The original105-day limit was corrected after the official CAVA filing established its16-week first fiscal quarter (111 days between start/end); the119-day VAL transition and114-day SCI anomaly remain excluded.
- Ruling: Reconstructed filing-table cells are restricted to verified proof records, including per-cell source pointers, so these tables cannot reintroduce excluded values.
- All300 company stages passed, containing652,811 selected financial values. Seven pilot companies passed exact relational read-back; shared beneficial-ownership filings already held by another issuer are preserved with explicit skip receipts. Original500 index/screener membership metadata was stamped under the aggregate lease, backed up and read back exactly. Public row counts remain500 until activation.
- New UI/API membership behavior passed186 JavaScript tests and a27-asset build. Independent core, IR and screener reviews passed after focused fixes. The financial/source suite passes 193 tests after the final shared-filing and IR resume guards. Price source readbacks are complete; full relational price readbacks are in progress.

- Core publication completed at 2026-11-05T03:00:34Z: all 300 companies, 652,811 fact rows, 74,339 filing rows and 900 SEC document records. Independent exact fingerprints matched all 652,811 fact rows across every stored fact field. The 243 shared filing references retain their existing owners.
- The activation branch enables the frozen 300-ticker registry. Merge and aggregate publication remain gated on successful price, IR and coverage receipts, a lease spanning production deployment, and the full 800-company aggregate checks described in `docs/verification/2026-11-05-additional-300.md`.
