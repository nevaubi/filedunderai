-- Filed Under schema. Idempotent: safe to run on every deploy.
-- Public market data is readable by the anon role; only the service role writes.

create table if not exists companies (
  ticker            text primary key,
  cik               integer not null unique,
  name              text not null,
  display_name      text,
  sector            text,
  sic               text,
  sic_description   text,
  exchange          text,
  fiscal_year_end   text,
  state_of_inc      text,
  address           jsonb,
  phone             text,
  website           text,
  ir_url            text,
  shares_outstanding numeric,
  shares_as_of      date,
  latest_accession  text,
  updated_at        timestamptz not null default now()
);

create table if not exists company_metrics (
  ticker            text primary key references companies(ticker) on delete cascade,
  as_of             date,
  price             numeric,
  chg_1d            numeric,
  ret_1m            numeric,
  ret_ytd           numeric,
  ret_1y            numeric,
  market_cap        numeric,
  pe_ttm            numeric,
  ps_ttm            numeric,
  eps_ttm           numeric,
  revenue_ttm       numeric,
  net_income_ttm    numeric,
  fcf_ttm           numeric,
  fcf_yield         numeric,
  gross_margin_lq   numeric,
  op_margin_lq      numeric,
  net_margin_ttm    numeric,
  rev_growth_lq     numeric,
  dividend_yield    numeric,
  buyback_yield     numeric,
  beta_1y           numeric,
  vol_60d           numeric,
  high_52w          numeric,
  low_52w           numeric,
  latest_quarter    text,
  latest_quarter_end date,
  updated_at        timestamptz not null default now()
);

create table if not exists filings (
  accession         text primary key,
  ticker            text not null references companies(ticker) on delete cascade,
  form              text not null,
  filed             date not null,
  report_date       date,
  primary_doc       text,
  items             text,
  description       text,
  url               text,
  has_source_tables boolean not null default false,
  updated_at        timestamptz not null default now()
);
create index if not exists filings_ticker_filed on filings (ticker, filed desc);
create index if not exists filings_filed on filings (filed desc);
create index if not exists filings_form on filings (form);

create table if not exists facts (
  ticker            text not null references companies(ticker) on delete cascade,
  statement         text not null,
  label             text not null,
  kind              text not null check (kind in ('A','Q')),
  period_end        date not null,
  period_start      date,
  concept           text not null,
  unit              text not null,
  value             numeric not null,
  accession         text not null,
  form              text,
  filed             date,
  derived           boolean not null default false,
  primary key (ticker, statement, label, kind, period_end)
);
create index if not exists facts_label on facts (label, kind, period_end desc);

create table if not exists prices_daily (
  symbol            text not null,
  d                 date not null,
  o numeric, h numeric, l numeric, c numeric not null, v bigint,
  primary key (symbol, d)
);

create table if not exists macro_observations (
  series            text not null,
  d                 date not null,
  value             numeric not null,
  primary key (series, d)
);

create table if not exists news_items (
  url               text primary key,
  ticker            text not null,
  title             text not null,
  source            text,
  published_at      timestamptz,
  snippet           text,
  summary_line      text,
  fetched_at        timestamptz not null default now()
);
create index if not exists news_ticker_pub on news_items (ticker, published_at desc);

create table if not exists insider_transactions (
  accession         text not null,
  seq               integer not null,
  ticker            text not null references companies(ticker) on delete cascade,
  cik               bigint,
  filed             date not null,
  owner             text,
  owner_cik         text,
  role              text,
  tx_date           date,
  code              text,
  security          text,
  shares            numeric,
  price             numeric,
  acq_disp          text,
  owned_after       numeric,
  ownership         text,
  derivative        boolean not null default false,
  plan_10b5         boolean not null default false,
  primary key (accession, seq)
);
create index if not exists insider_ticker_date on insider_transactions (ticker, tx_date desc);
create index if not exists insider_filed on insider_transactions (filed desc);
create index if not exists insider_code on insider_transactions (code, tx_date desc);

