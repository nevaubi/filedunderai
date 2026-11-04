# Filed Under

Source-attributed research on US equities. Financials come from SEC filings and every number links to the filing, table and XBRL fact it came from. Prices, macro series, filings, news and a research assistant sit around that core.

Coverage today: the S&P 500 (S&P 100 flagged in the index).

## How it fits together

```
                ┌───────────────────────────── Vercel ─────────────────────────────┐
 browser ──────▶│ public/ (index.html, app.js, app.css)                             │
   │            │ api/ai.js · api/search.js · api/scrape.js   (Node, guarded)       │
   │            │ api/cron/*.py  ── scheduled ETL, imports etl/                     │
   │            └──────────────────────────────┬───────────────────────────────────┘
   │                                           │ service role
   │   public JSON (CDN)                       ▼
   └────────────────────────────▶  Supabase: Storage `public-data` (site bundles)
                                             Storage `raw` (archived source docs)
                                             Postgres (companies, filings, facts, prices, insiders, macro, news, runs)
```

- **`etl/`** pulls and normalizes data and writes both Postgres rows and the JSON bundles the site reads.
  - `sec.py` submissions, XBRL company facts, statement series with filing attribution, per-filing statement tables, derived quarters.
  - `docs.py` original 10-K/10-Q statement tables with inline XBRL anchors, earnings release text (8-K EX-99.1).
  - `prices.py` daily and 5-minute bars, return/volatility/beta stats.
  - `fundamentals.py` TTM and latest-quarter metrics for screening.
  - `macro.py` Treasury par curve, FRED series (CPI, core CPI, unemployment, payrolls, fed funds, 2s10s), H.15.
  - `insiders.py` Form 4 XML parsing (owner, role, code, shares, price, holdings after, 10b5-1).
  - `news.py`, `summaries.py` recent coverage and written summaries.
  - `screener.py` the screener factor table: 189 fields per company (valuation and 5-year percentiles, growth, quality, balance sheet, Piotroski components, technicals, correlations to market, rates, commodities, the dollar, bitcoin and macro series, insider and filing activity, KPI families, segments), each with a definition, unit, source and per-company provenance.
  - `calls.py`, `callparse.py` earnings-call intelligence: transcripts and prepared remarks from `ir_documents` parsed into speakers, passages, prepared/Q&A sections and analyst exchanges (deterministic, with a model-read label roster only when the labels are ambiguous), then one model analysis per call (themes on a fixed taxonomy with sentiment and management confidence, guidance statements, analyst concerns, answers that were not direct, changes vs the previous call). Every item keeps its quote as character offsets into the stored document text; items whose quote is not found verbatim are dropped. Model results are cached in `raw/calls/llm/` by input hash.
  - `jobs.py` the jobs: `companies_job`, `prices_job`, `macro_job`, `news_rotation_job`, `build_index`, `build_feed`.
- **`public/`** is a dependency-free single-page app (D3 for charts). It reads `index.json`, `market.json`, `macro.json`, `feed.json`, `companies/{TICKER}.json` and `prices/{SYMBOL}.json` from the public bucket.
- **`db/schema.sql`** is applied on every deploy by `scripts/migrate.mjs` (idempotent). Public tables are read-only for `anon` via RLS.

## Storage layout

| Bucket | Path | Contents |
|---|---|---|
| `public-data` | `index.json` | Universe with prices and fundamentals for the screener |
| | `market.json` | Index, ETF, rates and commodity closes |
| | `macro.json` | Curve, FRED series, fed funds, macro news and brief |
| | `feed.json` | Latest filings across the universe |
| | `companies/{T}.json` | Statements, filing tables, original tables, release, news, summary |
| | `prices/{SYMBOL}.json` | 2y daily + 5d 5-minute bars (`^VIX` → `IDX_VIX`, `CL=F` → `CL_F`) |
| | `filings/{accession}.html.gz` | Full filed documents for the source panel (scripts removed, EDGAR base URL added), gzipped and inflated in the browser |
| | `logos/{T}.png` | Company logos (497 of 500) |
| | `insiders/{T}.json` | Form 4 transactions, open-market summaries, largest traders |
| | `sectors.json` | Sector and industry overview: headline aggregates, returns, rotation, macro correlations, the SIC mapping, KPI and segment rules |
| | `sectors/{slug}.json` | One sector or industry: quarterly aggregates since 2008, valuation vs 10-year range, performance, breadth, macro sensitivity, KPIs, constituents, segment exposure, insiders, filings |
| | `sectors/{slug}.comp.json` | Per-company inputs (with accessions) behind every aggregate, for the derivation panel |
| | `sectors/notes.json` | Weekly sector notes (numbers inserted from computed facts) |
| | `screener.json` | Screener field catalogue (id, group, name, unit, definition, source, distribution) and one compact row per company |
| | `screener/src/{T}.json` | Provenance per screener field: the filed facts behind it (statement line, period, value, accession, form, filing date, XBRL concept), price dates and closes, KPI document ids, Form 4 windows |
| | `screener/corr.json` | Pairwise one-year daily return correlations (int8, upper triangle) for correlation clusters |
| | `calls/{T}.json` | Calls per company: event, fiscal label, speakers with talk share, hedging and tone, Q&A exchanges, themes, guidance, changes, each quote with document offsets |
| | `calls/{T}/{call_id}.json` | Passage text per call (speaker, section, offsets, page) for the Q&A browser and search |
| | `calls/themes.json`, `calls/themes/{theme}.json` | Theme mentions by sector and quarter, and every mention with its quotes |
| | `calls/people.json`, `calls/coverage.json` | Executives (per-call statistics), analysts and firms (companies covered, question topics); calls stored per company |
| | `meta/*.json` | Job caches and status (fundamentals, price stats per part, rotation and check cursors, insiders index, pipeline status) |
| `raw` (private) | `screener/closes.json.gz`, `screener/fund.json.gz`, `screener/kpi.json.gz` | Screener caches: 6 years of split-adjusted daily closes and split dates, extracted quarterly facts per company (re-extracted when a new 10-K/10-Q arrives), KPI and segment extracts |
| | `sec/companyfacts/*.json.gz`, `sec/filings/{accn}/*.htm.gz`, `sec/releases/*.md.gz` | Archived source documents, indexed in `documents` |

