# Repair historical financial normalization

The cleanup command removes only explicitly derived quarterly EPS/share rows.
Full normalization is a separate, stricter command using existing private SEC
Company Facts objects. Neither mode fetches SEC data, prices, news, documents or
model output. Planning reads production and writes candidates locally. Applying
or rolling back is a separate operation.

## Required environment

Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in the server process through
the existing secret mechanism. Never put the key in a command argument, plan,
repository file or public artifact. Run commands from the repository root.

## Inventory and bounded planning

```bash
python -m etl.repair_normalization inventory
python -m etl.repair_normalization plan-cleanup --tickers NVDA,A,ABNB --clean-bundles --output /tmp/nonadditive-cleanup-pilot
```

`plan-cleanup` does not introduce replacement financial values. It selects only
quarterly database rows with `derived=true` and a recognized nonadditive EPS/share
label or concept. Every directly reported row and every additive value is retained.
The optional `--clean-bundles` flag also removes explicitly derived nonadditive
quarterly points from selected and historical company-bundle arrays. Annual
values, reported quarters, unrelated data and aggregate inputs remain unchanged.

Cleanup does **not** set or upgrade `pipeline_version`. Legacy companies remain
pending full normalization; the aggregate publisher continues withholding their
unverified financial metrics. If a bundle already has an indexed source proof,
point removal is held because it would invalidate that proof's array indexes.
Database-only planning without `--clean-bundles` remains available for review.

To investigate full normalization separately:

```bash
python -m etl.repair_normalization plan --tickers NVDA,AAL,MSFT --output /tmp/financial-repair-pilot
```

Inventory distinguishes unversioned or legacy aggregate entries from actual
unsupported derived quarterly EPS/share rows in the database. These sets need
not be identical. The planned repair scope is their union among the active
universe. Pending companies are not activated by this operation.

Select explicit tickers for each plan, with a maximum of 25 companies. The default
four planning workers only perform reads. Use `--workers 1` for a single sequential
pilot. A plan directory cannot be overwritten. Run later batches into new paths.

For full normalization, each company folder contains the exact archived source bytes, source proof,
original and candidate company bundle, original and replacement relational
facts, and original and candidate aggregate metadata. `plan.json` seals these
files with byte counts and SHA-256 hashes and lists changed periods and affected
rows. Cleanup plans instead seal the complete ordered before/after snapshots and
the exact unsupported rows selected for deletion. They do not need to refetch an
archive because no new numeric values are introduced. Do not commit production
snapshots, plans, receipts, backups or credentials to the repository.

Only a plan whose candidates are all `ready` can be applied. A missing archive,
issuer mismatch, unsupported source proof or empty replacement blocks planning.
A candidate that loses a previously reported period is `review_required` and
cannot be applied by this command. Derived nonadditive quarters may disappear;
that is the intended correction. Other historical relational rows absent from
the candidate remain untouched and must be reviewed separately.

The planner also compares every replacement with the database independently of
the public bundle. It blocks replacement of a directly reported row by a derived
value or an older filing. An equally dated reported value with a changed value,
unit or period start requires review. A database-only reported period absent
from the candidate also requires review. Apply recomputes these checks from the
sealed inputs, so changing a plan's status does not bypass them.
Changed reported values with an unknown filing date are also held; missing dates
cannot establish that a replacement is newer.

An existing reported observation is preferred over an equal derived alternative
only when its complete concept/unit/period/value/accession/filing context matches
an exact archived SEC record. That observation is retained in the candidate
bundle and relational replacement and independently passes the full source-proof
gate. This preserves reported evidence without trusting a legacy database flag.

## Apply a reviewed pilot

```bash
python -m etl.repair_normalization apply --plan /tmp/nonadditive-cleanup-pilot/plan.json
```

Sequential issuer processing and one-row deletion requests remain the defaults.
After reviewing a bounded plan and the batching path, opt in explicitly:

