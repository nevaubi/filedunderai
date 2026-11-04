# Research workspace implementation report

Implemented in the existing feature worktree. No commits, pushes, deployment, database mutations, outbound messages, or model requests were made by this task.

## Files and integration

New owned assets:

- `public/lib/workspace.js`: deterministic workbook/formula/history/model engine.
- `public/pages/workspace.js`: `FU_PAGES.workbench` replacement, editors, safe document serialization, AI review workflow, Office imports/exports.
- `public/workspace.css`: compact ribbon, spreadsheet, document paper, contextual assistant, empty/loading states, responsive layout.
- `public/professional.css`: restrained shared shell/table/control/source-drawer refinements.
- `tests/workspace/engine.test.mjs` and `tests/workspace/dom.test.mjs`: behavioral, DOM, and real file roundtrip tests.

Root owns script/style registration and dependency/build changes. Load evidence/financial libraries before app.js, workspace engine after app.js, and workspace page after the engine. Load the root-built `/vendor/office.js` before export/import use. Root installed exact MIT dependencies `exceljs@4.4.0`, `docx@9.5.1` and dev test runtime `jsdom@26.1.0`.

The shared assistant function is `chatPanel(host, cfg)`, rather than `createChat`. The workspace uses this existing function and thus its current base/web/evidence tools and response checks.

## Public API

`window.FU_WORKSPACE` exposes:

```js
await FU_WORKSPACE.openModel(ticker, {title?});
// Loads company data, preserves the active workbook in the saved-workbook archive,
// builds a new assumption-driven workbook, navigates to #workbench, returns a clone.

await FU_WORKSPACE.openReport(ticker, {
  title?, markdown?, evidence?, claims?, sections?
});
// evidence is an array of immutable record objects or resolvable evidence IDs.
// sections optionally contain {heading|title, text|content}.
// Existing content is archived; a new editable report is opened in document mode.
// Returns a document clone. Markdown citations resolve to safe source hyperlinks.

FU_WORKSPACE.stageProposal({
  summary,
  cells: [{sheet?, address, value, source?}],
  blocks: [{id?, type?, text}]
});
// sheet may be its ID or name; omitted sheet means active sheet.
// value is a number, string/formula or null. source must resolve to an existing
// immutable evidence record. type is p/h1/h2/h3/blockquote.
// Existing block ID replaces that specific block; omit ID to append.
// [[E:evidence-id]] in block text becomes a source link on apply.
// No edit is applied by this function. Returns a staged-proposal clone.

FU_WORKSPACE.getWorkbook(); // cloned workbook, after flushing pending document edit
FU_WORKSPACE.getSelection(); // {sheet, range, documentText}
await FU_WORKSPACE.exportXlsx();
await FU_WORKSPACE.exportDocx();
FU_WORKSPACE.sanitizeHTML(html);
```

Model/report creation always archives the previous workbook. The active state uses `fu:workspace:v1`; archived workbooks use `fu:workspace:book:<id>` with `fu:workspace:index` for discovery. Storage errors are visible. There is no fabricated cloud sync or collaboration status.

## Workbook behavior

Multiple named sheets, editable title, formula/address bars, row/column headers, sheet tabs, keyboard navigation, Shift range selection, mouse range selection, TSV clipboard copy/paste, fill down/right, number/currency/percent/integer/multiple formats, font/size/emphasis/alignment/fill, row height, column width, column visibility and show-all are implemented. The top header and row labels remain visible when scrolling. Rows/columns can be appended. Layout commands perform real state changes.

The parser uses no JavaScript evaluation. Supported operations are arithmetic, parentheses, percentages, sheet references, quoted sheet names, absolute references in fill, named inputs, SUM, AVG/AVERAGE, MIN, MAX, COUNT, ABS, SQRT, ROUND and FU/PRICE/MCAP/PE. Unsupported functions show `#NAME?`; missing filed data propagates `#N/A`; division by zero shows `#DIV/0!`; circular dependencies show `#CYCLE!`. Blank generic cells may be skipped by aggregates, while an unavailable FU result remains an error throughout dependent formulas. No missing financial fact is coerced to zero.

There are explicit practical limits of 2,000 rows, 100 columns, 50,000 actual cells and 50,000 cells per selected range. Clipboard pastes validate the whole range before mutating it. This is a research-scale grid, with no virtualized rendering or full Excel function library.

Undo/redo keeps up to 60 in-memory complete states, including earlier sources. A persisted audit log retains up to 300 labeled changes across reloads. Editing a filed cell into a user value clears its old provenance; undo restores the previous source. Full undo stacks are not persisted across reloads.

Legacy `fu:sheet` and `fu:memo` are migrated once into the new workspace. A raw `fu:workspace:legacy-backup` is retained, and original keys are never removed or changed. Saved HTML is sanitized before it is displayed.

## Document behavior

The document surface supports paragraphs, title/headings, lists, editable tables, font/size/bold/italic/underline, left/center/right alignment, quoted blocks, page margins, zoom, undo/redo, and literal case-sensitive find/replace. The UI retains and restores the active document text range while toolbar commands execute. Top-level blocks have stable IDs for targeted AI editing.

