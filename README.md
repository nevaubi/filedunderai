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
| | `filings/{accession}.html.gz` | Full filed documents for the source panel (scripts removed, EDGAR base URL added), gzipped and inflated in the browser |
| | `logos/{T}.png` | Company logos (497 of 500) |
| | `insiders/{T}.json` | Form 4 transactions, open-market summaries, largest traders |
| | `statements/{T}/index.json`, `statements/{T}/{accession}.json.gz`, `statements/{T}/std.json.gz`, `statements/std_map.json` | As-filed statements per filing (gzipped, inflated in the browser), standardized values per filing, the template mapping |
| | `meta/*.json` | Job caches and status (fundamentals, price stats per part, rotation and check cursors, insiders index, pipeline status) |
| `raw` (private) | `sec/companyfacts/*.json.gz`, `sec/filings/{accn}/*.htm.gz`, `sec/releases/*.md.gz` | Archived source documents, indexed in `documents` |

## Schedules (UTC, `vercel.json`)

| Job | When | What |
|---|---|---|
| `/api/cron/prices?part=N&parts=8` | 21:40-21:54 Mon-Fri | Bars and stats for an eighth of the stocks each, with a 250s budget (part 0 adds index, rate and commodity symbols); every part rebuilds the index |
| `/api/cron/macro` | 12:10 daily | Curve, FRED, fed funds, macro news and brief |
| `/api/cron/companies` | six runs a day | Checks SEC submissions for all 500 (rotating cursor). New 10-K/10-Q/8-K: full reprocess, up to 15 per run. Other new filings (Form 4, 144, proxies) are written straight to `filings` |
| `/api/cron/news` | 13:00, 19:00 | Rolling news + summary refresh, 14 companies per run |
| `/api/cron/segments` | 12:50, 00:50 | Recomputes XBRL segment tables for companies with a new 10-K or 10-Q |
| `/api/cron/ir` | 14:40, 22:40, 04:40 | Collects IR documents: companies that just reported earnings first, then a rolling cursor (8 per run) |
| `/api/cron/insiders` | 15:30, 23:30 | Parses new Form 4 filings into `insider_transactions`, rewrites `insiders/{T}.json` and `meta/insiders.json` |

| `/api/cron/statements` | 09:17 daily | Loads any new or republished quarter of the SEC Financial Statement Data Sets (resumes a partial one within a 250s budget) |

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
python -m etl.run statements --defer --workdir /tmp/fsds   # as-filed statements, every quarter not yet loaded
python -m etl.run statements --quarters 2026q3 --force     # reload one quarter
```

Adding a company: append it to `etl/universe.py` and run `python -m etl.run companies --tickers NEW` then `python -m etl.run prices`.

## Source panel

Clicking a value opens the actual filed document in a sandboxed frame and scrolls to it. The panel finds the inline XBRL element whose concept and context period match the value; for filings without inline XBRL it finds the printed figure. Latest 10-K and 10-Q copies are published by the ETL; any other filing is fetched once by `/api/filing?accn=` (only accessions present in the site's data), stored, and served from storage after that.

## API guard

`/api/ai`, `/api/search`, `/api/scrape` and `/api/filing` only answer requests from filedunder.ai or this project's Vercel domains, and count calls per IP and globally per day in `api_usage` via `bump_usage()`.