-- Investor relations: sites, stored documents, KPI taxonomy and values (XBRL dimensions and IR documents).
create table if not exists ir_sites (
  ticker            text primary key references companies(ticker) on delete cascade,
  ir_url            text,
  results_url       text,
  platform          text,
  notes             text,
  discovered_at     timestamptz not null default now()
);

create table if not exists ir_documents (
  id                text primary key,
  ticker            text not null references companies(ticker) on delete cascade,
  url               text not null,
  title             text,
  doc_type          text,
  fiscal_label      text,
  period_end        date,
  published         date,
  ext               text,
  content_type      text,
  bytes             bigint,
  sha256            text,
  storage_path      text,
  source_page       text,
  pages             integer,
  text_path         text,
  extracted_at      timestamptz,
  fetched_at        timestamptz not null default now()
);
create index if not exists ir_docs_ticker on ir_documents (ticker, period_end desc nulls last);
create index if not exists ir_docs_type on ir_documents (doc_type);

create table if not exists kpi_definitions (
  ticker            text not null references companies(ticker) on delete cascade,
  kpi_id            text not null,
  name              text not null,
  category          text,
  subcategory       text,
  unit              text,
  parent_id         text,
  sums_to_parent    boolean not null default false,
  source_kind       text not null default 'ir',
  description       text,
  sort              integer,
  updated_at        timestamptz not null default now(),
  primary key (ticker, kpi_id)
);

create table if not exists kpi_values (
  ticker            text not null references companies(ticker) on delete cascade,
  kpi_id            text not null,
  period_kind       text not null check (period_kind in ('Q','A','I','Y','T')),
  period_end        date not null,
  fiscal_label      text,
  value             numeric not null,
  unit              text,
  source_kind       text not null,
  source_id         text not null,
  anchor            jsonb,
  derived           jsonb,
  reconciled        boolean,
  updated_at        timestamptz not null default now(),
  primary key (ticker, kpi_id, period_kind, period_end, source_kind)
);
create index if not exists kpi_values_lookup on kpi_values (kpi_id, period_kind, period_end desc);

create table if not exists summaries (
  id                text primary key,
  kind              text not null,
  ticker            text,
  content           jsonb not null,
  generated_at      timestamptz not null default now()
);

create table if not exists documents (
  id                text primary key,
  ticker            text,
  accession         text,
  kind              text not null,
  source_url        text,
  storage_path      text not null,
  bytes             integer,
  sha256            text,
  fetched_at        timestamptz not null default now()
);
create index if not exists documents_ticker on documents (ticker, kind);

create table if not exists etl_runs (
  id                bigserial primary key,
  job               text not null,
  started_at        timestamptz not null default now(),
  finished_at       timestamptz,
  status            text not null default 'running',
  stats             jsonb,
  error             text
);
create index if not exists etl_runs_job on etl_runs (job, started_at desc);

create table if not exists api_usage (
  bucket            text primary key,
  count             integer not null default 0,
  updated_at        timestamptz not null default now()
);

-- Atomic counter used by the API routes for per-IP and global daily limits.
create or replace function bump_usage(p_bucket text, p_limit integer)
returns integer language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  insert into api_usage(bucket, count, updated_at) values (p_bucket, 1, now())
  on conflict (bucket) do update set count = api_usage.count + 1, updated_at = now()
  returning count into n;
  return n;
end $$;
revoke all on function bump_usage(text, integer) from public, anon, authenticated;

-- Row level security: read-only public data.
do $$
declare t text;
begin
  foreach t in array array['companies','company_metrics','filings','facts','prices_daily','macro_observations','news_items','summaries','documents','insider_transactions','ir_sites','ir_documents','kpi_definitions','kpi_values'] loop
    execute format('alter table %I enable row level security', t);
    if not exists (select 1 from pg_policies where tablename = t and policyname = 'public read') then
      execute format('create policy "public read" on %I for select to anon, authenticated using (true)', t);
    end if;
  end loop;
  execute 'alter table etl_runs enable row level security';
  execute 'alter table api_usage enable row level security';
end $$;

