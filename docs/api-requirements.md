# API requirements for Filed Under

Code audit: 2026-11-04. This describes the repository implementation, not a verification of account credentials, quotas, billing, or available provider models. No live model requests or email deliveries were made for this audit.

## Services used by the workflows

| Capability | Service and server configuration | Application path | Required when |
|---|---|---|---|
| Conversational analyst, document/sheet AI proposals, optional thesis decomposition | Fireworks AI — `FIREWORKS_API_KEY` | `POST /api/ai` → `https://api.fireworks.ai/inference/v1/chat/completions` | Generating model responses or AI edit proposals |
| Natural-language screener | Fireworks AI — same key | `POST /api/screen` → same chat-completions endpoint | Turning a prompt into a validated screen specification |
| External web/news research | Tavily — `TAVILY_API_KEY` | `POST /api/search` → `https://api.tavily.com/search` | Assistant `web_search`, fresh news discovery, and parts of IR discovery |
| Read an external page or extract a source document | Firecrawl — `FIRECRAWL_API_KEY` | `POST /api/scrape` → `https://api.firecrawl.dev/v2/scrape`; ETL document/IR readers | Assistant `read_url` and the relevant document ingestion jobs |
| Store research data, source files and structured records | Supabase — `SUPABASE_URL`, server-only `SUPABASE_SERVICE_ROLE_KEY` | Storage and REST APIs | Publishing/updating the platform data and running service-side monitoring |
| Sign-in, private watchlist/rule/thesis sync and in-app cloud alerts | Supabase Auth + REST — `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` (or legacy `SUPABASE_ANON_KEY`) | `/api/client-config`, `/api/monitoring`, `/auth/v1/*`, owner-scoped REST requests | Account-backed monitoring; local research remains available without sign-in |
| Scheduled checks while the user is away | Existing Vercel Cron + Supabase; `CRON_SECRET` | `/api/cron/analyst` | Autonomous server-side checks; this is scheduling infrastructure, not another LLM API |
| Email alerts and morning briefs | Resend — `RESEND_API_KEY`, `MONITORING_FROM_EMAIL` | Monitoring outbox → `https://api.resend.com/emails` | Sending email; the sender must be configured with the provider and the recipient must have a verified account and current opt-in |
| Financial facts, filings and insider filings | SEC EDGAR source requests; `SEC_USER_AGENT` | `etl/sec.py`, related ETL modules, `/api/filing` | Refreshing primary filing data and reading filing sources |

Source: [assistant](../api/ai.js), [screener](../api/screen.js), [search](../api/search.js), [page reader](../api/scrape.js), [monitoring](../api/_lib/monitoring.js), [filing reader](../api/filing.js), [ETL common client](../etl/common.py), [IR ingestion](../etl/ir.py), [document ingestion](../etl/docs.py).

### Practical configuration groups

**Live AI on existing platform data:** Fireworks is the only model provider currently wired in. Use the existing Supabase setup for the data and API usage counters. OpenAI and Anthropic keys are not read by these routes; changing providers requires an adapter/code change.

**AI with external research:** add Tavily and Firecrawl. Without them, the assistant can still reason over the platform's loaded structured data, but its corresponding web-search/page-reading tool calls cannot complete. These are separate capabilities, not two alternative model providers.

**Autonomous monitoring:** configure Supabase Auth/private tables, the server service key, and the scheduled cron secret. The monitoring job evaluates stored rules and thesis criteria deterministically against published data. It does not call the LLM for each check. Resend is needed only for email delivery; account-backed in-app alerts do not need it.

**Continuous enrichment:** the existing company/news/IR/KPI pipelines use the same Fireworks, Tavily and Firecrawl services where applicable. Viewing previously published data does not itself invoke these paid enrichment providers. Refreshing those datasets is a separate process and may depend on their keys.

## Model configuration

The code currently prefixes each configured model value with `accounts/fireworks/models/`. These values are model slugs, not arbitrary provider URLs or full model paths.

| Workflow | Primary variable and default string | Fallback variable and default string |
|---|---|---|
| Assistant and editor AI | `FU_ASSISTANT_MODEL` = `kimi-k3p1-pro` | `FU_ASSISTANT_FALLBACK` = `deepseek-v4p1-flash` |
| Natural-language screener | `FU_SCREEN_MODEL` = `kimi-k3p1-pro` | `FU_SCREEN_FALLBACK` = `deepseek-v4p1-flash` |
| Generated company/news/macro narratives | `FU_SUMMARY_MODEL` = `deepseek-v4p1-flash` | `FU_SUMMARY_FALLBACK` = `kimi-k3p1-pro` |
| KPI standardization | `FU_KPISTD_MODEL` = `kimi-k3p1-pro` | `FU_KPISTD_FALLBACK` = `deepseek-v4p1-flash` |

