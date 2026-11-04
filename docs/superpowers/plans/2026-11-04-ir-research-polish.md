# IR, Compact Workspace and Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track steps with checkboxes.

**Goal:** Reliable resumable IR collection, a simplified screener, compact white editors and longer reproducible research.

**Architecture:** Extend existing ETL, vanilla browser UI and Fireworks orchestration. Private IR checkpoints use existing storage and leases. Research analysis is a fixed bounded interpreter using source evidence.

**Tech Stack:** Python, JavaScript, CSS, Supabase Storage/Postgres, Vercel, existing Node/Python test suites.

**Spec:** `docs/superpowers/specs/2026-11-04-ir-research-polish-design.md`

## Global Constraints

- Preserve original files/records, source/evidence identities, financial units and missing-value semantics.
- Never claim complete from a successful cron, a map result, a row count or a text_path alone.
- No new provider activation, credential changes, database schema changes, emails, trades or security grants.
- Keep main-app reference styling; white/compact changes are scoped to editors.
- No arbitrary eval, Function, server process execution or unsafe HTML rendering.
- Work only in /workspace/scratch/6f66af7c3d91/filedunderai-improvements on feat/ir-research-polish. No worker commits/pushes; root integrates/publishes.
- File owners edit only assigned regions; no whole-file reformatting. Independent disjoint implementation may run in parallel.

## Review Focus

- Partial/failed enumeration must survive invocation boundaries without being mistaken for completion.
- Existing completed candidates must not starve queued files or failed extraction repairs.
- Compact menus and moved editor commands must preserve keyboard focus, pending input and document selection.
- Truncated output/tool JSON must remain unverified/inert until complete; cancellation must not trigger more calls.
- Analysis programs must preserve provenance and reject excessive/ambiguous input rather than silently compute fabricated results.

### Task 1: IR pipeline reliability

**Files:** etl/ir.py; IR functions in etl/jobs.py; IR options in etl/run.py; api/cron/ir.py; IR coverage section in etl/kpitools.py; focused tests under tests/financials; requirements.txt only if a verified parser dependency is necessary.

**Interfaces:** Retain process_company/ir_job/ir_rotation_job compatible return contracts; extend their result dictionaries. Publish ir/{ticker}.json with original documents plus period_end, published, extracted_at, extraction status and coverage. Private raw/ir/checkpoints/{ticker}.json carries scope, queues and outcome state. Root consumes coverage in renderIrDocs. Define and document exact coverage fields in the implementation report.

- [x] Add regression tests for starvation, multi-run resume, failed checkpoint reads, SEC-only isolation, deadlines, error propagation, empty extraction, quoted CSV and honest truncation.
- [x] Implement bounded private checkpoints, retry/extraction state, truthful metadata, safe leases and rotation retries without destructive schema/data changes.
- [x] Run relevant Python tests; report scope, interfaces, test evidence and actual remaining limits.

### Task 2: Compact screener and white editors

**Files:** public/pages/screener.js; public/pages/workspace.js; public/workspace.css; tests/screener/layout.test.mjs; relevant workspace DOM tests.

**Interfaces:** Preserve registered screener state/spec handlers and public workspace mode/input/source/history/assistant hooks. Do not edit app.js, engine files or index.html. Research profile selector lives in shared chatPanel and requires no workspace API changes.

- [x] Consolidate presets/actions/filters and make statistics optional; correct popover lifecycle with focused interaction tests.
- [x] Combine title/mode row and reduce desktop rows to the spec targets; scope white tokens to workspace/dialog and preserve mobile touch controls.
- [x] Verify preset/filter/export/share/menu interactions and editor pending-input/selection/Undo/source/assistant behaviors with meaningful DOM tests.

### Task 3: Deeper research and bounded analysis

**Files:** api/ai.js; assistant/rendering region and pResearch in public/app.js; new public/lib/research.js; new public/research.css; api/scrape.js if paginated source reads are included; focused tests/assistant and tests/evidence.

**Interfaces:** Keep agentLoop/chatPanel/baseTools contracts compatible. Add FU_RESEARCH_RUNTIME shared profile/program interface; root adds its script/CSS to index.html. Preserve checkedReport/immutable evidence semantics. Do not edit workspace.js/css or renderIrDocs.

- [x] Define bounded Brief/Detailed/Report profiles and verify server/profile validation using mock requests.
- [x] Implement valid text continuation, safe partial states, final synthesis budgeting, cancellation and claim-limit handling; test malformed/truncated tool calls and contradicted assembled reports.
- [x] Add source-based typed run_analysis operations with explicit bounds and lineage; test numerical fixtures, invalid operations, mixed units, assumptions and resource limits.
- [x] Add escaped code rendering and test inert citation markup in code; run relevant assistant/evidence tests.

### Task 4: Coverage UI, actual repair, integration and deployment

**Files:** renderIrDocs region in public/app.js; public/index.html; focused IR UI tests; source/verification docs and screenshots.

**Interfaces:** Use Task1 coverage contract and Task3 library/style paths. Wait for Task3 app.js edits before editing renderIrDocs. Root owns live provider/data access and publishing.

- [x] Publish truthful IR scope/status and paginated/searchable full history with date ordering; verify access beyond record200.
- [x] Audit physical storage and metadata; run bounded existing-data extraction/metadata repair when access permits, preserving backups and lease protection.
- [x] Review all independent changes, resolve findings, run full JavaScript/Python/technical gates and build.
- [ ] Publish PR, verify required checks, deploy and verify screener/editor/research/IR UI. Save measured geometry, actual screenshots, coverage counts and unresolved limitations.
