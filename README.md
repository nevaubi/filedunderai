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
| | `insiders/{T}.json` | Form 4 transactions, open-market summaries, largest traders |
| | `meta/*.json` | Job caches and status (fundamentals, price stats per part, rotation and check cursors, insiders index, pipeline status) |
| `raw` (private) | `sec/companyfacts/*.json.gz`, `sec/filings/{accn}/*.htm.gz`, `sec/releases/*.md.gz` | Archived source documents, indexed in `documents` |

## Schedules (UTC, `vercel.json`)

| Job | When | What |
|---|---|---|
| `/api/cron/prices?part=N&parts=8` | 21:40-21:54 Mon-Fri | Bars and stats for an eighth of the stocks each, with a 250s budget (part 0 adds index, rate and commodity symbols); every part rebuilds the index |
| `/api/cron/macro` | 12:10 daily | Curve, FRED, fed funds, macro news and brief |
| `/api/cron/companies` | six runs a day | Checks SEC submissions for all 500 (rotating cursor). New 10-K/10-Q/8-K: full reprocess, up to 15 per run. Other new filings (Form 4, 144, proxies) are written straight to `filings` |
| `/api/cron/news` | 13:00, 19:00 | Rolling news + summary refresh, 14 companies per run |
| `/api/cron/insiders` | 15:30, 23:30 | Parses new Form 4 filings into `insider_transactions`, rewrites `insiders/{T}.json` and `meta/insiders.json` |

Every run is logged in `etl_runs`; the latest runs are published to `meta/status.json` for the Data status page.

Vercel Hobby runs each cron entry once a day within the scheduled hour, which is why the jobs are split into several entries.

## Environment

Set in Vercel (the Supabase ones come from the integration):

`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `POSTGRES_URL_NON_POOLING`, `FIREWORKS_API_KEY`, `TAVILY_API_KEY`, `FIRECRAWL_API_KEY`, `CRON_SECRET`, `SEC_USER_AGENT` (SEC asks for `Name email@domain`).

Optional: `FU_ASSISTANT_MODEL`, `FU_ASSISTANT_FALLBACK`, `FU_SUMMARY_MODEL`, `FU_SUMMARY_FALLBACK`.

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
```

Adding a company: append it to `etl/universe.py` and run `python -m etl.run companies --tickers NEW` then `python -m etl.run prices`.

## API guard

`/api/ai`, `/api/search` and `/api/scrape` only answer requests from filedunder.ai or this project's Vercel domains, and count calls per IP and globally per day in `api_usage` via `bump_usage()`.
