# Filed Under Research Workspace Implementation Plan

> **For agentic workers:** Use subagent-driven development with disjoint file ownership and a final integrated review.

**Goal:** Deliver accurate financial research, verifiable AI evidence, a professional research workspace and proactive watchlist analysis.

**Architecture:** Keep vanilla JS/D3 and published JSON. Add pure financial/evidence/analyst/workspace modules, tested independently, with thin page adapters and optional authenticated cloud monitoring.

**Tech Stack:** JavaScript, Node 22 API functions, Python ETL, Supabase Postgres/Storage, Vercel.

**Spec:** docs/superpowers/specs/2026-11-04-research-workspace-design.md

## Global Constraints

- Preserve source provenance, local user content, existing charts and deep links.
- Never invent numbers, consensus estimates, supplier relationships, backtest samples or delivery status.
- Do not run production ETL, migrations, deployment builds, or send email from a worker agent.
- No privileged secrets in source, logs, fixtures or reports.
- Financial and assistant behavior receives focused regression tests; cosmetic CSS changes use visual QA.
- Root owns integration, index.html, package/build configuration, monitoring backend, analyst pages and final git operations.

## Review Focus

- Missing capex or incomplete quarter histories produce missing values, not plausible numbers.
- Revisions and filing dates cannot leak future information into an as-of result.
- Fabricated/mismatched evidence IDs and numeric claims are visibly rejected or flagged.
- Existing local sheets/memos survive upgrades and failed persistence attempts.
- Repeated scheduled runs cannot send duplicate notifications or read another user's research.

## Tasks

- [ ] Task 1 Financial pipeline: etl/sec.py, fundamentals.py, jobs.py, common.py and related modules; tests under tests/financials. Correct revenue semantics, EPS derivation, missing capex, aligned TTM, vintage selection, publication completeness and historical revisions. Produce a bounded dry-run/rebuild command.
- [ ] Task 2 Evidence and assistant: public/lib/evidence.js, financial semantics helpers, public/app.js, api/ai.js, api/screen.js, public/pages/screenspec.js; tests under tests/evidence and tests/assistant. Immutable evidence IDs, robust citations, valid bounded tool JSON, evidence-aware assistant tools, safer calculation operations, useful context and recoverable upstream errors.
- [ ] Task 3 Workspace and presentation: public/pages/workspace.js, public/lib/workspace.js, public/workspace.css and public/professional.css; tests under tests/workspace. Office-style document/sheet editing, workbook model builder, source-backed formulas/exports and reversible AI patches. Dedicated module overrides existing Workbench route.
- [ ] Task 4 Proactive analyst: public/lib/analyst.js, public/pages/analyst.js, public/analyst.css; tests under tests/analyst. Watchlist briefing, earnings changes, standing orders, thesis guardian, historical signals, pair comparisons, earnings setup, disclosure comparison, guidance outcomes and evidence-linked report/trade cards.
- [ ] Task 5 Cloud monitoring: api/monitoring.js, api/client-config.js, api/cron/analyst.js and shared Node monitoring helpers, additive migration, browser account/preferences integration. Verified owner, rule validation, deduplicated outbox and configurable email delivery. Test without sending mail.
- [ ] Task 6 Integration: register assets/routes/tools, remove build-time schema replay, add meaningful test/build commands and CI, run the full suite, browser-test representative workflows, resolve reviews, publish a reviewable branch and integrate validated changes.

## Interfaces

- Browser global FU_EVIDENCE exposes record creation/registration/resolution/formatting/rendering/claim checks; module documents exact API for other workers.
- Browser global FU_WORKSPACE exposes model building, document/sheet import/export and staged patches; workspace module supplies its own assistant tool set through existing createChat.
- Browser global FU_ANALYST exposes deterministic changes/briefs/rules/thesis/signals/pairs and tools. Module registers dedicated routes and a company Changes tab.
- Optional window.FU_TOOL_PROVIDERS is an array of functions receiving a trace callback and returning assistant tool definitions; baseTools merges them at request time.
- Existing createChat, ensureCompany, model, findCell, openSource, getJSON, store and IDX remain available to classic page modules.

## Execution record

See docs/superpowers/plans/2026-11-04-progress.md for current ownership, tests and integration rulings.
