# Interface refinement implementation plan

Spec: `docs/superpowers/specs/2026-11-04-interface-refinement.md`

## Global constraints

No financial or evidence calculation changes. Preserve source callbacks, periods, missing-data states, local workbooks, document editing, review/apply/reject AI actions, routes, and shortcuts. No new paid API calls, email delivery, grants, or credentials. Use existing vanilla JavaScript and CSS, with no framework or dependency migration.

## Task 1 — Shared shell and analyst brief

Owner: primary agent. Files: `public/professional.css`, `public/index.html`, `public/app.js`, `public/analyst.css`, `public/pages/analyst.js`, focused analyst UI test.

- Introduce the common typography and neutral tokens in professional.css; remove its old cosmetic shadow overrides.
- Use a 212 px rail and compact 56 px header. Group navigation without losing existing route metadata, and set accessible current-page state.
- Flatten analyst headers, metrics, forms, tables, and notes; replace morning-brief company cards with an accessible expandable table using the same evidence records.
- Keep the workspace container full width with page padding zero, coordinated with Task 2.
- Add a meaningful regression for expansion, retained source interactions, and missing coverage. Verify syntax and targeted tests.

## Task 2 — Workspace layout and controls

Owner: delegated implementer. Files: `public/workspace.css` and only presentation-related sections of `public/pages/workspace.js`.

- Remove the FU monogram and verbose breadcrumb/slogan; use the editable workspace title, save status, model action, and existing assistant visibility state.
- Use compact mode and ribbon tabs, a practical 60–72 px command band including group labels, and legible 12–13 px commands. Avoid 8–10 px labels and unnecessary padding.
- Use flat, neutral surfaces and a quiet document canvas. Preserve paper dimensions, zoom, formula/grid sizing, existing explicit table width, and print behavior.
- Make the assistant heading, context, suggestions, proposal, and composer simpler and readable. Preserve all handlers, local persistence, selection, model logic, and exports.
- Root sets `--f-ui` to Inter/system, the shell rail to 212 px, top header to 56 px, and `.page:has(.ws-workspace)` padding to zero. Workspace fills the remaining viewport on desktop; mobile navigation adds height below 860 px.
- Verify JavaScript syntax and run existing relevant workspace tests. Report changed files and any remaining limitations; do not commit or publish independently.

## Task 3 — API handoff and integrated verification

Owner: primary agent with independent read-only audit/review.

- Record actual provider and configuration requirements in a repo document without credential values.
- Review both implementation tasks against the spec and actual changed code.
- Run the existing suite and build; verify actual live rendering and interactions through the approved browser connection.
- Publish the reviewable branch through the existing GitHub workflow; check CI and Vercel status before integration, then inspect the deployed interface. Report material limitations accurately.
