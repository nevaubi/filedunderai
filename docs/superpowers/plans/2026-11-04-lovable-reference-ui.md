# Lovable Reference UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Match the user-selected Filed Under reference's warm paper, serif-title and compact-control visual system across the existing application.

**Architecture:** Keep existing HTML/CSS/JavaScript and data/evidence engines. Change the shared presentation tokens, shell and scoped company layout, then align analyst pages and workspace chrome.

**Tech Stack:** Existing browser JavaScript, CSS, jsdom tests, esbuild, Vercel.

**Spec:** `docs/design/lovable-reference/DESIGN.md`

## Global Constraints

- Preserve existing page IDs/hash routes, keyboard shortcuts, watchlist state, source hooks, editor state, and numerical formatting semantics.
- No model calls, emails, provider configuration changes, or database migrations.
- Scope display-font rules to app page titles. Never apply the serif title treatment to user-authored document headings or embedded source documents.
- Define `--topbar-height: 48px` once and consume it in the workspace viewport calculation.
- Reference transcripts and prices are visual context only; use the existing application's actual data.

## Review Focus

- Long company names and missing profile fields must wrap or show an explicit missing state without breaking filing actions.
- Company/header price values must come from existing data, retain as-of context, and never present unavailable values as zero.
- A hidden assistant and pending editor input must remain intact when only the visual system changes.
- Source clicks and brief disclosures must remain separate interactions with exact original evidence.
- Narrow screens must retain reachable navigation and local table/paper scrolling without page-width overflow.

### Task 1: Shared tokens and shell

**Files:** `public/professional.css`, `public/index.html`, shell functions in `public/app.js`, targeted shell test if behavior changes.

**Interfaces:** Existing `PAGES`, `renderRail()`, `buildShell()`, `last()`, `dayChg()`, and hash routes. Produces shared design tokens, 240px rail, 48px top bar, Research/Markets/Workspace groups, compact actual-data quote tape and assistant navigation.

- [x] Apply extracted fonts, colors, typography, gutters, borders and button/tab styles.
- [x] Keep the rail's brand/footer fixed within its height and make its navigation/watchlist region scrollable.
- [x] Restore compact logo/ticker/price/change watchlist rows using existing data and original links.
- [x] Test shell links, missing price data and grouped navigation if rendering behavior changes.

### Task 2: Company composition and page-specific consistency

**Files:** `pCompany()` in `public/app.js`, new `public/company.css`, `public/analyst.css`, `public/workspace.css`, targeted company test if interaction structure changes.

**Interfaces:** Existing company model/source actions and `FU_COTABS` renderers; shared tokens from Task 1. Root adds the new stylesheet link. This task owns only the `pCompany()` region of `app.js`; root owns shell functions.

- [x] Match reference company identity, quote/actions, tabs and compact metric strip with scoped classes. Keep long profile details accessible.
- [x] Align analyst headings, tables, forms, cards, labels, disclosure and missing states to the extracted design.
- [x] Align workspace outer controls/assistant with shared tokens; use the top-bar variable for height and preserve editing-content styles.
- [x] Run relevant existing UI tests and add meaningful company behavior coverage if needed.

### Task 3: Review, deployment and visual verification

**Files:** Design/verification documentation and screenshots only, plus fixes from review.

- [x] Independently review task diffs for design compliance and functional regressions.
- [x] Run the complete JavaScript suite and production build; use required CI gates before merging.
- [x] Publish a reviewable branch/PR, verify checks, merge and inspect the production result under the existing authorization.
- [x] Check company, brief, source drawer, assistant and editors in the live UI. Save screenshots of representative changed screens and report limitations.

Production results and captured screenshots: `docs/verification/2026-11-04-reference-ui/REPORT.md`.
