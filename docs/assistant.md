# Assistant panel

The top-right **Assistant** button (or ⌘J / Ctrl+J) opens a research agent on every page. It plans its work, calls tools against Filed Under's data and the web, asks a clarifying question when a choice changes the work, and answers in markdown with citations that open the source drawer.

Files:

| File | Role |
|---|---|
| `public/lib/assistant-loop.js` | `FU_AGENT.runAgent`: the agent loop (browser tool execution, `/api/ai` model steps) |
| `public/lib/assistant-tools.js` | `FU_ASSISTANT_TOOLKIT`: built-in tools, the code sandbox and the plugin registry |
| `public/lib/assistant-panel.js` | `FU_ASSISTANT`: panel UI, markdown/citations/charts, filing-quote opener, history, `#research` view |
| `public/assistant.css` | Panel, docking and drawer coexistence styles |
| `api/ai.js` | Fireworks chat completions relay (guard, limits, tool-spec validation, agent profiles) |
| `api/search.js` | Tavily web/news/finance search and SEC EDGAR full-text search (`source: "edgar"`) |
| `api/scrape.js` | Firecrawl page reader plus `mode: "map" | "crawl" | "extract"` |

## Layout

- **Docked** (default): fixed to the right edge. `html.asst-docked` adds `padding-right: var(--asst-w)` to `.app`, so the page reflows instead of being covered. Drag the panel's left edge to change the width (340 to 720 px, persisted).
- **Source drawer with a docked panel**: on screens 1100 px and wider the drawer opens to the panel's left (`right: var(--asst-w)`), so a cited passage and the answer are visible side by side; the closed drawer sits under the panel. Narrower, the drawer opens over the panel as before.
- **Floating**: the dock button pops the panel out as a window that can be dragged by its header and resized from the bottom-right corner. It stays above the drawer.
- **Phones** (≤ 860 px): full screen; the source drawer opens above it.
- State (`open`, `docked`, `deep`, `width`, floating `box`) is stored in `localStorage["fu:assistant:ui"]`; every read and write is wrapped in try/catch and the panel works without storage.

## Agent loop

`FU_AGENT.runAgent({ messages, tools, deep, request, signal, stopped, onUpdate, onAsk })`, ported from the reference loop to OpenAI-compatible tool calling:

- **Modes.** Fast: up to 14 model steps (`profile: "agent_fast"`). Deep: up to 32 steps (`profile: "agent_deep"`) and one self-review pass once at least three tools ran: the draft is sent back with a review instruction; the model either does more work or restates the final answer.
- **Plan.** `update_plan` (goal, steps with todo/doing/done/dropped, notes) is handled by the loop and rendered as a live plan card with progress. Earlier plan notes are passed to follow-up turns.
- **Clarifying questions.** `ask_user` pauses the loop and renders a "Needs your input" card with up to four option chips; typing a reply also answers. Stop resolves a pending question.
- **Parallel tools.** All tool calls in a step run concurrently; replies are appended in call order.
- **Validation.** Arguments must parse as a JSON object and pass the tool's JSON schema (types, required, enums, bounds, array sizes); unknown keys are dropped. Invalid or unknown calls are not executed and the model gets an error reply. Each tool run is capped at 95 s and its output at 14,000 characters.
- **Retries.** 429, 408, 5xx and network errors retry up to three attempts with 1.2 s / 2.4 s backoff. 413 compacts the context and retries. Other 4xx errors stop the turn.
- **Compaction.** Older tool outputs beyond the latest eight messages are shortened when the request passes 450 KB; evidence ids found in them are kept so earlier citations still resolve.
- **Restart hint.** After two consecutive steps where every tool failed, the model is told to update its plan and change approach.
- **Budget.** Each request carries `profile`, `round`, `remainingCalls`, `maxTokens`, `remainingTokens` and `timeoutMs`, validated by `FU_RESEARCH_RUNTIME.requestBudget` on the server. The final step sends no tools and asks for the best answer with gaps noted.
- **Reasoning summaries.** `reasoning_content` / `reasoning` fields or `<think>` blocks become collapsible "thought" steps and are removed from the answer.

The server stays a stateless relay: it checks the origin and daily per-IP/global caps (`guard`), body size, message roles, tool definitions (at most 64 well-formed functions) and the budget, then calls Fireworks (kimi-k3, falling back to deepseek-v4p1-flash). Keys never reach the browser. Each model step counts against the `ai` daily cap (300 per IP), so a Deep question can use up to about 34 of them.

## Citations and provenance

- Financial values from `get_financials` / `get_segments` carry evidence ids. The model writes `[[E:ev_…]]` after the number; the chip opens the existing source drawer on the filed value (same handler as the rest of the site). After an answer completes, displayed numbers next to citations are checked with `validateMarkdownNumbers`; mismatches are struck through and a banner says so.
- Filing text is cited as `[10-Q](filing:TICKER:ACCESSION "verbatim phrase")`. The chip opens the filing in the drawer, finds the phrase (whitespace, quote and dash differences normalized, across inline markup), highlights it with `.fu-hit` and scrolls to it. If the phrase is not found, the drawer says the statement is unverified.
- Web pages and Form 4 filings are normal links. Step rows show source chips with favicons; the answer footer shows a deduplicated source list, an "AI-generated" label, the date and the citation check count. Uncited statements are labeled unverified.
- Charts: a fenced block with language `chart` and JSON `{type: "line"|"bar", title, unit: "$"|"%"|"", x, series, data}` is drawn with d3 (at most 80 points and 5 series).

