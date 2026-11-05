# Registered company directory and professional profiles

## What is shipped

`public/data/company-directory.json` contains all 1,100 validated registry issuers. Its `screener_eligible` flag is independent of discovery: the scheduled/published universe currently has 800 eligible companies and 300 pending publication. A pending company may already have prices, filings and facts. The directory deliberately makes no unsupported claim about missing source coverage.

Regenerate it from the repository root:

```sh
python scripts/generate_company_directory.py
```

The generator reads `etl.universe.BY_TICKER` and `UNIVERSE`, and optionally copies known website/industry metadata from reviewed local profiles with matching ticker **and** CIK. Pass `--fundamentals /path/to/meta/fundamentals.json` to include SEC industry descriptions from an already retrieved snapshot; ticker, registry name and sector must all match. The current manifest contains SEC industry descriptions for all 1,100 issuers. The runtime also accepts the current index’s SEC classification, and related-company ranking uses that shared classification when curated industry wording differs. It cannot change screener membership. The build ships the directory as a local fallback.

Profiles are shipped at `public/data/company-profiles/{TICKER}.json` and published as cached objects. Official descriptions, leadership, portraits and contextual images retain their own sources; verified PDL records add separately attributed company and professional metadata. The initial five-company pilot is recorded under the historical section below. Current coverage comes from the generated directory and profile index, rather than that pilot's counts.

The directory's **Company metadata** filter uses actual profile availability, and **Leadership available** requires a positive sourced leadership count. Neither filter infers enrichment from registry membership or screener eligibility. Row labels distinguish Leadership, Company details, Screener and Registered; their tooltips expose leader/portrait counts where available. A metadata-only profile is not a claim of complete management coverage.

## Expanded backfill, 2026-11-05

The expanded snapshot contains **728 company profiles**, including **87 companies with official leadership** and **901 sourced leadership entries**. Company metadata includes 727 websites, 686 company LinkedIn pages, 692 headquarters locations, 634 founding years, 687 provider size bands and 149 business descriptions. The leadership records contain 158 explicit role start dates and 30 separately sourced company joining dates. These are field coverage counts, not a claim that every profile is complete.

The registry still contains 372 issuers without an enriched profile and 1,013 without a sourced leadership roster. Even among covered companies, some rosters are deliberately limited to the official page or selected biographies reviewed. The public profile's coverage note identifies that scope. Unknown titles, biographies, dates and photographs are left empty.

The [backfill audit](audits/2026-11-05-company-metadata-backfill.json) records the final snapshot, publication/readback results, portrait outcomes, provider request accounting and validation checks. Per-field evidence remains in each public profile. The raw provider candidates and source extraction archive remain private.

## Public profile contract

Each profile identifies `schema_version`, `ticker`, numeric `cik`, registry `name`, `updated_at` and `coverage`. It may include:

| Field | Meaning |
| --- | --- |
| `description`, `description_source` | Business prose and the exact source/observation metadata. |
| `website`, `linkedin_url`, `industry`, `headquarters`, `founded` | Available company metadata and the company's LinkedIn page; `field_sources` records provenance. Headquarters is city/region/country, without a street address. |
| `employees` | Officially sourced workforce, when available. PDL profile counts never populate this field. |
| `employee_size_range` | Provider-reported size band, shown with that label when an official workforce count is unavailable. It is not reported headcount. |
| `professional_profile_count` | PDL profiles associated with the company, explicitly identified as a provider count rather than reported headcount. |
| `professional_profile_count_basis`, `professional_profile_count_source` | Count interpretation and provider source. |
| `leadership[]` | Names, sourced professional roles, biography and LinkedIn URLs, observation/verification dates, and sourced portraits when available. `start_date` identifies verified role tenure; `company_start_date` identifies an officially sourced joining date; `employment_start_date` identifies provider employment tenure. |
| `images[]` | Actual, sourced official context images. Leadership portraits use each person's `photo_url` and `photo_source_url`. |
| `sources[]` | Provider/official name, URL, observation time and, for PDL, endpoint and record identity. |
| `coverage` | Field availability and origin; an available metadata record is not a complete management roster. |