-- Storage buckets: public-data serves the site's JSON; raw keeps source documents.
insert into storage.buckets (id, name, public) values ('public-data', 'public-data', true)
  on conflict (id) do update set public = true;
insert into storage.buckets (id, name, public) values ('ir', 'ir', true)
  on conflict (id) do update set public = true;
insert into storage.buckets (id, name, public) values ('raw', 'raw', false)
  on conflict (id) do nothing;

notify pgrst, 'reload schema';

-- year-to-date values (6M / 9M) for KPIs that companies only print cumulatively
alter table kpi_values drop constraint if exists kpi_values_period_kind_check;
alter table kpi_values add constraint kpi_values_period_kind_check check (period_kind in ('Q','A','I','Y','T'));

-- ---------------------------------------------------------------- compare workstream
-- Five years of weekly closes per symbol (split-adjusted close and split-and-dividend-adjusted close), with provenance.
create table if not exists prices_weekly (
  symbol            text not null,
  d                 date not null,
  c                 numeric not null,
  adj               numeric,
  source_url        text,
  retrieved_at      timestamptz,
  primary key (symbol, d)
);
-- Dividends (ex-date, split-adjusted amount per share) and splits (numerator / denominator) as published by the source.
create table if not exists corporate_actions (
  symbol            text not null,
  d                 date not null,
  kind              text not null check (kind in ('dividend','split')),
  value             numeric,
  numerator         numeric,
  denominator       numeric,
  source_url        text,
  retrieved_at      timestamptz,
  primary key (symbol, d, kind)
);
-- Ten most return-correlated companies per company, recomputed daily from the published daily closes.
create table if not exists return_peers (
  ticker            text not null,
  rank              int not null,
  peer              text not null,
  rho               numeric,
  window_from       date,
  window_to         date,
  computed_at       timestamptz,
  primary key (ticker, rank)
);
do $$
declare t text;
begin
  foreach t in array array['prices_weekly','corporate_actions','return_peers'] loop
    execute format('alter table %I enable row level security', t);
    if not exists (select 1 from pg_policies where tablename = t and policyname = 'public read') then
      execute format('create policy "public read" on %I for select to anon, authenticated using (true)', t);
    end if;
  end loop;
end $$;

-- ---------------------------------------------------------------- workstream: sectors
-- Quarterly sector and industry aggregates (etl/sectors.py). group_id is the slug used in sectors/{slug}.json.
create table if not exists sector_metrics (
  group_id          text not null,
  metric            text not null,
  quarter           text not null,
  agg               numeric,
  cw                numeric,
  med               numeric,
  n                 integer,
  updated_at        timestamptz not null default now(),
  primary key (group_id, metric, quarter)
);
create index if not exists sector_metrics_metric on sector_metrics (metric, quarter);

-- Weekly written sector notes; content holds the text with {{fact}} tokens and the facts they resolve to.
create table if not exists sector_notes (
  group_id          text not null,
  week              date not null,
  content           jsonb not null,
  generated_at      timestamptz not null default now(),
  primary key (group_id, week)
);

do $$
declare t text;
begin
  foreach t in array array['sector_metrics','sector_notes'] loop
    execute format('alter table %I enable row level security', t);
    if not exists (select 1 from pg_policies where tablename = t and policyname = 'public read') then
      execute format('create policy "public read" on %I for select to anon, authenticated using (true)', t);
    end if;
  end loop;
end $$;
notify pgrst, 'reload schema';