## Built-in tools

| Tool | Data |
|---|---|
| `get_coverage`, `screen_companies` | `index.json` screener fields |
| `get_financials` | company bundles via `ensureCompany` + `model()`, statement rows or ratios, with evidence ids |
| `get_segments` | `segments/TICKER.json` via `ensureSegments` / `segCell`, with evidence ids |
| `list_filings`, `read_filing` | bundle filing list; filing HTML via `loadFilingDoc` (public bucket cache, then `/api/filing`) |
| `search_filings` | `/api/search` with `source: "edgar"` (efts.sec.gov full-text search, SEC user agent) |
| `get_price_history`, `get_technicals`, `get_quotes` | stored daily bars via `ensurePrices` / `priceStats` |
| `get_insider_trades` | `insiders/TICKER.json` (Form 4) |
| `get_macro`, `get_news`, `get_earnings_reactions` | `macro.json`, bundle news, 8-K Item 2.02 reactions |
| `web_research`, `read_webpage`, `web_map`, `web_crawl`, `web_extract` | `/api/search` (Tavily) and `/api/scrape` (Firecrawl read/map/crawl/extract) |
| `get_page_context`, `navigate_to` | current route, title, visible text; in-app hash routes and company tabs |
| `run_code` | model-written JavaScript in a throwaway Web Worker: network, storage and nested-worker APIs removed from the global prototype chain, 8 s limit, data passed in (`data.universe`, `data.companies[T]`, `data.prices[S]`, `stats`) |
| `inspect_evidence`, `update_plan`, `ask_user` | evidence record lookup; loop-handled plan and question |

## Plugin tools: `window.FU_ASSISTANT_TOOLS`

Other modules (supply chain, estimates, insiders/government, quant) register tools at load time. Registration can happen before or after the assistant scripts load; the panel reads the registry at the start of every turn.

```js
window.FU_ASSISTANT_TOOLS = window.FU_ASSISTANT_TOOLS || [];
window.FU_ASSISTANT_TOOLS.push({
  name: "get_supply_chain",              // /^[a-z][a-z0-9_]{1,63}$/; built-in names win on collision
  label: "Mapped supply chain",           // past-tense label in the step timeline
  kind: "tool",                           // icon hint: "tool" | "web" | "filing" | "code" | "nav"
  description: "Named customers and suppliers from 10-K text, with share of revenue and accession.",
  parameters: {                           // JSON schema (type "object"); arguments are validated before run()
    type: "object",
    properties: { ticker: { type: "string" }, relation: { type: "string", enum: ["customer", "supplier"] } },
    required: ["ticker"],
  },
  pages: ["company", "supply"],           // optional: offered only on these route ids (#company-…, #supply…)
  async run(args, { signal }) {           // honor signal for Stop
    return {
      output: "text the model reads (cite accessions as filing:TICKER:ACCESSION)",
      summary: "12 links for NVDA",       // one line in the step row
      sources: [{ url: "https://www.sec.gov/…", title: "NVDA 10-K" }],   // favicon chips + source list
      refs: [{ ticker: "NVDA", accn: "0001045810-26-000021", label: "10-K" }],  // filing chips that open the drawer
    };
  },
});
// Optional page-aware suggestions for the empty state:
window.FU_ASSISTANT?.registerSuggestions("supply", ["Explain the biggest risks on this map"]);
```

`run` may also return a plain string. Existing `window.FU_TOOL_PROVIDERS` providers (`trace => [{ name, description, inputSchema, execute }]`) are adapted automatically.

## Handoff

```js
window.dispatchEvent(new CustomEvent("fu:research-request", { detail: "Stress-test this thesis…" }));
```

Opens the panel and prefills the prompt for the user to review. It never submits and never discards an answer in progress. `requestResearch(prompt)` in `app.js` (company brief buttons, theses, the command palette) uses it.

## History

Conversations are stored in this browser under `localStorage["fu:assistant:history:v1"]` (40 conversations, about 220 KB each; tool outputs are trimmed before saving and the oldest are dropped if storage is full). `#research` lists, searches, reopens and deletes them; the previous chat page remains at `#research-classic`. There is no server-side history: no existing authenticated table or policy covers conversations (the monitoring settings validator rejects unknown keys), and this work adds no tables.

## Testing

- `node scripts/test.mjs` runs the unit tests: `tests/assistant/panel-loop.test.mjs` (loop), `panel-api.test.mjs` (routes with mocked providers), `panel-ui.test.mjs` (panel in the real script order under JSDOM).
- `python3 tests/assistant/e2e/run_e2e.py --port 8106 [--shots DIR]` builds nothing itself: run `node scripts/build.mjs` first. It serves `dist/` with `tests/assistant/e2e/mock_api.py`, a scripted stand-in for `/api/ai`, `/api/search` and `/api/scrape`, and drives Chrome through plan, parallel tools, ask_user, cited answers, the filing-quote drawer, Deep self-review, Stop, floating drag/resize persistence, handoff, history and mobile. Public data comes from the production bucket through the local proxy.
- Live Fireworks, Tavily, Firecrawl and EDGAR full-text responses are not exercised by these tests; their request and response handling is tested against documented shapes only.
