# IR reliability, compact editors and deeper research

Implementation base: `1150b673227232940774d1cdbc4b525c78c93248`.

## Changes

### IR backfill and document access

Backfill now persists discovery, document-download and extraction queues in private storage. Successful documents do not consume the new-work allowance. Failed and deferred work survives subsequent invocations. A normal maintenance run reserves work and time for new documents while advancing legacy extraction repairs. SEC roots refresh daily and completed web pages weekly, independently of blocked historical work. Source polling timestamps represent actual work rather than successful no-op runs.

Full-history scope survives maintenance. A missing/corrupt/unavailable checkpoint cannot silently replace progress with an empty state. An IR-specific Supabase adapter bounds each I/O attempt by the remaining run deadline, including lease acquisition, relational pagination, storage and aggregate publication. Per-company and aggregate leases protect writes without attaching worker leases to a shared client.

Coverage records separate source enumeration, pending/failed work and text-extraction quality. Web crawling remains unverified even after all known links are followed. Legacy text paths remain unverified until checked. The company IR library displays all stored records through search, document-type filtering and 50-row pagination; the previous 200-record display cap is removed. Actual publication dates are preferred, with explicitly labeled mapped fiscal periods or unavailable dates as fallbacks.

Spreadsheet repair preserves original bytes and previous text objects. XLS is supported through pinned `xlrd==2.0.2`; XLSX files with Windows ZIP member separators are normalized only in memory, with collision/path/archive safety checks. Extraction limits and blank/failed pages are explicit. CSV parsing preserves quoted separators/newlines and numeric precision.

### Screener and workspace

The screener's main control row contains search, a preset selector, Filters and Actions. All 19 presets remain available. Sort, group, columns, saved screens, sharing, CSV export, assistant and reset are grouped in Actions; statistics start collapsed. Menu lifecycle handles outside clicks, Escape, route changes and focus restoration. Sorting by company/ticker cannot insert identity fields into the metric columns.

Workspace surfaces and dialogs use scoped white/neutral tokens. Mode controls share the title row; desktop height targets are title 36 px, tabs 28 px, ribbon 36 px, formula 32 px and ruler 20 px. Mobile controls retain 40 px touch targets. The editor host stays mounted. Unsubmitted formulas survive mode changes and commit through the existing Undo history. Document selection, formatting, sources, history, export and AI proposal review remain available.

### Research and code capabilities

Brief, Detailed (default) and Report profiles use explicit request, tool, time, output and claim bounds. A valid length-limited answer can continue within the profile budget, with reserved final synthesis capacity. Cancellation stops further requests; malformed/truncated tool arguments are never executed. Completed continuation fragments are retained as one complete answer with the original question and tool evidence. Large histories compact intact tool payloads rather than slicing JSON or erasing a completed response.

`FU_RESEARCH_RUNTIME` is separate from the existing analyst-page `FU_RESEARCH` API. Integration tests load the actual index scripts in browser order to detect namespace collisions.

The new `run_analysis` tool interprets bounded filter, derive, group, sort and statistics operations over immutable evidence records. It uses a fixed expression parser, explicit assumptions, dimensional/period checks and source lineage; it does not run arbitrary JavaScript, Python, shell commands or installed packages. Financial percentages use fractions (`0.2 = 20%`). Source-ID budgets include both input rows and bound values.

Code blocks/inline code render as escaped text, and citation-looking syntax inside code remains inert. The same Markdown segmentation drives rendering and numeric checking. Source reads support explicit 12,000-character pages within a 240,000-character source window, identified by the full content hash. Longer output does not bypass structured-evidence validation: contradicted claims remain withheld and unsupported/overflow content remains partial.

## Live data audit and repair

The initial database contained 73,404 IR documents across 499 companies. Original storage references and all 73,401 existing text references resolved; three spreadsheet documents lacked text. All publication dates were null. The 500 public manifests contained 72,088 records, leaving 1,316 existing rows unpublished. Ninety-five companies had more than 200 records, beyond the old UI display limit.

Applied repairs:

| Repair | Observed result |
|---|---|
| Agilent XLS `2993bdd7287642b6` | Readable extraction restored; original preserved |
| Entergy XLS `cae9533488343f37` | All 80 sheets and 27,987 cells extracted; original preserved |
| Marathon Petroleum XLSX `71c1f3f5e685f038` | All 17 sheets and 8,184 cells extracted; original preserved |
| Exact SEC accession + company publication-date join | 8,678 actual filing publication dates restored; other unknown dates remain null |
| Vylor official news archive | 16 distinct releases, full rendered article bodies and actual publication dates loaded |

Vylor's configured investor root returned 404 without its required trailing slash. The official root was corrected. The official published archive declared 16 matching releases; all 16 IDs were enumerated and each captured page was checked for its headline and a nonempty article body. Two initial dynamic loaders were rejected and recaptured after rendering. Stored provenance identifies these as rendered HTML captures, retaining official source URLs and publication metadata. This verifies that particular news listing, not every Vylor investor-relations archive or SEC filing.

After these changes, the database contains 73,420 documents across 500 companies, 73,420 text references and 8,694 publication dates. Nineteen new/repaired extractions have passed the current structural checks; 73,401 legacy extractions remain unverified. An object reference alone does not establish text quality or financial correctness.

Full metadata and original pre-repair rows were backed up privately before broad changes, under `raw/ir/repair_backups/2026-11-04T23-27-06Z/`. The metadata backup SHA-256 is `592bb8ea2d40b41f9f91539ee71b66625d09e2b5e8f6f390fb61af86bac84c93`. Reconciliation preparation used a fresh, ordered complete relational read and independently matched database ID and publication/text/hash digests.

## Verification

- 160 JavaScript tests passed, including actual-page integration, screener interaction, formula-draft preservation, evidence calculations and research continuation/history regressions.
- 74 Python financial/IR tests passed, including 46 focused IR regressions.
- 1,698 technical events matched between Python and JavaScript implementations.
- Production build passed with 27 fingerprinted assets and the Office export bundle.
- Python compilation and `git diff --check` passed.
- Independent UI, research and IR reviews accepted their cohesive correction waves. The final integrated branch review also found no blocking integration or regression issue.

## Publication and browser verification

Pending final publication of the prepared 500-company manifests, reviewed code deployment and live browser checks. This section is updated with actual outcomes before final delivery.

## Limits

Complete collection of every historical IR document has not been established. The current release exposes and reconciles stored data, repairs specific failures and makes remaining collection/extraction work durable and visible. Dynamically loaded or undiscovered historical material can remain outside known queues. Retrying exhausted work requires an explicit refresh. Parser CPU work is cooperative rather than enforced through a separate process kill.

Research orchestration and evidence behavior were verified with controlled model responses; no new paid/live model generation was used in this change. Real model answer quality remains provider/model dependent. Profile budgets are enforced per run and request, not by a durable server-side cumulative spend ledger. Arbitrary code execution is deliberately outside this implementation; reproducible source-based analysis is supported.
