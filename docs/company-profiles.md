# Registered company directory and professional profiles

## What is shipped

`public/data/company-directory.json` contains all 1,100 validated registry issuers. Its `screener_eligible` flag is independent of discovery: the scheduled/published universe currently has 800 eligible companies and 300 pending publication. A pending company may already have prices, filings and facts. The directory deliberately makes no unsupported claim about missing source coverage.

Regenerate it from the repository root:

```sh
python scripts/generate_company_directory.py
```

The generator reads `etl.universe.BY_TICKER` and `UNIVERSE`, and optionally copies known website/industry metadata from reviewed local profiles with matching ticker **and** CIK. Pass `--fundamentals /path/to/meta/fundamentals.json` to include SEC industry descriptions from an already retrieved snapshot; ticker, registry name and sector must all match. The current manifest contains SEC industry descriptions for all 1,100 issuers. The runtime also accepts the current index’s SEC classification, and related-company ranking uses that shared classification when curated industry wording differs. It cannot change screener membership. The build ships the directory as a local fallback.

Reviewed profiles are shipped for AAPL, AMZN, GOOGL, MSFT and NVDA at `public/data/company-profiles/{TICKER}.json`. Their descriptions, current leadership, portraits and contextual images come from the official pages listed in each record. PDL adds separately attributed company metadata for the initial AAPL/MSFT/NVDA pilot. AMZN and GOOGL use official sources without additional paid calls. These five profiles contain 20 selected leaders, 17 sourced portrait links, and five contextual images; the lists are partial rosters, and image availability remains source-dependent. No PDL pilot person record was substituted for official leadership.

## Public profile contract

Each profile identifies `schema_version`, `ticker`, numeric `cik`, registry `name`, `updated_at` and `coverage`. It may include:

| Field | Meaning |
| --- | --- |
| `description`, `description_source` | Business prose and the exact source/observation metadata. |
| `website`, `industry`, `headquarters`, `founded` | Available company metadata; `field_sources` records provenance. Headquarters is city/region/country, without a street address. |
| `employees` | Officially sourced workforce, when available. PDL profile counts never populate this field. |
| `professional_profile_count` | PDL profiles associated with the company, explicitly identified as a provider count rather than reported headcount. |
| `professional_profile_count_basis`, `professional_profile_count_source` | Count interpretation and provider source. |
| `leadership[]` | Names, exact professional roles, professional profile/source URLs, observation time, provider verification date when applicable, and real partial start dates. |
| `images[]` | Only actual, sourced official context images. PDL does not supply or invent portraits. |
| `sources[]` | Provider/official name, URL, observation time and, for PDL, endpoint and record identity. |
| `coverage` | Field availability and origin; partial provider leadership is not a complete management roster. |

A start date remains `YYYY`, `YYYY-MM` or `YYYY-MM-DD` at its source precision. Invalid and future dates are omitted. Role groups are presentation categories and do not assert reporting relationships. Unknown values remain null or absent.

The front end reads public cached objects or the bundled fallback and does not invoke enrichment. API keys, contact details, personal addresses, phones, email, raw person records and unrelated personal data are not published.

## Authenticated enrichment

`api/cron/company_profiles.py` uses the existing `CRON_SECRET` authentication gate before constructing a backend client. It is **not** added to the schedule. Configuration is server-only:

- `PEOPLE_DATA_LABS_API_KEY`: provider credential.
- `PDL_COMPANY_TICKERS`: an explicit comma-separated allowlist of at most five registered tickers. Empty/unset disables refresh. URL query parameters cannot expand this list.
- Existing `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and `CRON_SECRET` support private state and public profile publication.

An operator may invoke the route after setting a reviewed allowlist. The browser cannot do this anonymously. Merely configuring the PDL credential does not start spending.

`refresh_profiles()` uses the existing distributed ETL lease under `company-profiles`. It reserves a maximum of **five company attempts and ten requested professional records per UTC day** in the private `raw/company-profiles/state.json` object. Reservations are made before requests, increment the state revision and must pass authenticated SHA-256 readback. A stale or failed readback stops the job. Reservations are retained after timeouts, no matches and failures rather than risking another charge for an uncertain request.

Provider requests use a 15-second timeout, no automatic retry, no redirects, a response-size bound and explicit field selection. A no-credit HEAD preflight checks quota before the first paid request to each endpoint. PDL may return HTTP400 for these parameterless HEAD requests while supplying valid quota headers. The client excludes the explicit overage allowance, rejects non-finite or negative credit/rate values, and caps its local balance by every subsequent response. A missing response balance requires another preflight; malformed metadata stops further spending while retaining an already paid successful result. These checks do not change account-level overage settings or coordinate other applications using the account.

Positive provider results are cached for 30 days; no-match/failed attempts for seven days. Provider stops add a one-day pause. Curated official corrections are reconciled and republished without provider calls before cache, pause and daily-budget checks; this path does not require a PDL key or reset the provider cache/reservations. Identical merges do not rewrite objects, and older bundled official evidence cannot replace newer official evidence already published. The default person query requests at most two records per company. Fresh reviewed official leadership avoids those person requests entirely. Lease and deadline checks run around reservation I/O and immediately before each provider transport, including after quota preflight. In-flight storage operations still use the shared storage client's timeout policy.

Provider records are admitted only after exact ticker matching. A known official domain must match exactly; without one, the result needs a stronger likelihood and a normalized exact issuer name. Current person records must match the verified provider company ID, known company domain, an exact corporate title allowlist, and a full provider verification date within 180 days. Broad `cxo` classification alone is insufficient. Provider metadata is not proof that a person appears on the issuer's official leadership roster; current official sources take precedence.

The serverless bundle includes `public/data/company-profiles/**` so the backend can preserve curated official facts when enriching. Leadership freshness is checked per person against its own observation date, with a dedicated official snapshot date as the fallback. A provider supplement's overall `updated_at` cannot renew old leadership. Future or more-than-180-day-old official observations are excluded, while source dates and date precision remain intact. A current official snapshot may be newer than the provider observation and still wins.

## Pilot and verification, 2026-11-05

The bounded live pilot made three successful company enrichments (AAPL, MSFT, NVDA), each at likelihood 10 with matching ticker/domain/name. Response headers reported one company credit for each. Six professional records were returned across three person searches, with two search credits reported per response. No additional paid calls were made for the implementation or tests.

The broad pilot person query returned divisional roles, a title-classification false positive and an old verification date. Those results motivated the stricter exact-title and freshness gates; the initial public leadership lists use official sources. PDL's company response is flat rather than wrapped in `data`; person search returns a `data` array. The implementation handles those shapes separately.

Automated checks cover directory completeness/eligibility separation, exact issuer matching, contact-field exclusion, headcount interpretation, date precision, role/employer/freshness rejection, official source precedence, positive and negative caching, durable quota reservation, unknown/exhausted quota, free credits versus overages and unauthenticated route rejection. Tests use controlled network/storage boundaries and make no live paid requests.

## Official provider references

These documentation pages were opened and checked during implementation:

- [Company Enrichment API](https://docs.peopledatalabs.com/docs/company-enrichment-api)
- [Company enrichment input parameters](https://docs.peopledatalabs.com/docs/input-parameters-company-enrichment-api)
- [Company enrichment output response](https://docs.peopledatalabs.com/docs/output-response-company-enrichment-api)
- [Company schema](https://docs.peopledatalabs.com/docs/company-schema)
- [Person Search API](https://docs.peopledatalabs.com/docs/person-search-api)
- [Person search input parameters](https://docs.peopledatalabs.com/docs/input-parameters-person-search-api)
- [Person schema and verification dates](https://docs.peopledatalabs.com/docs/fields)
- [Usage limits and credit/rate headers](https://docs.peopledatalabs.com/docs/usage-limits)