-- ---------------------------------------------------------------- workstream: guidance
-- Quantitative guidance statements, adjusted actuals the company reported, and withdrawals, each with its verbatim quote,
-- document and page (etl/guidance.py). Values in printed units: dollars and counts in base units, per-share in dollars,
-- margins, rates and growth in percent. action / prev_id chain the revisions of one series and period.
create table if not exists guidance_items (
  id                text primary key,
  ticker            text not null references companies(ticker) on delete cascade,
  kind              text not null check (kind in ('guide','reported','withdrawn')),
  metric            text not null,
  label             text,
  segment           text,
  basis             text not null,
  measure           text not null,
  unit              text,
  series_key        text not null,
  period_type       text not null,
  fiscal_year       integer,
  quarter           integer,
  fiscal_label      text not null,
  period_end        date,
  low               numeric,
  high              numeric,
  point             numeric,
  mid               numeric,
  bound             text,
  precision         numeric,
  status_words      text,
  printed           jsonb,
  issued            date not null,
  doc_id            text not null,
  page              integer,
  quote             text not null,
  method            text not null,
  model             text,
  action            text,
  prev_id           text,
  extracted_at      timestamptz not null default now()
);
create index if not exists guidance_items_series on guidance_items (ticker, series_key, fiscal_label, issued);
create index if not exists guidance_items_issued on guidance_items (issued desc);
create index if not exists guidance_items_doc on guidance_items (doc_id);

-- Extraction log per document (resumable runs; version re-reads documents after an extractor change).
create table if not exists guidance_docs (
  doc_id            text primary key,
  ticker            text,
  issued            date,
  version           integer not null,
  status            text,
  windows           integer,
  items             integer,
  rejected          integer,
  reasons           jsonb,
  method            text,
  model             text,
  error             text,
  seconds           numeric,
  processed_at      timestamptz not null default now()
);
create index if not exists guidance_docs_ticker on guidance_docs (ticker);

-- The last guidance before the actual was reported, scored against the actual (XBRL facts, company-reported adjusted
-- figures or published KPI values; actual_ref holds the accession or document, page and quote).
create table if not exists guidance_outcomes (
  ticker            text not null references companies(ticker) on delete cascade,
  series_key        text not null,
  fiscal_label      text not null,
  metric            text not null,
  basis             text,
  measure           text,
  unit              text,
  label             text,
  segment           text,
  period_type       text,
  period_end        date,
  initial_id        text,
  final_id          text,
  revisions         integer,
  initial_mid       numeric,
  final_low         numeric,
  final_high        numeric,
  final_mid         numeric,
  actual            numeric,
  actual_source     text,
  actual_ref        jsonb,
  reported_on       date,
  surprise_abs      numeric,
  surprise_rel      numeric,
  result            text,
  direction         integer,
  met               boolean,
  updated_at        timestamptz not null default now(),
  primary key (ticker, series_key, fiscal_label)
);

-- Per-company guidance record.
create table if not exists guidance_credibility (
  ticker            text primary key references companies(ticker) on delete cascade,
  n_scored          integer,
  n_met             integer,
  hit_rate          numeric,
  in_range_rate     numeric,
  above_rate        numeric,
  below_rate        numeric,
  avg_surprise      numeric,
  median_surprise   numeric,
  raise_rate        numeric,
  tendency          text,
  definition_changes jsonb,
  n_series          integer,
  n_points          integer,
  first_issued      date,
  last_issued       date,
  updated_at        timestamptz not null default now()
);

-- Consensus estimates from a licensed feed (none connected yet). One row per provider snapshot.
create table if not exists consensus_estimates (
  ticker            text not null references companies(ticker) on delete cascade,
  metric            text not null,
  basis             text not null default 'adjusted',
  fiscal_label      text not null,
  period_end        date,
  as_of             date not null,
  mean              numeric,
  median            numeric,
  high              numeric,
  low               numeric,
  n_estimates       integer,
  unit              text,
  provider          text not null,
  source_url        text,
  retrieved_at      timestamptz not null default now(),
  primary key (ticker, metric, basis, fiscal_label, provider, as_of)
);

do $$
declare t text;
begin
  foreach t in array array['guidance_items','guidance_outcomes','guidance_credibility','consensus_estimates'] loop
    execute format('alter table %I enable row level security', t);
    if not exists (select 1 from pg_policies where tablename = t and policyname = 'public read') then
      execute format('create policy "public read" on %I for select to anon, authenticated using (true)', t);
    end if;
  end loop;
  execute 'alter table guidance_docs enable row level security';
end $$;
notify pgrst, 'reload schema';
