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
  - `statements.py` financial statements as filed from the SEC Financial Statement Data Sets (quarterly ZIPs: sub, pre, num, tag): every 10-K/10-Q of covered companies since 2009, keyed by accession, with filed line order, labels, tags, values, periods, axis/member segments and co-registrants. `stdmap.py` holds the standardized templates (industrial, bank, insurer, REIT, utility) and the documented tag mapping.
  - `fundamentals.py` TTM and latest-quarter metrics for screening.
  - `macro.py` Treasury par curve, FRED series (CPI, core CPI, unemployment, payrolls, fed funds, 2s10s), H.15 (feeds `macro.json` and the market brief).
  - Macro series store (`mstore.py`, jobs in `macrojobs.py`): official-source series with point-in-time vintages. Collectors: `frb.py` (Federal Reserve Board release files: H.15, H.4.1, H.8, H.6, G.17, G.19, SLOOS, SCOOS, debt service ratios, Z.1), `nyfed.py` (NY Fed Markets API: reference rates, repo and reverse repo operations, SOMA, primary dealers), `treasury.py` (par curve, bills, real yields), `bls.py` (BLS API v2: CPI components, payrolls by sector, household survey, JOLTS, ECI), `oecd.py` (OECD SDMX: CLI, confidence, CPI, GDP, unemployment, short and long rates for G20 and aggregates), `cftc.py` (Commitments of Traders), `fdic.py` (BankFind aggregates and failures), `mcal.py` (release calendar from the Fed, BLS and CFTC schedules).
  - `insiders.py` Form 4 XML parsing (owner, role, code, shares, price, holdings after, 10b5-1).
  - `news.py`, `summaries.py` recent coverage and written summaries.
  - `screener.py` the screener factor table: 189 fields per company (valuation and 5-year percentiles, growth, quality, balance sheet, Piotroski components, technicals, correlations to market, rates, commodities, the dollar, bitcoin and macro series, insider and filing activity, KPI families, segments), each with a definition, unit, source and per-company provenance.
  - `kpi_taxonomy.py`, `kpistd.py` standardized KPIs: a canonical taxonomy (about 75 measures: comparable sales, organic growth, ARR, net revenue retention, RPO, backlog, book-to-bill, subscribers, ARPU, NIM, CET1, ROTCE, combined ratio, FFO per share, occupancy, RevPAR, load factor, ASMs, production, adjusted EBITDA and EPS, free cash flow, capex, segment revenue and operating income, ...) and the mapping of every company KPI id onto it. Rules on name, unit, category and parent decide most ids; the ones they leave open with a plausible match go to a Fireworks model, and a proposal is kept only if it passes the rule review (candidate list, unit, exclusions, segment qualifiers, value range). Each mapping stores scope (total, segment, variant), confidence, method and the reason. Each reporting-basis id is mapped on its own; per company and measure one total-company id is the compared series and other bases stay separate series, never joined.
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
| | `macro/catalog.json` | Macro page: every series with metadata and statistics, upcoming releases, this week's largest moves |
| | `macro/s/{id}.json`, `macro/snap/{id}.json` | Series history (current values, first-seen dates, revisions) and the publisher's rows for the source panel |
| | `macro/registry.json`, `macro/releases.json`, `macro/state.json` | Series registry, release calendar, Fed file Last-Modified cursor |
| | `companies/{T}.json` | Statements, filing tables, original tables, release, news, summary |
| | `prices/{SYMBOL}.json` | 2y daily + 5d 5-minute bars (`^VIX` → `IDX_VIX`, `CL=F` → `CL_F`) |
| | `filings/{accession}.html.gz` | Full filed documents for the source panel (scripts removed, EDGAR base URL added), gzipped and inflated in the browser |
| | `logos/{T}.png` | Company logos (497 of 500) |
| | `insiders/{T}.json` | Form 4 transactions, open-market summaries, largest traders |
| | `sectors.json` | Sector and industry overview: headline aggregates, returns, rotation, macro correlations, the SIC mapping, KPI and segment rules |
| | `sectors/{slug}.json` | One sector or industry: quarterly aggregates since 2008, valuation vs 10-year range, performance, breadth, macro sensitivity, KPIs, constituents, segment exposure, insiders, filings |
| | `sectors/{slug}.comp.json` | Per-company inputs (with accessions) behind every aggregate, for the derivation panel |
| | `sectors/notes.json` | Weekly sector notes (numbers inserted from computed facts) |
| | `kpistd/taxonomy.json` | Canonical KPI taxonomy with coverage per measure and industry |
| | `kpistd/map.json` | Per company: the compared KPI id for each canonical measure, ids of other bases, unit factors |
| | `kpistd/t/{T}.json` | One company's mapping (scope, role, confidence, method, reason per KPI id) and its standardized series with source documents |
| | `kpistd/series/{id}.json` | Every company's series for one canonical measure (quarterly and annual points, each with document, page and quote) |
| | `kpistd/latest.json` | Latest value and year-over-year change per measure and company (company panels) |
| | `screener.json` | Screener field catalogue (id, group, name, unit, definition, source, distribution) and one compact row per company |
| | `screener/src/{T}.json` | Provenance per screener field: the filed facts behind it (statement line, period, value, accession, form, filing date, XBRL concept), price dates and closes, KPI document ids, Form 4 windows |
| | `screener/corr.json` | Pairwise one-year daily return correlations (int8, upper triangle) for correlation clusters |
| | `tech/bars/{SYMBOL}.json` | Daily split-adjusted OHLCV since 2008-01-01 (or listing) plus cash dividends, for every stock and market symbol; source URL and retrieval time in `meta` |
| | `tech/events/{T}.json` | EDGAR 8-K (with items), 10-K and 10-Q filings since 2008, and parsed open-market Form 4 trades |
| | `tech/signals.json`, `tech/breadth.json`, `tech/states.json` | S&P 500 signal statistics (forward 5/20/60-day returns vs base rate), daily breadth since 2009, current technical state per name |
| | `statements/{T}/index.json`, `statements/{T}/{accession}.json.gz`, `statements/{T}/std.json.gz`, `statements/std_map.json` | As-filed statements per filing (gzipped, inflated in the browser), standardized values per filing, the template mapping |
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
| `/api/cron/kpistd` | 04:05, 10:05, 16:05, 22:05 | Maps the KPI ids of companies whose KPI definitions changed since the last run (rules, then reviewed model proposals), republishes their `kpistd/t` files and the series files they touch; 230s budget, unfinished companies continue on the next run |
| `/api/cron/screener` | 22:55 daily | Rebuilds `screener.json` after the price runs: extends the close history from the prices files, re-extracts facts only for companies with a new report, recomputes every field, correlations and provenance |
| `/api/cron/technicals?part=N&parts=4` | 22:05-22:26 Mon-Fri | Appends the day's bars from `prices/` to `tech/bars/` (no extra Yahoo calls; a split is detected from the overlap and rescales history), refreshes `tech/events/`, computes signals, breadth and state for a quarter of the names |
| `/api/cron/technicals?merge=1` | 23:10 Mon-Fri | Combines the parts into `tech/signals.json`, `tech/breadth.json`, `tech/states.json` |
| `/api/cron/insiders` | 15:30, 23:30 | Parses new Form 4 filings into `insider_transactions`, rewrites `insiders/{T}.json` and `meta/insiders.json` |