## Schedules (UTC, `vercel.json`)

| Job | When | What |
|---|---|---|
| `/api/cron/prices?part=N&parts=8` | 21:40-21:54 Mon-Fri | Bars and stats for an eighth of the stocks each, with a 250s budget (part 0 adds index, rate and commodity symbols); every part rebuilds the index |
| `/api/cron/macro` | 12:10 daily | Curve, FRED, fed funds, macro news and brief |
| `/api/cron/companies` | six runs a day | Checks SEC submissions for all 500 (rotating cursor). New 10-K/10-Q/8-K: full reprocess, up to 15 per run. Other new filings (Form 4, 144, proxies) are written straight to `filings` |
| `/api/cron/news` | 13:00, 19:00 | Rolling news + summary refresh, 14 companies per run |
| `/api/cron/segments` | 12:50, 00:50 | Recomputes XBRL segment tables for companies with a new 10-K or 10-Q |
| `/api/cron/ir` | 14:40, 22:40, 04:40 | Collects IR documents: companies that just reported earnings first, then a rolling cursor (8 per run) |
| `/api/cron/sectors?step=prices` | 21:05 Mon-Fri | Refreshes monthly price histories (20 years, with splits) older than six days |
| `/api/cron/sectors` | 22:35 Mon-Fri | Rebuilds every sector and industry file from facts, prices, macro_observations, FRED, KPIs, segments, insiders, filings; writes `sector_metrics` |
| `/api/cron/sectors?step=notes` | 12:15 Mon | Weekly sector notes, validated so every figure comes from the data (`sector_notes`) |
| `/api/cron/screener` | 22:55 daily | Rebuilds `screener.json` after the price runs: extends the close history from the prices files, re-extracts facts only for companies with a new report, recomputes every field, correlations and provenance |
| `/api/cron/calls` | 15:20, 23:20, 05:20 | New transcripts and prepared remarks after the IR runs: parse, analyse (last 8 quarters first, then older calls), publish `calls/*`, copy rows into `calls`, `call_speakers`, `call_turns`, `call_exchanges`, `call_items` |
| `/api/cron/insiders` | 15:30, 23:30 | Parses new Form 4 filings into `insider_transactions`, rewrites `insiders/{T}.json` and `meta/insiders.json` |

Every run is logged in `etl_runs`; the latest runs are published to `meta/status.json` for the Data status page.

Vercel Hobby runs each cron entry once a day within the scheduled hour, which is why the jobs are split into several entries.

## Environment

Set in Vercel (the Supabase ones come from the integration):

`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `POSTGRES_URL_NON_POOLING`, `FIREWORKS_API_KEY`, `TAVILY_API_KEY`, `FIRECRAWL_API_KEY`, `CRON_SECRET`, `SEC_USER_AGENT` (SEC asks for `Name email@domain`).

Optional: `FU_CALLS_MODEL`, `FU_CALLS_FALLBACK`, `FU_ASSISTANT_MODEL`, `FU_ASSISTANT_FALLBACK`, `FU_SUMMARY_MODEL`, `FU_SUMMARY_FALLBACK`.

## Running the ETL locally

```bash
pip install beautifulsoup4 lxml
export FU_ENV_FILE=.env.local        # same keys as above
python -m etl.run prices --full-history
python -m etl.run macro
python -m etl.run companies --workers 5          # all 500 companies
python -m etl.run companies --changed            # only those with new reports
python -m etl.run insiders                       # Form 4 backfill / refresh
python -m etl.run companies --tickers AAPL MSFT  # a subset
python -m etl.run news                           # next slice of the news rotation
python -m etl.run sectors --step prices          # monthly price cache for sector valuation history
python -m etl.run sectors                        # sector and industry files (etl/sectors.py, mapping in etl/sector_map.py)
python -m etl.run sectors --step notes           # weekly sector notes
python -m etl.run calls --recent                 # call transcripts: parse everything, analyse the last 8 quarters
python -m etl.run calls                          # then older calls; --mode publish | db for the cross-company files or the tables
python -m etl.run screener --full-history        # screener table; --full-history refetches 10y closes and every company
node scripts/test_screen_prompts.mjs             # screen agent accuracy on tests/screener_prompts.json
```

Adding a company: append it to `etl/universe.py` and run `python -m etl.run companies --tickers NEW` then `python -m etl.run prices`.

## Source panel

Clicking a value opens the actual filed document in a sandboxed frame and scrolls to it. The panel finds the inline XBRL element whose concept and context period match the value; for filings without inline XBRL it finds the printed figure. Latest 10-K and 10-Q copies are published by the ETL; any other filing is fetched once by `/api/filing?accn=` (only accessions present in the site's data), stored, and served from storage after that.

## API guard

`/api/screen` (the screener agent) returns a validated screen spec, never numbers; it uses `FU_SCREEN_MODEL` / `FU_SCREEN_FALLBACK` when set.

`/api/ai`, `/api/screen`, `/api/search`, `/api/scrape` and `/api/filing` only answer requests from filedunder.ai or this project's Vercel domains, and count calls per IP and globally per day in `api_usage` via `bump_usage()`.