```bash
python -m etl.repair_normalization apply --plan /tmp/cleanup-batch-01/plan.json --workers 4 --delete-batch-size 20
```

For a reviewed sequence of cleanup-only batches followed by a separate verified
aggregate publication, opt in to deferral:

```bash
python -m etl.repair_normalization apply --plan /tmp/cleanup-batch-02/plan.json --workers 4 --delete-batch-size 20 --defer-aggregate
```

This option is rejected for full normalization. Cleanup candidates must leave
their sealed fundamental metadata unchanged, and the entire live fundamental
snapshot, including unselected companies, must still equal the original snapshot
after all issuer work succeeds. The command then records `status: cleanup_complete`
and `aggregate_status: deferred`, with the unchanged snapshot's SHA-256. It writes
no fundamental snapshot, public index or relational company metrics. This status
confirms the fact/bundle cleanup only; the separately controlled final aggregate
and screener publication is still pending. All backups, leases, exact-row guards
and full issuer readbacks remain required.

Issuer concurrency is bounded at eight. All workers share one outer aggregate
lease; they do not acquire nested leases. The complete batch backup is verified
before any issuer writes begin. Workers only mutate their selected issuer.
Only the coordinator updates receipts and the shared fundamental snapshot, and
the final aggregate publication runs after every issuer succeeds. The first
error stops dispatch and prevents further worker mutations. All in-flight tasks
are awaited before the lease can be released.

Application acquires the existing renewable `financial-aggregates` lease; it
refuses to run while a compliant aggregate writer holds that lease. It also
requires each selected company's bundle, aggregate entry and complete fact set
to still equal the reviewed input. A stale plan fails before public or fact
changes and must be generated again.

The repair uses a dedicated client with one bounded HTTP attempt per mutation.
Every write checks the lease immediately before and after the request, and its
timeout fits within the remaining lease lifetime. Ambiguous write failures stop
the operation instead of retrying under a potentially expired lease. Receipts
are replaced atomically, preserving the last recovery pointer on interruption.

Before replacements, the command writes a complete compressed backup into the
private `raw/repairs/financial-normalization/` prefix and verifies its bytes.
The local `apply-receipt.json` records that backup path before the first change,
so an interrupted application still has a recovery pointer.

Full normalization verifies and retains the immutable source archive and proof. Both modes use
`persist_normalized_facts` for the real upsert and guarded cleanup. That helper
backs up each obsolete derived fact group before exact-key deletes. Reported
replacements at the same key are protected. The repair compares the resulting
full fact set with the predicted result before promoting the company bundle.
In cleanup-only mode the replacement-row list is empty, so it cannot overwrite
any reported or additive value. The before/after fact comparison also confirms
that all unrelated rows are identical.

The fundamental entry receives the current normalization version only after a
source-proved rebuild. It is never upgraded by changing a version flag alone.
Unselected companies retain their aggregate inputs. Final aggregate publication
uses that in-memory snapshot, reads it back, and verifies the public index and
core relational `company_metrics` against the same publication. This applies
the current price/share-date guard, including rejection of future-dated shares.

The first pilot should include an affected legacy issuer and an already
normalized control. Review source proof counts, removed nonadditive rows,
retained reported periods, and index/relational equality before larger batches.
Re-run inventory after each batch; its observed timestamp and the receipt show
progress without assuming that scheduled jobs have stopped.

## Failure, partial receipt and restart

Read `apply-receipt.json` even when the command fails. The private recovery pointer
is recorded atomically before the first fact or public-object mutation. A receipt
with `status: published` confirms cleanup and aggregate verification. The explicit
deferred path instead finishes at `cleanup_complete` with `aggregate_status: deferred`;
do not report aggregate publication from that receipt. Intermediate states are
`applying` and `publishing_aggregate`; an error records `failed`. Completed callbacks can be
skipped when another worker fails, so the listed completed tickers are not an
exhaustive account of partial mutations. The full private before/after backup,
and fresh ordered reads, determine the actual state.