| `/api/cron/mdata?source=…` | daily per source (calendar 06:15, oecd 09:20, bls 13:50, nyfed 18:25, cftc 20:55, frb_monthly 21:15, frb_weekly 22:45, frb_daily and treasury 23:15/23:35 weekdays, fdic Mondays 09:40) | Macro series store. Fed files are only re-read when their Last-Modified changes |

Macro store: `macro_series` holds one row per series (publisher, release, series code, units, frequency, adjustment, geography, URLs). `macro_series_obs` keeps every vintage: `macro_ingest()` inserts a row only when a value is new or differs from the latest stored one, with `vintage` = the publisher's release date when the source states it (Fed file Last-Modified) and the retrieval date otherwise; `macro_series_latest` is the current view. Until the tables exist the jobs run storage-only and backfill the tables on their first run after.
| `/api/cron/statements` | 09:17 daily | Loads any new or republished quarter of the SEC Financial Statement Data Sets (resumes a partial one within a 250s budget) |

Every run is logged in `etl_runs`; the latest runs are published to `meta/status.json` for the Data status page.

Vercel Hobby runs each cron entry once a day within the scheduled hour, which is why the jobs are split into several entries.

## Environment

Set in Vercel (the Supabase ones come from the integration):

`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `POSTGRES_URL_NON_POOLING`, `FIREWORKS_API_KEY`, `TAVILY_API_KEY`, `FIRECRAWL_API_KEY`, `CRON_SECRET`, `SEC_USER_AGENT` (SEC asks for `Name email@domain`).

Optional: `BLS_API_KEY` (raises the BLS API quota), `FU_ASSISTANT_MODEL`, `FU_ASSISTANT_FALLBACK`, `FU_SUMMARY_MODEL`, `FU_SUMMARY_FALLBACK`.

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
python -m etl.run kpistd                         # standardized KPI mapping (incremental); --full-history remaps every company, --no-llm rules only
python -m etl.run screener --full-history        # screener table; --full-history refetches 10y closes and every company
node scripts/test_screen_prompts.mjs             # screen agent accuracy on tests/screener_prompts.json
python -m etl.run technicals --backfill          # tech/bars history since 2008 (resumable; skips symbols already stored)
python -m etl.run technicals --events            # tech/events from EDGAR submissions (resumable)
python -m etl.run technicals                     # all four parts, then the merge
node --test tests/technicals/*.test.cjs          # indicator tests (TA-Lib reference values and worked examples)
python3 tests/technicals/test_parity.py          # JS and Python signal definitions find identical events
python -m etl.run mdata --source nyfed --full-history   # macro store: one source, full backfill (sources in etl/macrojobs.py)
python -m etl.run statements --defer --workdir /tmp/fsds   # as-filed statements, every quarter not yet loaded
python -m etl.run statements --quarters 2026q3 --force     # reload one quarter
```

Adding a company: append it to `etl/universe.py` and run `python -m etl.run companies --tickers NEW` then `python -m etl.run prices`.

## Source panel

Clicking a value opens the actual filed document in a sandboxed frame and scrolls to it. The panel finds the inline XBRL element whose concept and context period match the value; for filings without inline XBRL it finds the printed figure. Latest 10-K and 10-Q copies are published by the ETL; any other filing is fetched once by `/api/filing?accn=` (only accessions present in the site's data), stored, and served from storage after that.

## API guard

`/api/screen` (the screener agent) returns a validated screen spec, never numbers; it uses `FU_SCREEN_MODEL` / `FU_SCREEN_FALLBACK` when set.

`/api/ai`, `/api/screen`, `/api/search`, `/api/scrape` and `/api/filing` only answer requests from filedunder.ai or this project's Vercel domains, and count calls per IP and globally per day in `api_usage` via `bump_usage()`.