These are **repository defaults**, not a claim that those exact models are currently offered or enabled on the connected account. Validate the configured IDs before activating the workflows. Assistant/editor models must support the function-tool messages used by the tool loop; the screener and ETL paths request JSON output. KPI standardization also sends `reasoning_effort: "low"` to its primary and `"none"` to its fallback, so those parameters need to be supported by the chosen models. Both assistant primary and fallback currently use the same Fireworks endpoint and credential, so this is model fallback within one provider, not protection against a provider-wide outage.

Source: [assistant model selection](../api/ai.js), [screener model selection](../api/screen.js), [summary generation](../etl/summaries.py), [KPI standardization](../etl/kpistd.py).

## What does not need an LLM key

With the required published data available, these features use the application code:

- Exact reported figures, quarter comparisons and evidence-record inspection.
- The morning brief's filing/event list and numerical changes.
- Supported plain-language standing-order patterns, their explicit rule preview, and deterministic evaluation.
- Thesis scoring after measurable criteria are accepted. Optional AI help creating those criteria uses `/api/ai`.
- Financial signal calculations, peer comparisons, scenario arithmetic and the implemented historical studies.
- Spreadsheet formulas, model construction from filed history, editable forecast assumptions, and XLSX/DOCX export.
- The factual cited report template. Free-form rewriting and AI editing use the model endpoint.

The deterministic morning brief and the older generated company/macro narrative pipeline are distinct. A key is needed for the latter's generation step even though the numerical brief works without it. Current document/spreadsheet editing and export use browser code plus bundled `exceljs` and `docx`; they do not require Microsoft 365 or Google Docs APIs.

Source: [analyst engine](../public/lib/analyst.js), [research pages](../public/pages/analyst.js), [workspace engine](../public/lib/workspace.js), [workspace UI](../public/pages/workspace.js), [shared assistant tool loop](../public/app.js).

## Infrastructure details that matter

- `/api/cron/analyst` is configured in `vercel.json` at `5 * * * *`: once each hour, at minute 5. The code then checks each account's saved time zone and morning hour. It is not an instant filing-push subscription.
- Monitoring operates on the platform's ingested datasets. If a source ingestion job is stale or a business metric is missing, another model key cannot supply a verified result; coverage must be fixed upstream.
- Supabase Auth email/sign-in delivery is separate from Resend research-alert delivery. Having the alert key alone does not configure account verification emails.
- `POSTGRES_URL_NON_POOLING` appears in older setup documentation but is not consumed by the active AI, ETL or migration code. The current migration wrapper runs `supabase db push --linked` through the official CLI. It is not an AI API requirement; migrations remain explicit and are not performed by the application build.
- `BLS_API_KEY` is optional for the BLS source integration. The current primary filing-data path does not read an SEC API token.
- The existing rate-counter guard uses `SUPABASE_URL` plus `SUPABASE_SERVICE_ROLE_KEY`; it currently fails open if the counter is unavailable. That is existing behavior and should not be mistaken for a hard spending cap.
- This audit does not establish that the deployment has every variable set or that any account is ready for billable execution. API activation was deferred by the user.

Source: [cron configuration](../vercel.json), [monitoring time and delivery checks](../api/_lib/monitoring.js), [browser authentication](../public/lib/monitoring-client.js), [migration script](../scripts/migrate.mjs), [BLS source](../etl/bls.py), [request guard](../api/_lib/guard.js).

## Accuracy and future capabilities

The accuracy path is source ingestion → normalized financial value → deterministic calculation → immutable evidence record → model interpretation → numeric-claim validation. The structured evidence record is implemented inside this repository; it is not another service to subscribe to. A citation must resolve to the exact value, unit, period and filing version used in the answer, including the inputs of derived values.

The currently connected services do not by themselves create complete licensed earnings-call coverage, a historical consensus-estimates database, a supply-chain relationship database, intraday market data, or unbiased historical constituent coverage. Product promises that require those datasets need explicit source integrations and validation; they should not be filled with model guesses.

Source: [evidence engine](../public/lib/evidence.js), [financial engine](../public/lib/financial.js), [analyst coverage and historical-study rules](../public/lib/analyst.js).