A start date remains `YYYY`, `YYYY-MM` or `YYYY-MM-DD` at its source precision. PDL's `job_start_date` is stored as `employment_start_date`, with an explanatory `tenure_note`; it is not evidence of when the displayed role began. The UI labels this “With company since” when a role start is unavailable. A role `start_date` requires an explicit appointment source (`start_date_source_url` or `start_date_source.url`). Invalid and future dates are excluded. Role groups are presentation categories and do not assert reporting relationships. Unknown values remain null or absent.

The PDL records used here do not supply profile portraits. Portraits must come from separately sourced official pages; missing photos remain missing. The UI retains the official biography as the person's main link and exposes a separate LinkedIn link when available. It displays the complete sourced roster rather than truncating it to sixteen people.

The front end reads public cached objects or the bundled fallback and does not invoke enrichment. API keys, contact details, personal addresses, phones, email, raw person records and unrelated personal data are not published.

## Authenticated enrichment

`api/cron/company_profiles.py` uses the existing `CRON_SECRET` authentication gate before constructing a backend client. It is **not** added to the schedule. Configuration is server-only:

- `PEOPLE_DATA_LABS_API_KEY`: provider credential.
- `PDL_COMPANY_TICKERS`: an explicit comma-separated allowlist of at most five registered tickers. Empty/unset disables refresh. URL query parameters cannot expand this list.
- Existing `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and `CRON_SECRET` support private state and public profile publication.

An operator may invoke the route after setting a reviewed allowlist. The browser cannot do this anonymously. Merely configuring the PDL credential does not start spending.

`refresh_profiles()` uses the existing distributed ETL lease under `company-profiles`. It reserves a maximum of **five company attempts and ten requested professional records per UTC day** in the private `raw/company-profiles/state.json` object. Reservations are made before requests, increment the state revision and must pass authenticated SHA-256 readback. A stale or failed readback stops the job. Reservations are retained after timeouts, no matches and failures rather than risking another charge for an uncertain request.

Provider requests use a 15-second timeout, no automatic retry, no redirects, a response-size bound and explicit field selection. A no-credit HEAD preflight checks quota before the first paid request to each endpoint. PDL may return HTTP400 for these parameterless HEAD requests while supplying valid quota headers. The client excludes the explicit overage allowance, rejects non-finite or negative credit/rate values, and caps its local balance by every subsequent response. A missing response balance requires another preflight; malformed metadata stops further spending while retaining an already paid successful result. These checks do not change account-level overage settings or coordinate other applications using the account.

Positive provider results are cached for 30 days; no-match/failed attempts for seven days. Provider stops add a one-day pause. Curated official corrections are reconciled and republished without provider calls before cache, pause and daily-budget checks; this path does not require a PDL key or reset the provider cache/reservations. Identical merges do not rewrite objects, and older bundled official evidence cannot replace newer official evidence already published. The default person query requests at most two records per company, only to supplement missing professional links on a current official roster. Without an official roster, it does not spend on person candidates. Lease and deadline checks run around reservation I/O and immediately before each provider transport, including after quota preflight. In-flight storage operations still use the shared storage client's timeout policy.

Provider company records require verified ticker identity and supporting company identity. Enrichment uses its reported likelihood together with a known domain or normalized exact issuer/legal name. Company search has no enrichment likelihood: its records need ticker identity plus an exact normalized name or verified domain, with the actual endpoint and match basis recorded. A share-class alias is acceptable only when the registry establishes the same CIK. The registry display name remains the published company name.

Person candidates must match the verified provider company ID and known company domain, an approved corporate title, and a full provider verification date within 180 days. Approved title normalization handles case, whitespace, separators, abbreviations and recognized compound roles such as “Chairman and CEO” or “Executive Vice President and CFO”. Regional, divisional and former-role qualifiers are not accepted through broad title substring matching, and `cxo` classification alone is insufficient. Search queries also restrict verification dates before requesting paid results. **These checks do not establish current corporate leadership.** The bulk pass found false and outdated role claims even on fresh provider records. Provider-only people remain in the private candidate cache, and the publisher rejects them. Names and roles require corroboration from current official leadership pages or filings before appearing in the public roster.

The serverless bundle includes `public/data/company-profiles/**` so the backend can preserve curated official facts when enriching. Leadership freshness is checked per person against its own observation date, with a dedicated official snapshot date as the fallback. A provider supplement's overall `updated_at` cannot renew old leadership. Future or more-than-180-day-old official observations are excluded, while source dates and date precision remain intact. A current official snapshot may be newer than the provider observation and still wins.

Precedence follows the actual source of each field and person. Provider refreshes preserve current official descriptions, biographies, roles, appointment dates and portraits; older official evidence cannot replace newer official evidence. A fresh provider record can supplement a missing official LinkedIn URL only after an exact normalized full-name and role-family match within the same verified company. Description attribution follows `description_source`, so an official description is not labeled PDL merely because other company metadata came from PDL.

## Explicit bulk backfill and publication

`scripts/backfill_company_profiles.py` is an operator-run workflow, separate from the cron's five-company/ten-person daily limits. Its three phases are:

| Phase | Operation |
| --- | --- |
| `companies` | Read an explicit JSON plan with `enrich` and `search` ticker arrays; fetch bounded company records. |
| `people` | Search only verified provider company IDs, with approved titles and the 180-day verification window; give each company group a first page before deeper pagination. |
| `assemble` | Validate cached records, merge official corrections and write public profiles locally. Makes no provider calls and does not require the PDL key. |

All phases require `--issuers` and `--cache`; company fetching also requires `--plan`. The issuer JSON is an array of records containing registry-matching `ticker` and `cik`, with `name` supplying an optional legal-name match and `website` supplying a known domain. `--output` defaults to `public/data/company-profiles`. Example invocations from the repository root:

```sh
python scripts/backfill_company_profiles.py companies --issuers /path/to/issuers.json --plan /path/to/plan.json --cache cache/company-profile-backfill
python scripts/backfill_company_profiles.py people --issuers /path/to/issuers.json --cache cache/company-profile-backfill
python scripts/backfill_company_profiles.py assemble --issuers /path/to/issuers.json --cache cache/company-profile-backfill
```

Set `PEOPLE_DATA_LABS_API_KEY` in the environment for fetch phases. Review the explicit `--company-enrich-limit`, `--company-search-limit` and `--people-limit` options before a run; they cap separate endpoint credit pools and must remain identical when resuming the same cache journal. The fetcher reserves credits in an atomic local journal before transport, uses a process lock and endpoint pacing, checks non-overage quota, and keeps only allowlisted response fields. Completed operations reuse cached results. Uncertain transports retain their reservation and are not automatically replayed. Only a conclusive provider credit receipt releases an unused reservation. Cached fetch data and its journal are operational files, not public profile artifacts.

Person pagination stores the exact request parameters and page size before transport. Resuming uses those requests even when the remaining balance changes; it never substitutes a smaller first page and spends again on duplicate results. Legacy person plans without durable request parameters and with paid reservations require manual review instead of automatic replay.

`scripts/backfill_company_basics.py` uses the separate `company/clean` allowance for the wider directory, with a private atomic journal, bounded attempts and serialized request starts. Its output is a candidate cache, not a publication. Assembly accepts only exact ticker plus normalized issuer/legal name or an independently known official domain. The cleaner's raw `score` and `fuzzy_match` values are retained as diagnostics, never converted to enrichment likelihood. Verified paid company records take precedence over cleaner records. Use `--basics-cache` on assembly to select a different cleaner cache.

`scripts/cache_profile_portraits.py` downloads source-linked portraits with bounded concurrency and byte limits, requires a real image signature, uploads a content-addressed public asset and verifies its public bytes by SHA-256. HTML/error pages are rejected. The original URL and official attribution remain available when a cached image replaces the remote URL. The cache manifest reports individual failures; it does not silently substitute a different person's photo. Source downloads and owned storage operations have separate timeout policies. A transport-only failure can receive one bounded recovery attempt; source403 responses and non-image content are not retried. An existing cached object is reused only after its public bytes match the freshly validated source image.

Offline assembly applies the portrait manifest. Verified cached images retain `photo_original_url`, `photo_source_url`, credit and a content hash. A known403 or non-image source has its displayed `photo_url` cleared while retaining the original URL and attribution for later repair. A size-limit rejection alone does not establish that the original image is broken. Portrait updates change the profile's update timestamp without refreshing the official leadership observation date.

Publish the assembled profiles explicitly:

```sh
python scripts/publish_company_profiles.py --profiles public/data/company-profiles --fundamentals /path/to/fundamentals.json --report cache/company-profile-backfill/publication-report.json
```

Publication uses the existing Supabase server credentials and distributed `company-profiles` lease. It validates issuer identity, source URLs, portrait attribution and tenure dates; reconciles newer live official evidence; and backs up changed previous objects to private storage before replacing them. Changed profiles and the regenerated directory must match their readback exactly. The script updates bundled fallbacks, the profile index and a publication report. Neither a page view nor an unset cron allowlist invokes this bulk workflow.

Both assembly and publication reject a populated leadership roster without an official coverage marker. This catches an official collector accidentally retaining a previous provider-only marker before any roster can disappear during the merge. The merge also retains previously verified provider company fields when an official-only collector supplies new leadership, preserving their original field dates and attribution.

## Historical pilot and verification, 2026-11-05

The initial shipped profiles covered AAPL, AMZN, GOOGL, MSFT and NVDA: 20 selected leaders, 17 sourced portrait links and five contextual images. PDL added company metadata for AAPL/MSFT/NVDA; AMZN and GOOGL used official sources without additional paid calls. No PDL pilot person record replaced official leadership. These are historical pilot counts, not the current backfill totals.

The bounded live pilot made three successful company enrichments (AAPL, MSFT, NVDA), each at likelihood 10 with matching ticker/domain/name. Response headers reported one company credit for each. Six professional records were returned across three person searches, with two search credits reported per response. No additional paid calls were made during that initial implementation or its tests; subsequent bulk work is tracked in its own journal and reports.

The broad pilot person query returned divisional roles, a title-classification false positive and an old verification date. Those results motivated the stricter exact-title and freshness gates; the initial public leadership lists use official sources. PDL's company response is flat rather than wrapped in `data`; person search returns a `data` array. The implementation handles those shapes separately.

Automated checks cover directory completeness/eligibility separation, exact issuer matching, contact-field exclusion, headcount interpretation, date precision, role/employer/freshness rejection, official source precedence, positive and negative caching, durable quota reservation, unknown/exhausted quota, free credits versus overages and unauthenticated route rejection. Tests use controlled network/storage boundaries and make no live paid requests.

## Official provider references

These documentation pages were opened and checked during implementation:

- [Company Enrichment API](https://docs.peopledatalabs.com/docs/company-enrichment-api)
- [Company enrichment input parameters](https://docs.peopledatalabs.com/docs/input-parameters-company-enrichment-api)
- [Company enrichment output response](https://docs.peopledatalabs.com/docs/output-response-company-enrichment-api)
- [Company schema](https://docs.peopledatalabs.com/docs/company-schema)
- [Cleaner APIs](https://docs.peopledatalabs.com/docs/cleaner-apis-reference)
- [Person Search API](https://docs.peopledatalabs.com/docs/person-search-api)
- [Person search input parameters](https://docs.peopledatalabs.com/docs/input-parameters-person-search-api)
- [Person schema and verification dates](https://docs.peopledatalabs.com/docs/fields)
- [Usage limits and credit/rate headers](https://docs.peopledatalabs.com/docs/usage-limits)
