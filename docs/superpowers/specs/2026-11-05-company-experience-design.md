# Companies experience and data consistency

## User outcome

Give every registered issuer its own discoverable company page outside the screener. Preserve Filed Under's compact, white research workspace while making the initial profile useful: substantial market chart, business overview, financial table, leadership, related companies and sourced recent context. Use People Data Labs for professional enrichment with the supplied credential kept server-side.

## Scope and boundaries

- Directory includes all 1,100 registered issuers; screener/index eligibility remains the existing 800 validated issuers. Registry membership is not silently activated.
- Company deep links work for pending issuers using their existing company and price objects. Coverage is accurately described.
- Keep existing financial, segment, news, IR, filing and insider tabs and evidence drawers.
- No invented business descriptions, portraits, management tenure, reporting lines, financial values or intraday claims.
- Fix identified future share dates, stale aggregate publication and legacy normalization/cache handling using deterministic tests. Any historical repair uses backups, exact guards and readback verification.
- No database schema changes are required for the new UI. Public professional enrichment uses a cached storage object; no billable calls from anonymous page views.

## Information layout

Directory: heading/count, large company search, sector filters, sorting and compact aligned rows with company identity, sector/industry, available market data and coverage. Responsive pagination avoids rendering 1,100 images/rows at once. Keyboard-accessible links open company pages.

Profile: breadcrumb and compact identity header with real logo fallback, exchange/ticker, industry/location, latest close and source date. Tabs remain. Overview opens with a substantial chart and concise key figures, followed by a readable business description and company facts; sourced financial rows show period and exact evidence; leadership is grouped by verified roles, with start dates at their real precision; related issuers use explainable sector/industry similarity; recent context links to real articles/IR material and uses sourced relevant images only.

Chart: daily OHLC candlesticks/line, volume, 1M/3M/6M/YTD/1Y/3Y/all controls, SMA20/50/200, RSI14 and MACD, hover/keyboard crosshair and OHLCV, benchmark comparison when actual SPY data exists. Reuse parity-tested technical calculations. Show actual price adjustment basis and date, and descriptive indicator readings without trading promises.

## Interfaces

- `public/data/company-directory.json`: build-time registered issuer fallback; optional newer public-data directory. Entries have ticker/name/cik/sector plus available domain/industry and screener eligibility. Loading this must not mutate screener membership.
- `company-profiles/{TICKER}.json`: optional server-produced professional profile. UI handles unavailable profiles and never triggers enrichment costs. Fields: description, website, industry, headquarters, founded, employees, leadership[], images[], sources[], updated_at, coverage. Every enriched record retains its source and observation date.
- `window.FU_COMPANY_CHART.render(container, {ticker, prices, benchmarkPrices, companyName})`: returns cleanup. Prices use existing `{meta,bars:[{t,o,h,l,c,v}]}` store shape.
- `window.FU_COMPANIES`: directory loader/search/view module, separate registered issuer store.
- `window.FU_COMPANY_PROFILE`: new overview renderer, retaining source cells from the existing financial model.

## Data and credential policy

PDL is used only through an authenticated ETL/cron path with `PEOPLE_DATA_LABS_API_KEY`. Company identity must match ticker/domain and professional records must match the selected company. Allowlist names, roles, professional profile URLs, tenure dates, public company facts; do not publish contact details or unrelated personal information. Cache and bound company/people requests, honor rate limits and stop on exhausted credits. A small live pilot establishes access/fields before bulk enrichment. PDL descriptions and employee estimates are labeled with their provider; SEC remains the financial source.

Photos and context media require actual source URLs. Missing photos use initials; no guessed images. Role grouping is not an asserted reporting structure. Missing or invalid financial values stay unavailable.

## Audit baseline (2026-11-05)

Main/production: b87d50cd16b8474eb0f0c825c743282a5fd2b9e8 (PR25), GitHub verify successful, Vercel READY. Supabase active healthy. Registered companies1,100; active published index800. Facts2,502,434; filings273,625; daily prices1,657,211. IR count was growing during review; sampled full object-reference check found no missing originals/text.

Confirmed defects: 12,754 unsupported derived quarterly EPS/share rows across431 issuers; 3,137 negative derived diluted-share rows across396 issuers; 441 active legacy fundamental-cache entries without pipeline version; 14 just-processed issuers had fresh metadata but stale index values; AAL future shares date2027-07-17 was accepted. Published index/screener snapshots were produced at different times and disagreed on coverage. Job rows included42 running statuses,37 older than2h; logs showed prior300-second IR/insider timeouts. These are distinct from deployment health.

## Verification and completion

Behavior tests for all-company routing/search without changing screeners; source-preserving overview and missing metadata; indicator correctness and cleanup; PDL identity, sanitization, quota/cache/auth failures; future-date and stale-read regressions. Run existing JS, financial, parity suites and production build. Inspect actual desktop/mobile UI, navigation, chart controls, pending issuer, financial drawer and failed enrichment. Record exact verified commit/deployment and remaining coverage limitations.
