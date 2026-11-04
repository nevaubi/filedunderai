# IR coverage, compact workspaces and deeper research

## Intent

Improve IR backfill reliability and make coverage auditable; reduce screener controls; give the document and spreadsheet editors white surfaces and a substantially shorter header; support longer evidence-backed research and reproducible analysis programs.

The user explicitly requested implementation. The existing reference visual system remains the main app style; white editor surfaces and smaller editor controls are the user's new refinement. Routine implementation choices below are engineering decisions, not additional user approvals.

## Findings and approach

Production baseline: 500 companies and IR sites, 73,404 documents across 499 companies, 73,401 text references. A storage-object join found no missing original or referenced text objects. This establishes storage-path integrity only. All publication dates are null; 50,544 records have period ends. Existing web discovery examines only a few pages, completed files consume the processing cap, and extraction/discovery failures can be reported as successful runs.

For IR, improve the existing Python pipeline with a private resumable checkpoint rather than add another orchestration service. Preserve original records/files; record failures, remaining work, extraction limits and source scope. A complete label requires verified enumeration with no unresolved work. Map-only or legacy coverage is unverified. For UI, consolidate existing controls instead of removing capabilities. For research, extend the current Fireworks tool loop and add a bounded typed analysis interpreter rather than execute arbitrary model-written server code or require a new service.

## Requirements

### IR ingestion and coverage

- Resume archive-page and document queues across invocations, with bounded retries, explicit deadlines and strict checkpoint reads.
- Never let previously successful records consume the new-work budget. Retry extraction from stored originals independently of discovery cooldown.
- Include discovery/download/extraction/deferred failures in company and job statistics; retain skipped/failed rotation work.
- Use the existing lease mechanism safely across worker clients. No overlapping unguarded aggregate publication.
- SEC-only mode must not invoke Tavily or Firecrawl. Preserve actual SEC filing publication dates, never substitute retrieval dates.
- Parse quoted CSV correctly; classify empty/unsupported/limited extraction honestly. Preserve page, sheet and cell coordinates and existing evidence/source identities.
- Preserve legacy records; do not claim their extraction has been independently checked merely because text_path exists.
- Add publication/period/extraction metadata and a compact coverage object to manifests. Include all 500 companies in coverage reporting.
- Make every IR list record reachable; use actual dates and parsed fiscal periods rather than lexicographic labels for order.
- Run bounded repair/backfill work with existing authorized access when possible, verify results and report unresolved scope without claiming universal completeness.

### Screener

- Primary controls: search, preset selector, counted Filters, Actions.
- Keep all presets, filters, sorting, grouping, columns, saved screens, links, export and assistant available.
- Optional result statistics stay collapsed initially. Preserve explicit assistant preferences; new state defaults closed.
- Popovers have one active lifecycle, accessible names, Escape/outside/route dismissal and focus restoration. Nested actions anchor to the persistent Actions button.

### Editors

- White editor surfaces and dialogs; neutral gray canvas; retain document paper and user formatting.
- Desktop target: 36px title/mode/actions row, 28px tabs, 36px ribbon, 32px formula bar, 20px ruler. Mobile targets remain at least 40px where currently supported.
- Keep existing mode, input, source, history and assistant hooks and command selection preservation. No host rerender merely to toggle controls.
- Preserve viewport containment and local paper/grid scrolling.

### Research

- Brief, Detailed and Report profiles; Detailed default. Server validates and caps budgets.
- Longer analytical prompts synthesize evidence, comparisons, calculations, risks and missing evidence without padding or inventing facts.
- Preserve valid length-limited text and continue within hard limits. Never execute incomplete tool arguments or label partial structured JSON checked.
- Reserve a final synthesis step, bound total calls/time/output, preserve useful partial progress on limits, honor cancellation.
- Claim limits indicate incompleteness rather than a false contradiction. Contradictory claims still withhold output.
- Render escaped fenced code and hierarchical headings; citation syntax inside code must stay inert.
- Add run_analysis for typed, bounded programs: filtering, safe derived formulas, grouping, sorting and statistics. Use immutable evidence inputs, propagate lineage, reject invalid operations/nonfinite math/mixed-unit calculations, and explicitly label user assumptions.
- Keep existing tool/evidence contracts and reviewed editor proposals. No arbitrary eval, Function, process spawning or new execution service.

## Constraints and verification

Existing API activation remains deferred. Use mocked model responses to test orchestration. No credential changes, new grants, database schema changes, emails, trading actions or private-data publication. Source/account targets stay the user's nevaubi/filedunderai and apxfeteebnspwyajlxfh project. Tests must verify actual failure cases, not mirror cosmetic CSS. Review each independent scope, run required suites/build/CI, then verify deployed interactions and save actual screenshots. Describe live-data and mobile limitations explicitly.