Contenteditable is implemented with native selection/formatting commands and a strict semantic sanitizer/serializer. Scripts, embedded content, arbitrary attributes, unsafe protocols and unsupported CSS are removed. Pasted document content uses plain text. Imported rich HTML is not accepted as a separate format. This is not a full Word pagination/layout engine; native browser editing commands have browser-specific edge cases, and native Office collaboration, tracked changes, comments, equations and complete styles are outside this implementation.

## Model builder and financial semantics

A one-click model loads up to three filed fiscal years and builds three explicitly labeled forecast columns. History uses linked FU formulas. Separate Assumptions and Sources worksheets preserve editable inputs and filed evidence links/IDs.

The forecast calculates revenue, EBIT, margins, operating cash flow, capex, net income and operating-cash-flow-minus-capex FCF. Growth begins at an explicit neutral 0%; margins and cash-flow/capex ratios carry forward latest filed data. The illustrative 10% discount rate and 2% terminal growth are labeled user assumptions, never consensus or guidance.

Enterprise valuation discounts separately calculated unlevered cash flow:

```
EBIT × (1 − cash tax rate) + D&A − capex − incremental working capital
```

Cash tax, D&A/revenue, incremental working-capital/revenue, net debt and diluted-share assumptions start blank. Therefore enterprise/equity/per-share valuation remains unavailable until the relevant assumptions are supplied. The discount/terminal-growth spread must be positive; a negative spread rejects the valuation. Missing filed history also propagates unavailable values. The generic industrial DCF explicitly flags that financials, REITs and other accounting models require specialist assumptions.

## Native exports and honest imports

XLSX export uses ExcelJS and produces real editable worksheets, standard formulas, named driver inputs, source comments, a Cell evidence worksheet and a Change history worksheet. FU and market helper calls are replaced inside exported formulas with their exact numeric source snapshots; unsupported/missing results remain Excel errors. Their original workspace formulas and source URLs/IDs are preserved in comments/provenance. The workspace custom functions do not install a live Excel add-in.

DOCX export uses docx and preserves headings, paragraphs, inline emphasis/font settings, alignment, lists, tables, margins and safe hyperlinks. A source appendix retains referenced evidence records and URLs. The research JSON backup preserves the complete workbook/document/editable assumptions/source metadata and original imported file payloads.

Imports support XLSX, research JSON, CSV, TSV, TXT and Markdown. XLSX imports preserve values, supported formulas, sheet names and basic font/number formatting. Charts, macros, pivots, merged cells, external links and complete Office format parity are explicitly unsupported. DOCX import is explicitly unsupported. Unsupported formulas remain visible errors. Originals are retained as data URLs with name/type/time in the workbook and can be downloaded from File → Original imports. Imports have a 2 MB limit and check storage capacity before replacing active work.

## AI editing and evidence

The assistant has `read_workspace` and `propose_workspace_changes` tools. Read results are bounded structured projections, with explicit per-section limits, truncation flags and sheet/cell/selection/document/name pagination offsets. No JSON string is sliced into an invalid fragment.

Proposals preview old and new cell/block content and offer Apply/Reject. Staging never mutates content. Apply rejects cell conflicts or changed document state. Existing blocks are replaced individually; the remainder of a document is preserved. Source annotations must resolve to existing immutable evidence. Applying a proposal is an undoable action. Source-linked FU cells open the existing filing drawer.

No actual AI API call was made during implementation. The shared agent's request/response handling and evidence checks are owned and tested by the evidence task.

## Verification

Latest focused run: `node --test tests/workspace/*.test.mjs`: **12 tests pass, 0 fail**. Red regressions were observed before fixes for initial missing engine, invalid valuation spread, filed provenance on manual overrides and unlevered valuation input behavior.

Behavior coverage includes:

- formula recalculation, cross-sheet references/named inputs, missing-data/error propagation, unsupported functions, cycles;
- atomic range paste, translated fill, undo/redo and limits;
- staged edits, old-value preview, conflict detection and double-apply rejection;
- one-time non-destructive migration with raw backups;
- deterministic forecast/model/unlevered valuation arithmetic and required missing assumptions;
- real DOM interactions: selection, proposal application, forecast recalculation, undo/redo and assistant pagination;
- real XLSX export reopened by ExcelJS, verifying formulas, assumptions, source notes, provenance and history sheets;
- real DOCX export reopened as ZIP/XML, verifying headings/emphasis/tables/source appendix and hyperlink relationships;
- malicious HTML removal and retention of supported style properties.

Syntax checks pass. Root owns integrated browser visual QA and the full repository/build verification. Browser visual results should be appended by root after reviewing the deployed/served preview; they are not claimed here.

Full repository verification also completed from this task: `npm test` exited 0 with **75 passed, 0 failed**. This includes analyst, assistant, evidence, monitoring/security, technical indicators and all 12 workspace tests. No failing checks were omitted from this run. Remaining integrated browser visual QA belongs to root.
