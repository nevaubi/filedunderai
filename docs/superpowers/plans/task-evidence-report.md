# Evidence and assistant implementation report

Implemented in the shared feature worktree. No commits, pushes, production mutations, external model requests, or child agents.

## Owned changes

- `public/lib/evidence.js` (new): immutable content-addressed evidence ledger, immutable source-cell snapshots, local persistence, bounded lookup, exact numeric claim validation.
- `public/lib/financial.js` (new): deterministic arithmetic parser and financial calculations; strict quarterly-flow TTM policy.
- `public/app.js`: evidence-backed financial tool outputs and citations, safe Markdown table citation parsing, source drawer integration, explicit report verification, deterministic tools, cancellation, preserved follow-up tool history, provider hook.
- `api/ai.js`: validated completion protocol, bounded attempt deadlines, retryable fallback, client cancellation propagation.
- `api/screen.js`: full current-screen context, intact JSON history, bounded model/catalogue calls, cancellation, response shape validation.
- `public/pages/screenspec.js`: Boolean equality/inequality truth table corrected.
- `tests/evidence/evidence.test.mjs` and `tests/assistant/assistant.test.mjs` (new): 18 behavioral tests against actual production functions.

No index, package, CSS, ETL, database, monitoring, or other page module edits made by this worker. Those shared-worktree changes belong to other workers/root.

## Load order and API contract

Load `public/lib/evidence.js` and `public/lib/financial.js` before `public/app.js`. Both are classic scripts exposing frozen objects on `window` (or `globalThis` for Node tests).

`FU_EVIDENCE`:

- `capture({ticker, metric, value, unit, period, periodStart, periodEnd, periodType, concept, accession, filedDate, sourceUrl, availabilityDate, inputEvidenceIds, derivation, validation, quality})` returns a deeply frozen record with stable `id`. Identical content returns the same ID; revised values/vintages produce separate records. Invalid/missing values remain null with an unverified state.
- `fromCell(cell, {sourceUrl, period, filing, filedDate, resolveContext} = {})` recursively captures calculation/derivation inputs and preserves an immutable plain source-cell snapshot. `resolveContext(childCell)` supplies per-input source metadata.
- `get(id)` returns the frozen record or null; `cell(id)` returns its frozen source snapshot or null.
- `list({ticker,metric,offset=0,limit=20})` returns `{records,total,nextOffset,truncated}`. Maximum page size is 40.
- `validateClaim({evidenceId,value,unit,ticker?,metric?,period?})` returns `{status,reason,evidenceId}`, with status `verified`, `contradicted`, or `unverified`. Exact numeric value/unit/optional identity must match. This does not validate prose or independently reconcile a filing.
- `citation(id)` returns `[[E:ev_...]]`.
- `persistence()` returns `{ok,error}`. Storage failure is surfaced in the source drawer; memory records remain usable.
- `safeUrl(url)` accepts HTTP(S) only.

Each record contains `version,id,ticker,metric,value,unit,period,periodStart,periodEnd,periodType,concept,accession,filedDate,sourceUrl,availabilityDate,inputEvidenceIds,derivation,validation,quality`. `quality` preserves ETL `accessions`, `vintage`, `selection_reason` (as `selectionReason`), and `validation_status` (as `sourceValidationStatus`). `available_at` drives availability; calculated availability comes from contributing records. Reported/derived ETL labels are provenance categories, not independent reconciliation guarantees. `validation.scope` states the limited check explicitly.

Global main-app helper `evidenceForCell(cell)` supplies filing URLs, fiscal period labels and child context to `fromCell`. Root analyst and workspace workers can use it directly.

`FU_FINANCIAL`:

- `ttm(cells)` requires exactly four contiguous quarterly USD flow cells with valid dates, same company/metric/unit/statement, 70–105 days each, 350–380 total days, and no missing numeric values. Returns a calculated source cell or null. Balances, shares, EPS/per-share figures, ratios and unknown measures are rejected. Approved calculated flows: Free cash flow and Capital returned.
- `formula(expression, variables)` supports arithmetic, parentheses, numeric variables, SUM/AVG/MIN/MAX/ABS/SQRT with bounded length and nesting. There is no eval, Function, executable JavaScript, host capability, or network access surface.
- `calculate({operation,expression,variables,values,periods})` supports formula, growth, CAGR, sum and mean with finite-value checks.

