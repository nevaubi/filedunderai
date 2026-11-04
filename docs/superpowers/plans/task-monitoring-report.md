# Optional cloud monitoring implementation report

Completed the second disjoint task after the evidence implementation. This worker made no production database calls, no sign-in email requests, no Resend requests, and no commits/pushes. All auth, storage and delivery tests used mocks. Root owns the additive SQL migration, its execution/verification, script integration and cron configuration.

## Files

- `api/client-config.js`: public configuration endpoint; only project URL plus validated publishable/legacy anon key.
- `api/monitoring.js`: authenticated account read, explicit research/preferences save, owned alert mark-read.
- `api/_lib/monitoring.js`: validation, authenticated/service REST clients, deterministic monitoring events, evidence closures, leases, deduplicated alert/outbox persistence, bounded scheduler and optional email dispatch.
- `api/cron/analyst.js`: CRON_SECRET protected hourly entry point.
- `public/lib/monitoring-client.js`: passwordless auth/callback/OTP/refresh, explicit save/load UI and readable alerts.
- `tests/monitoring/monitoring.test.mjs`: 17 behavioral tests.

Load `public/lib/monitoring-client.js` **before app.js**, so auth credentials are removed from the hash before routing. Root integrated this order and the hourly cron configuration.

## Browser/API contract

`FU_CLOUD.panel(host, { getLocal: () => ({watchlist,rules,theses}), applyRemote: settings => ... })` builds the optional cloud section. It preserves local research by default. Saving sends the current local research plus visible preferences after the user clicks Save. Loading shows a preview/count/revision and requires the explicit Load action. Sign-in or refreshing cloud status never overwrites local research.

Additional methods: `initialize`, `signIn(email)`, `verifyOtp(email,code)`, `signOut`, `config`, `load`, `save(settings,revision)`, `markRead(id)`, `session()` (public user/expiry only), `readableAlert(content)`.

`GET /api/client-config` returns `{configured,supabaseUrl,publishableKey,emailDeliveryConfigured}`. A secret key or a JWT with `service_role` is rejected even if mistakenly assigned to the public-key environment variable.

`GET /api/monitoring`, with bearer token, returns the server-verified user identity, owned settings or null, latest 50 owned alerts, and email-delivery configuration status.

`POST /api/monitoring` accepts:

- `{action:'save', settings:{watchlist,rules,theses,email_enabled,morning_enabled,morning_hour,timezone,filing_alerts}, revision:number|null}`. Null revision inserts a first copy; updates require exact revision match. Concurrent changes return 409. The client never supplies ownership or bookkeeping fields.
- `{action:'mark_read',id}` updates only the owned alert's `read_at`.

The server obtains identity from `/auth/v1/user`, rejects anonymous sessions, and uses the **user bearer token** for settings/alerts REST calls. It does not decode JWT payloads to authorize ownership. Enabling email requires `email_confirmed_at`. User metadata is not used for authorization. RLS and column grants in root's migration provide an independent ownership boundary.

Settings validation enforces 100 watchlist tickers, 25 validated standing rules, 50 theses with 1–20 valid criteria, finite thresholds, supported operators/scopes, Boolean flags, hour 0–23, a real IANA time zone, and payload size limits. Unknown server-owned settings are rejected. Rules use `FU_ANALYST.validateRule`.

Auth uses the documented passwordless email/OTP flow. Implicit access-token callbacks and token-hash callbacks are supported; credentials are removed from the URL immediately. Callback tokens are checked against `/auth/v1/user` before being accepted. Expired sessions use refresh-token rotation with one in-flight refresh. Sessions are stored in sessionStorage; research remains in the existing local store. Expired/rejected-link callback errors are displayed on the Watchlists route. No privileged key reaches the browser.

## Scheduled behavior

`/api/cron/analyst` requires the exact CRON_SECRET bearer value. The service client is used only for scheduled/admin work.

Each run acquires `analyst:monitor` through the service-only lease RPC, uses a unique owner, renews after 60 seconds as necessary, and releases in `finally`. Another owner causes a skipped run. The default budget is 25 due users, 300 unique company loads, and 220 seconds; company loads run in groups of five. The script is configured by root for a 300-second function ceiling.

Watchlists and thesis tickers are checked directly. Broader rule scopes rotate through up to 100 selected companies per hourly slice, with exact loaded/total/remaining counts and explicit partial-coverage alerts. Each slice uses a seven-day source overlap; source accessions and stable rule/evidence fingerprints deduplicate repeats. Missing metrics, failed source loads and incomplete histories stay unknown. A watchlist rule with an empty watchlist does not broaden into all companies.

Monitoring uses the shared deterministic `FU_ANALYST` engine:

- Filing and release alerts contain the actual source event plus financial rows available as of that event's publication date. Current values and aligned YoY changes have exact evidence and full derivation-input records. The wording explicitly allows a reporting period older than the event and does not claim that every event changed the financial statements.
- Standing-rule matches retain source observations and evidence. Insider transaction shares/price/value have Form 4 source records and separate transaction/filing dates. Price drawdown is labeled a calculation from daily price history; a Form 4 is not represented as verification of market prices.
- Thesis alerts retain measured status, score, criteria counts, observations and evidence. Insufficient criteria remain unknown.
- Morning briefs are keyed by the user's local calendar date and scheduled hour; source events have a stated date window, financial periods and source links remain explicit, and zero-new-event companies are described honestly.

Alerts/outbox use `(user_id,event_key)` conflicts with ignore-duplicates. Persistence uses batches of 40, with deadline/lease checks between batches; partial retries are safe. The monitor state records the start of user processing and next due time. An error does not falsely advance successful checking state.