Do not blindly rerun an old plan after partial writes. Its original before-state
checks should reject changed inputs. Either use the verified backup and guarded
rollback below, then plan again, or create a fresh read-only cleanup plan from the
actual current state and review the remaining work. A lost lease, a changed input,
an ambiguous write response, a readback mismatch or an invalid backup is a hard
stop. Do not bypass checks or reclassify a blocked full-normalization plan as ready.

The optional exact-batch path is selected only with `--delete-batch-size` greater
than one. It uses the same full primary-key, `derived=true`, accession, concept
and value filters in every AND tuple, with an outer issuer/quarter/derived boundary.
Only an OR of those fully guarded tuples is batched. It caps both deletion and
readback URLs at 6,000 encoded bytes and each group at 20 rows, verifies the private
per-issuer backup hash and row set first, and reads remaining complete keys without
the old value guards. A concurrent reported replacement survives; a changed derived
row causes a failure. The existing per-issuer backup is created and verified before
delegation; the helper independently verifies its hash and complete row set again.

## Guarded rollback

Read the exact backup path from `apply-receipt.json`:

```bash
python -m etl.repair_normalization rollback --backup raw/repairs/financial-normalization/RECEIPT_SHA256.json.gz
```

Rollback verifies the backup hash encoded in its path, takes the same lease,
and checks each affected fact, bundle and aggregate input against the recorded
before/after states. It supports an interrupted apply but refuses subsequent
unrelated edits. It restores prior facts and company bundles, removes only new
fact keys with exact value/accession guards, preserves unselected companies,
and rebuilds the aggregate index with the current safety rules. It does not
reintroduce fabricated legacy values into the index. Immutable source/proof
objects remain as an audit trail.

## Scope and remaining publication work

The initial audit snapshot contained 441 legacy active aggregate entries and 12,754
unsupported derived rows across 431 issuers. A fresh ordered inventory at
**2026-11-05 14:35:26 UTC** found **434 legacy entries and 12,487 unsupported rows
across 424 issuers**, reflecting seven companies processed by the scheduled
normalizer. Treat those as dated observations;
the command's live inventory is authoritative for a repair batch. A company can
have clean relational facts and a stale bundle, or the reverse.

Full-normalization pilots for Agilent, Airbnb and NVIDIA exposed historical
reported-versus-derived value differences, so those candidates were held. The
narrow pilot targets 92 explicit unsupported database rows and 50 derived bundle
points across those three issuers, with no financial replacements or version
upgrade. A prepared plan is not evidence that the cleanup has happened; only an
actual verified apply receipt and a new live inventory establish completion.

Read-only preparation from that inventory completed 18 further bounded plans at
**2026-11-05 14:51:35 UTC**: 431 selected issuers, 12,395 database removals and
6,521 bundle-point removals, all ready at planning time. With the explicit
20-row option, these plans require 834 deletion requests and 834 matching
key-readback requests; their largest encoded deletion/readback URL is 5,448
bytes. Complete ordered snapshots, verified backups and aggregate publication
are additional work. Plans still require the fresh input and lease checks at
apply time, and these counts do not claim any production deletions occurred.

Full normalization rebuilds company bundles and relational facts; cleanup only
removes its reviewed unsupported rows/points. By default both republish
`meta/fundamentals.json` and `index.json`; explicit cleanup-only deferral leaves
those aggregates untouched for the final controlled publication. Neither mode
runs the normal screener job because that job may
refresh market-provider data. The screener financial cache now incorporates
normalization version and share metadata, fails closed on legacy or failed
extractions, and is invalidated by cache version 6. Complete its separately
controlled regeneration before claiming the public screener is refreshed.
Missing true reported EPS, ambiguous share classes and missing source archives
remain unknown; the repair does not manufacture values to increase coverage.