`window.FU_TOOL_PROVIDERS` is an optional array of functions `(trace) => toolDefinitions[]`, merged in `baseTools(trace)` each request. Existing `chatPanel(host,cfg)` signature and `cfg.tools(trace)` extension behavior are preserved. This checkout did not contain a `createChat` function.

## Assistant and evidence behavior

`get_financials` returns exact raw values/units, a complete evidence record per point, evidence ID/citation, and total/offset/nextOffset/truncated metadata. Maximum page size is 20; series are newest first. Percent metrics use exact fractions, not silently rounded percentages. TTM is a full evidence record or null with policy text.

Research tools added: `calculate`, `lookup_evidence`, `inspect_evidence`, `compare_evidence`, `validate_claims`. `run_js` and its worker were removed. Comparisons require matching metric, unit and period type, expose exact inputs, and state whether period ends match.

Citations resolve immutable IDs. Legacy `[[TICKER|Metric|Period]]` syntax remains supported; unresolved unloaded-company references can be clicked to load and resolve. Legacy references inherently lack historical identity until captured; historical stability is guaranteed by evidence IDs. Unknown evidence IDs are visibly unresolved. Markdown table splitting protects citation pipes; ampersands are escaped once. Citation labels and attributes are escaped, and source URLs accept only HTTP(S).

The system asks for `{markdown,claims:[{evidenceId,value,unit,ticker?,metric?,period?}]}`. Exact claims are checked before display. Contradictions withhold the report; adjacent displayed-number citations are also checked with display-rounding tolerance. Structured claims are shown individually, and the narrative is explicitly unverified. Unstructured responses receive an explicit unverified label. This is a limited numeric/provenance check, not semantic verification of all sentences or every possible numeric typography.

Tool context is preserved across up to four whole user exchanges within a 240,000-character history budget. Tool exchanges are never cut midway. Oversized results become complete JSON errors with `truncated:true` and a pagination instruction; no arbitrary JSON substring is returned. Active-loop context is bounded. Sequential tool execution respects proposal dependencies and gives cancellation explicit boundaries. AbortSignals reach company/price fetches, web tools, model fetches and tool context; abort errors are not swallowed as tool success.

Server model attempts have 25-second timeouts. Retryable HTTP errors, transport failures and malformed completions can use one fallback model. Nonretryable client errors and cancellation do not retry. Empty/truncated completions, malformed tool calls and non-object tool arguments are rejected. Screen requests always include the supplied validated current spec, including ticker-only universes, exclusions, index, sort and columns.

## Validation evidence

Latest focused run:

```
node --test tests/evidence/*.test.mjs tests/assistant/*.test.mjs
18 tests passed, 0 failed
```

Combined regression run before the final date-guard and legacy-resolution refinements:

```
node --test tests/evidence/*.test.mjs tests/assistant/*.test.mjs tests/technicals/indicators.test.cjs
35 tests passed, 0 failed (18 new + 17 existing)
```

The final refinements passed all 18 focused tests again. Node syntax checks passed for edited app/server scripts; `git diff --check` passed for owned tracked files.

Coverage includes immutable restatements and reloads, source snapshots, exact claim mismatch, valid pagination, strict TTM, actual browser-model wiring and missing sum inputs, non-executable calculations, citation pipes/ampersands/injection, displayed and structured numeric contradictions, independent derived-input URLs/dates, complete tool-history preservation, explicit oversized-result JSON, cancellation, extension providers, server fallback/malformed responses, Boolean truth tables and screen follow-up context. Network calls are mocked; no paid API calls were made.

## Limits and integration notes

- Root must integrate assets and perform browser route/source-drawer/editor QA. This worker ran function-level tests, not a full signed-in UI session.
- Client TTM EPS is deliberately unavailable rather than summing quarterly EPS. Consequently client valuations depending on that TTM helper may show unavailable P/E; existing index/precomputed metrics are unaffected. A later implementation can use a correctly reported annual/TTM EPS basis with explicit methodology.
- Local evidence persistence is bounded by browser storage capacity; there is no silent eviction. Storage failure is visible. Cross-device durable evidence storage is not implemented.
- Old source IDs retain exact numeric/filing metadata. Archived full-document availability still depends on the existing filing endpoint/storage; the original SEC URL remains in the record.
- Available source data, not the ledger, determines whether a period has an independently reconciled fact. Validation does not claim broader factual verification.
- The initial-price dependency was already correct. Only cancellation propagation was changed there; no nonexistent dependency bug was reported or fixed.
