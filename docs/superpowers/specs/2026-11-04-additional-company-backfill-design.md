# Additional 300-company backfill

## Intent and scope

The user requested continuing Filed Under's backfill for a few hundred more companies using Tavily, Firecrawl and fully sourced values. The current universe is 500 companies. The user explicitly confirmed 300 new companies and instructed us to proceed after reconnecting Vercel. This expands the tracked universe to 800 issuers.

Use the existing Python ETL, Supabase, public JSON bundles and Vercel site. Preserve all existing records. Continue under the user's authorization to implement, validate and publish this backfill; no new design-approval gate is needed for routine implementation choices.

## Approach

Select 300 additional US-listed SEC issuers from an official mid-cap fund holdings snapshot, ordered by disclosed holding weight and cross-checked against SEC and exchange registries. Fund holdings are a selection basis; they do not establish S&P index membership. Preserve the original SP100 and SP500 membership independently of the broader covered universe.

Core numeric values come from official SEC Company Facts snapshots. Preserve exact retrieved bytes and SHA-256, issuer CIK, namespace/concept, unit, value, period start/end, accession, form, filed date and raw-record locator. Every normalized reported value must resolve to an exact source record. Derived values require recursively resolved inputs and verified arithmetic. Disable unarchived inline-XBRL gap filling for this batch; missing facts remain explicit.

Tavily discovers official issuer sources. Firecrawl retrieves full content and document links. Search snippets are discovery evidence only. Preserve provider output and original URLs; distinguish complete page retrieval from excerpts and archive enumeration from proven historical completeness. Report source identity, entity changes, reporting-period transitions and restatements explicitly.

Do not feed partial document extractions to the legacy replacement KPI publisher. Any document-derived KPI promoted in this batch needs verified source content, exact row/cell and header context, period, currency/unit, scale and entity scope. Ambiguous candidates remain unpublished. SEC core-facts completion and IR/KPI completion are separate states.

## Alternatives considered

- Deepening 300 existing companies would improve their history but would not satisfy the working interpretation of adding more companies.
- Blindly appending tickers would incorrectly label the expanded universe as S&P 500 and could trigger unintended provider calls.
- Replacing existing KPI snapshots with partial candidates risks deleting historical data and is rejected.

## Release sequence

1. Freeze and validate the cohort and raw selection sources.
2. Add explicit covered-universe/SP500/SP100 semantics, retaining compatibility with legacy 500-company bundles.
3. Stage exact SEC source snapshots, normalized bundles and relational rows; validate source proofs before publication.
4. Collect official IR documents in bounded resumable batches; retain partial/failure states.
5. Review a representative pilot and the complete staged batch. Publish through existing authorized credentials under distributed leases, with immutable source archives and checkpoints. Refresh coverage from grouped counts rather than global REST scans.
6. Publish expanded public indexes only after their company bundles are available. Activate additions for scheduled maintenance only after successful read-back.

**Observed release ordering (2026-11-05):** The active registry deployed while the price publisher held the shared aggregate lease. Production reached READY at 03:22:39.624 UTC; final price readbacks completed at 03:23:44.090581 UTC and coverage at 03:25:58 UTC. Public aggregate cutover began only after all per-company receipts and coverage were complete and the 305-second drain had elapsed. Activation acquired the shared lease after deployment and used a fresh run-bound marker; the release did not hold one activation lease across deployment. A later execution-service interruption after successful screener publication was resolved by a separate feed/final-verification recovery under the normal lease, preserving the original checkpoint and operator identity. The successful activation and final audit receipts are recorded in the verification report. The original acceptance requirements below remain unchanged.

The user restored Vercel access and explicitly confirmed the existing Supabase project `apxfeteebnspwyajlxfh`. The authenticated Vercel project `filedunderai` and its production Supabase binding were checked before publication. Continue using this project and its existing grants; no replacement database is part of this work.

## Acceptance

Exactly300 additional unique issuer identities; no old-company deletion or unexpected update; every new promoted financial value has a complete immutable source proof; no unreported failed reconciliation; explicit status for each company's core facts, IR discovery, checked documents and operating metrics; original SP500/SP100 membership preserved; meaningful regression tests and representative public read-backs; durable batch and source receipts.