## Delivery reliability

Email is optional and requires both RESEND_API_KEY and MONITORING_FROM_EMAIL. Without them, in-app alert generation remains functional.

Before **every attempt**, dispatch reads current preferences and the auth-admin user record. It requires the current verified email and email opt-in, honors morning/filing/release preferences, and suppresses removed/paused standing rules and removed theses. An address change after an initial attempt requires review rather than changing an ambiguous retry's recipient.

Outbox claims compare both status and attempts, so losing a claim cannot send. Every provider call uses `filedunder-monitoring/<outbox UUID>` as its idempotency key. The first-attempt sender, recipient, subject and text are stored as immutable delivery payload; retries reuse those bytes. Successful acknowledgements are recorded as `accepted` with provider ID, not falsely as delivered.

Transport ambiguity and retryable provider failures enter failed/retry state. Nonretryable errors, five attempts, missing first-attempt metadata, or an ambiguous outcome older than 23 hours enter `needs_review`. This stays inside Resend's documented 24-hour idempotency retention. An accepted-provider/database-write crash can retry only with the same key and payload. Long-running attempts have a 15-second timeout; normal storage/auth calls have 12-second timeouts. Remaining run budget limits dispatch count and leases are checked between attempts.

Plain-text emails show reported values, comparable YoY changes and source URLs resolved recursively through derived evidence. Long email excerpts are explicitly labeled truncated, with the full alert retained in-app. Alert UI shows company/status, event links, readable numeric tables, source links, dates and coverage notes. Raw technical JSON is collapsed secondary content.

## Required environment/configuration

- Existing `SUPABASE_URL`.
- `SUPABASE_PUBLISHABLE_KEY` (preferred) or a legacy `SUPABASE_ANON_KEY` whose JWT role is anon.
- Existing `SUPABASE_SERVICE_ROLE_KEY`, server-side only.
- `CRON_SECRET`.
- Optional `RESEND_API_KEY` and `MONITORING_FROM_EMAIL` for actual email delivery.
- Supabase Auth redirect allowlist must include the deployed application URL. Email templates determine whether recipients receive a magic link, code, or both; the UI supports both.
- Root migration `supabase/migrations/20261104184603_research_monitoring_and_leases.sql` supplies tables, RLS, revision trigger, due-settings RPC and lease RPCs. This worker did not execute it.

## Validation

```
node --test tests/monitoring/*.test.mjs
17 passed, 0 failed

node --test tests/evidence/*.test.mjs tests/assistant/*.test.mjs tests/monitoring/*.test.mjs
36 passed, 0 failed
```

Tests exercise actual functions with mocked boundary requests: public-key isolation; server-verified ownership and user-token REST; setting/rule constraints; revision conflicts; missing-data uncertainty; stale-source filtering; event/outbox deduplication; verified-recipient and opt-in gates; identical idempotency retries; expired ambiguous outcomes; lost claims; exclusive leases; broad-scope budgets; auth callback stripping/verification; refresh rotation; explicit save; derivation source closure and comparable YoY email content; release opt-out; and readable alert presentation.

No live delivery was tested. Browser sign-in/provider deployment readiness remains dependent on configured project redirect/templates/public key and delivery sender; the code reports configuration gaps rather than inventing successful delivery. Root is responsible for full browser route QA.

## Documentation consulted

The Supabase skill was read and applied. Its generic live-query verification step was not followed because this task explicitly prohibited this worker from production mutations/calls. The requested changelog markdown fetch was attempted but the web tool rejected its content type; current official auth docs were used for relevant flow behavior.

- https://supabase.com/docs/guides/auth/auth-email-passwordless
- https://supabase.com/docs/reference/javascript/auth-getuser
- https://supabase.com/docs/reference/javascript/auth-refreshsession
- https://supabase.com/docs/reference/javascript/auth-verifyotp
- https://supabase.github.io/auth/
- https://resend.com/changelog/idempotency-keys

## Final integration review fixes

- A failed logout endpoint (including HTTP401 for an expired token) no longer interrupts the signed-out redraw. The local session clears, both sign-out handlers render email/code inputs, and the UI accurately distinguishes local sign-out from unconfirmed remote revocation. A panel-level regression reproduces logout401 and verifies there is no stale signed-in account state.
- Scheduled rules and thesis criteria that request metrics absent from standard/calculated statements now load `kpis/<ticker>.json` through a bounded per-run supplemental cache. When quarterly KPI values lack direct publication dates, `ir/<ticker>.json` is loaded once to obtain explicitly published/filed document dates. Fetch timestamps, generation timestamps, fiscal labels and period ends are never substituted for publication dates.
- The shared `FU_ANALYST.attachBusinessMetrics` adapter maps exact business metrics, normalizes percentages, and accepts the credit-card-loss alias only for its single explicit card net-charge-off dollar series. General credit provision is not substituted. Supplemental misses and missing eligible publication dates appear in coverage diagnostics; scheduled rules remain unknown where evidence is unavailable.
- Root added optional `{asOf}` to `FU_ANALYST.thesis`; monitoring now passes its evaluation date, preventing unknown-publication KPI records from appearing as measured scheduled thesis criteria.
- Source and supplemental caches are shared across users in one run, but user settings/ownership remain separate. Standard financial criteria do not trigger extra KPI requests.

Final focused validation: **23 monitoring tests passed**, plus the **19 evidence/assistant tests**, for **42 passed, 0 failed**. New regressions cover logout redraw, exact KPI mappings and normalized units, explicit publication-date use, unknown dates, missing card-specific coverage, standard-metric request avoidance, per-run dataset caching, and scheduled thesis uncertainty. No production calls or emails were sent.
