# Interface refinement

## Purpose

Respond to the user's rejection of the current visual styling with a coherent, restrained research interface. Explain the APIs used by the actual workflows without activating new providers or changing account access.

## Design decisions

- Use a white canvas, neutral ink, clear rules, restrained navy actions, and Inter with system fallbacks. Keep source and formula identifiers in the existing monospace family.
- Reduce the navigation rail to 212 px and group existing routes by purpose. Preserve all destinations, command-palette entries, shortcuts, and watchlist behavior.
- Replace layered title areas, oversized editor chrome, decorative monograms, and repeated boxed summaries with compact, readable controls. The workspace uses the main content area directly.
- Present the morning brief as a company comparison table. Each row shows the latest quarter, sourced revenue and year-over-year change, sourced operating margin and its change in percentage points, and filing activity. An explicit expandable row exposes the full existing changes table, source links, and actions.
- Keep financial content readable at normal desktop scale: body and controls generally 12–14 px; small metadata at least 11 px. Use borders and spacing to establish hierarchy before adding backgrounds or containers.
- Preserve the document page, spreadsheet grid, formula bar, sheet tabs, AI proposal review, and all functional commands. The assistant has a compact context area and can be hidden through the existing state.
- Mobile layouts must retain access to all routes, actions, tables, editors, and the assistant. Tables may scroll within their own region; the page must not overflow horizontally.

## Non-negotiable behavior

- No change to financial calculations, evidence identity, values, units, citation resolution, storage formats, data ingestion, or production data.
- Every displayed financial number continues to use the existing evidence-backed value renderer. Missing values remain missing. Period and availability metadata and incomplete coverage remain visible.
- No new paid provider calls, email deliveries, API grants, secrets, or cloud subscriptions.
- No fabricated controls or claims of complete Microsoft Office parity. UI changes must operate the existing implementation.

## Acceptance

1. The shell and analyst pages share a consistent visual system without gradients, glass, oversized branding, or nested card stacks.
2. The workbench shows substantially more useful grid/document space and has legible, compact commands.
3. The morning brief supports keyboard-accessible expansion and preserves source inspection and company/model/report actions.
4. Existing tests and production build pass. A focused UI regression checks brief expansion and evidence callbacks. Live browser checks verify desktop, narrow layout, and workspace interaction after deployment.
5. The API handoff distinguishes required model services, existing data/storage, optional web research, and optional scheduled email. Exact configuration names are verified against code.
