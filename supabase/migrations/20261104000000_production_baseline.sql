-- Production baseline for the public schema, captured 2026-11-05 from pg_catalog on project apxfeteebnspwyajlxfh
-- (Postgres 17.11). It records tables that previously existed only in production (calls, call_*, guidance_*,
-- consensus_estimates, macro_series_latest and the rest of the public schema) so the database can be rebuilt
-- and reviewed from git.
--
-- Objects created by 20261104184603_research_monitoring_and_leases.sql are excluded here so that migration
-- still applies cleanly after this one on a fresh database.
--
-- PRODUCTION ALREADY HAS EVERYTHING IN THIS FILE. Before running `npm run migrate -- --apply` against the
-- linked production project, mark this version as applied once:
--   supabase migration repair --status applied 20261104000000
-- Otherwise `supabase db push` will try to re-create existing constraints and policies and fail.
--
-- Row-count and size comments at the end are informational only.

-- Filed Under production schema snapshot (public), generated 2026-11-05T21:36:54Z from pg_catalog, server 17.11

-- Extensions

create extension if not exists pg_stat_statements with schema extensions;

create extension if not exists pgcrypto with schema extensions;

create extension if not exists supabase_vault with schema vault;

create extension if not exists "uuid-ossp" with schema extensions;

-- Sequences

create sequence if not exists public.etl_runs_id_seq as bigint start 1 increment 1 minvalue 1 maxvalue 9223372036854775807 cache 1;

-- Tables

create table if not exists public.api_usage (
  bucket text not null,
  count integer default 0 not null,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.call_exchanges (
  call_id text not null,
  idx integer not null,
  analyst_idx integer,
  q_turns integer[],
  a_turns integer[],
  topics text[],
  concern text,
  concern_quote jsonb,
  deflected boolean,
  deflection_reason text,
  deflection_quote jsonb
);

create table if not exists public.call_items (
  call_id text not null,
  kind text not null,
  idx integer not null,
  theme text,
  sentiment numeric,
  confidence numeric,
  metric text,
  period text,
  value_low numeric,
  value_high numeric,
  unit text,
  text text,
  quote jsonb not null
);

create table if not exists public.call_speakers (
  call_id text not null,
  idx integer not null,
  name text not null,
  role text,
  firm text,
  kind text not null,
  turns integer,
  words integer,
  talk_share numeric,
  hedge_per_1k numeric,
  tone numeric
);

create table if not exists public.call_turns (
  call_id text not null,
  idx integer not null,
  speaker_idx integer not null,
  section text not null,
  s integer not null,
  e integer not null,
  page integer,
  words integer,
  text text
);

create table if not exists public.calls (
  call_id text not null,
  ticker text not null,
  doc_id text not null,
  alt_doc_ids text[],
  kind text not null,
  event_type text not null,
  title text,
  event_date date,
  fiscal_label text,
  period_end date,
  has_qa boolean default false not null,
  words integer,
  n_turns integer,
  n_exchanges integer,
  parse_method text,
  text_sha256 text,
  summary text,
  overall_tone numeric,
  analysis_model text,
  analyzed_at timestamp with time zone,
  source_url text,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.companies (
  ticker text not null,
  cik integer not null,
  name text not null,
  display_name text,
  sector text,
  sic text,
  sic_description text,
  exchange text,
  fiscal_year_end text,
  state_of_inc text,
  address jsonb,
  phone text,
  website text,
  ir_url text,
  shares_outstanding numeric,
  shares_as_of date,
  latest_accession text,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.company_metrics (
  ticker text not null,
  as_of date,
  price numeric,
  chg_1d numeric,
  ret_1m numeric,
  ret_ytd numeric,
  ret_1y numeric,
  market_cap numeric,
  pe_ttm numeric,
  ps_ttm numeric,
  eps_ttm numeric,
  revenue_ttm numeric,
  net_income_ttm numeric,
  fcf_ttm numeric,
  fcf_yield numeric,
  gross_margin_lq numeric,
  op_margin_lq numeric,
  net_margin_ttm numeric,
  rev_growth_lq numeric,
  dividend_yield numeric,
  buyback_yield numeric,
  beta_1y numeric,
  vol_60d numeric,
  high_52w numeric,
  low_52w numeric,
  latest_quarter text,
  latest_quarter_end date,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.consensus_estimates (
  ticker text not null,
  metric text not null,
  basis text default 'adjusted'::text not null,
  fiscal_label text not null,
  period_end date,
  as_of date not null,
  mean numeric,
  median numeric,
  high numeric,
  low numeric,
  n_estimates integer,
  unit text,
  provider text not null,
  source_url text,
  retrieved_at timestamp with time zone default now() not null
);

create table if not exists public.corporate_actions (
  symbol text not null,
  d date not null,
  kind text not null,
  value numeric,
  numerator numeric,
  denominator numeric,
  source_url text,
  retrieved_at timestamp with time zone
);

create table if not exists public.documents (
  id text not null,
  ticker text,
  accession text,
  kind text not null,
  source_url text,
  storage_path text not null,
  bytes integer,
  sha256 text,
  fetched_at timestamp with time zone default now() not null
);

create table if not exists public.etl_runs (
  id bigint default nextval('etl_runs_id_seq'::regclass) not null,
  job text not null,
  started_at timestamp with time zone default now() not null,
  finished_at timestamp with time zone,
  status text default 'running'::text not null,
  stats jsonb,
  error text
);

create table if not exists public.facts (
  ticker text not null,
  statement text not null,
  label text not null,
  kind text not null,
  period_end date not null,
  period_start date,
  concept text not null,
  unit text not null,
  value numeric not null,
  accession text not null,
  form text,
  filed date,
  derived boolean default false not null
);

create table if not exists public.filings (
  accession text not null,
  ticker text not null,
  form text not null,
  filed date not null,
  report_date date,
  primary_doc text,
  items text,
  description text,
  url text,
  has_source_tables boolean default false not null,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.fs_filings (
  accession text not null,
  ticker text not null,
  cik integer not null,
  registrant text,
  form text not null,
  period date,
  fy smallint,
  fp text,
  fye text,
  filed date not null,
  accepted timestamp with time zone,
  prevrpt boolean default false not null,
  sic text,
  template text,
  n_lines integer,
  n_values integer,
  std jsonb,
  source_zip text not null,
  retrieved_at timestamp with time zone default now() not null
);

create table if not exists public.fs_reports (
  accession text not null,
  report smallint not null,
  stmt text not null,
  inpth boolean default false not null,
  rfile text,
  lines jsonb not null,
  facts jsonb not null
);

create table if not exists public.fs_std_map (
  template text not null,
  line_id text not null,
  line_label text not null,
  stmt text not null,
  ord smallint not null,
  tag text not null,
  priority smallint not null,
  sign smallint default 1 not null
);

create table if not exists public.fs_tags (
  tag text not null,
  version text not null,
  tlabel text,
  datatype text,
  iord text,
  crdr text
);

create table if not exists public.guidance_credibility (
  ticker text not null,
  n_scored integer,
  n_met integer,
  hit_rate numeric,
  in_range_rate numeric,
  above_rate numeric,
  below_rate numeric,
  avg_surprise numeric,
  median_surprise numeric,
  raise_rate numeric,
  tendency text,
  definition_changes jsonb,
  n_series integer,
  n_points integer,
  first_issued date,
  last_issued date,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.guidance_docs (
  doc_id text not null,
  ticker text,
  issued date,
  version integer not null,
  status text,
  windows integer,
  items integer,
  rejected integer,
  reasons jsonb,
  method text,
  model text,
  error text,
  seconds numeric,
  processed_at timestamp with time zone default now() not null
);

create table if not exists public.guidance_items (
  id text not null,
  ticker text not null,
  kind text not null,
  metric text not null,
  label text,
  segment text,
  basis text not null,
  measure text not null,
  unit text,
  series_key text not null,
  period_type text not null,
  fiscal_year integer,
  quarter integer,
  fiscal_label text not null,
  period_end date,
  low numeric,
  high numeric,
  point numeric,
  mid numeric,
  bound text,
  "precision" numeric,
  status_words text,
  printed jsonb,
  issued date not null,
  doc_id text not null,
  page integer,
  quote text not null,
  method text not null,
  model text,
  action text,
  prev_id text,
  extracted_at timestamp with time zone default now() not null
);

create table if not exists public.guidance_outcomes (
  ticker text not null,
  series_key text not null,
  fiscal_label text not null,
  metric text not null,
  basis text,
  measure text,
  unit text,
  label text,
  segment text,
  period_type text,
  period_end date,
  initial_id text,
  final_id text,
  revisions integer,
  initial_mid numeric,
  final_low numeric,
  final_high numeric,
  final_mid numeric,
  actual numeric,
  actual_source text,
  actual_ref jsonb,
  reported_on date,
  surprise_abs numeric,
  surprise_rel numeric,
  result text,
  direction integer,
  met boolean,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.insider_transactions (
  accession text not null,
  seq integer not null,
  ticker text not null,
  cik bigint,
  filed date not null,
  owner text,
  owner_cik text,
  role text,
  tx_date date,
  code text,
  security text,
  shares numeric,
  price numeric,
  acq_disp text,
  owned_after numeric,
  ownership text,
  derivative boolean default false not null,
  plan_10b5 boolean default false not null
);

create table if not exists public.ir_documents (
  id text not null,
  ticker text not null,
  url text not null,
  title text,
  doc_type text,
  fiscal_label text,
  period_end date,
  published date,
  ext text,
  content_type text,
  bytes bigint,
  sha256 text,
  storage_path text,
  source_page text,
  pages integer,
  text_path text,
  extracted_at timestamp with time zone,
  fetched_at timestamp with time zone default now() not null
);

create table if not exists public.ir_sites (
  ticker text not null,
  ir_url text,
  results_url text,
  platform text,
  notes text,
  discovered_at timestamp with time zone default now() not null
);

create table if not exists public.kpi_canonical (
  id text not null,
  name text not null,
  family text,
  unit text,
  how text,
  definition text,
  higher_is_better boolean,
  match text,
  industries jsonb,
  taxonomy_version integer,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.kpi_definitions (
  ticker text not null,
  kpi_id text not null,
  name text not null,
  category text,
  subcategory text,
  unit text,
  parent_id text,
  sums_to_parent boolean default false not null,
  source_kind text default 'ir'::text not null,
  description text,
  sort integer,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.kpi_std_map (
  ticker text not null,
  kpi_id text not null,
  canonical_id text not null,
  scope text not null,
  member text,
  era text,
  role text,
  basis text,
  confidence numeric,
  method text,
  reason text,
  factor numeric,
  taxonomy_version integer,
  mapped_at timestamp with time zone default now() not null
);

create table if not exists public.kpi_values (
  ticker text not null,
  kpi_id text not null,
  period_kind text not null,
  period_end date not null,
  fiscal_label text,
  value numeric not null,
  unit text,
  source_kind text not null,
  source_id text not null,
  anchor jsonb,
  derived jsonb,
  reconciled boolean,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.macro_observations (
  series text not null,
  d date not null,
  value numeric not null
);

create table if not exists public.macro_releases (
  release text not null,
  release_date date not null,
  release_time text,
  provider text not null,
  title text not null,
  source_url text not null,
  retrieved_at timestamp with time zone default now() not null
);

create table if not exists public.macro_series (
  id text not null,
  source text not null,
  provider text not null,
  title text not null,
  units text,
  frequency text,
  seasonal_adj text,
  geography text,
  source_url text,
  notes text,
  data_url text,
  source_key text,
  section text,
  release text,
  meta jsonb,
  stats jsonb,
  first_date date,
  last_date date,
  last_value numeric,
  obs_count integer,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.macro_series_obs (
  series_id text not null,
  d date not null,
  vintage date not null,
  value numeric not null,
  published date,
  retrieved_at timestamp with time zone default now() not null
);

create table if not exists public.news_items (
  url text not null,
  ticker text not null,
  title text not null,
  source text,
  published_at timestamp with time zone,
  snippet text,
  summary_line text,
  fetched_at timestamp with time zone default now() not null
);

create table if not exists public.prices_daily (
  symbol text not null,
  d date not null,
  o numeric,
  h numeric,
  l numeric,
  c numeric not null,
  v bigint
);

create table if not exists public.prices_weekly (
  symbol text not null,
  d date not null,
  c numeric not null,
  adj numeric,
  source_url text,
  retrieved_at timestamp with time zone
);

create table if not exists public.return_peers (
  ticker text not null,
  rank integer not null,
  peer text not null,
  rho numeric,
  window_from date,
  window_to date,
  computed_at timestamp with time zone
);

create table if not exists public.sector_metrics (
  group_id text not null,
  metric text not null,
  quarter text not null,
  agg numeric,
  cw numeric,
  med numeric,
  n integer,
  updated_at timestamp with time zone default now() not null
);

create table if not exists public.sector_notes (
  group_id text not null,
  week date not null,
  content jsonb not null,
  generated_at timestamp with time zone default now() not null
);

create table if not exists public.summaries (
  id text not null,
  kind text not null,
  ticker text,
  content jsonb not null,
  generated_at timestamp with time zone default now() not null
);

-- Primary keys, unique and check constraints

alter table public.api_usage add constraint api_usage_pkey PRIMARY KEY (bucket);

alter table public.call_exchanges add constraint call_exchanges_pkey PRIMARY KEY (call_id, idx);

alter table public.call_items add constraint call_items_pkey PRIMARY KEY (call_id, kind, idx);

alter table public.call_items add constraint call_items_kind_check CHECK ((kind = ANY (ARRAY['theme'::text, 'guidance'::text, 'change'::text])));

alter table public.call_speakers add constraint call_speakers_pkey PRIMARY KEY (call_id, idx);

alter table public.call_speakers add constraint call_speakers_kind_check CHECK ((kind = ANY (ARRAY['management'::text, 'analyst'::text, 'operator'::text, 'other'::text])));

alter table public.call_turns add constraint call_turns_pkey PRIMARY KEY (call_id, idx);

alter table public.call_turns add constraint call_turns_section_check CHECK ((section = ANY (ARRAY['prepared'::text, 'qa'::text])));

alter table public.calls add constraint calls_pkey PRIMARY KEY (call_id);

alter table public.calls add constraint calls_kind_check CHECK ((kind = ANY (ARRAY['transcript'::text, 'remarks'::text])));

alter table public.companies add constraint companies_pkey PRIMARY KEY (ticker);

alter table public.companies add constraint companies_cik_key UNIQUE (cik);

alter table public.company_metrics add constraint company_metrics_pkey PRIMARY KEY (ticker);

alter table public.consensus_estimates add constraint consensus_estimates_pkey PRIMARY KEY (ticker, metric, basis, fiscal_label, provider, as_of);

alter table public.corporate_actions add constraint corporate_actions_pkey PRIMARY KEY (symbol, d, kind);

alter table public.corporate_actions add constraint corporate_actions_kind_check CHECK ((kind = ANY (ARRAY['dividend'::text, 'split'::text])));

alter table public.documents add constraint documents_pkey PRIMARY KEY (id);

alter table public.etl_runs add constraint etl_runs_pkey PRIMARY KEY (id);

alter table public.facts add constraint facts_pkey PRIMARY KEY (ticker, statement, label, kind, period_end);

alter table public.facts add constraint facts_kind_check CHECK ((kind = ANY (ARRAY['A'::text, 'Q'::text])));

alter table public.filings add constraint filings_pkey PRIMARY KEY (accession);

alter table public.fs_filings add constraint fs_filings_pkey PRIMARY KEY (accession);

alter table public.fs_reports add constraint fs_reports_pkey PRIMARY KEY (accession, report);

alter table public.fs_std_map add constraint fs_std_map_pkey PRIMARY KEY (template, line_id, tag);

alter table public.fs_tags add constraint fs_tags_pkey PRIMARY KEY (tag);

alter table public.guidance_credibility add constraint guidance_credibility_pkey PRIMARY KEY (ticker);

alter table public.guidance_docs add constraint guidance_docs_pkey PRIMARY KEY (doc_id);

alter table public.guidance_items add constraint guidance_items_pkey PRIMARY KEY (id);

alter table public.guidance_items add constraint guidance_items_kind_check CHECK ((kind = ANY (ARRAY['guide'::text, 'reported'::text, 'withdrawn'::text])));

alter table public.guidance_outcomes add constraint guidance_outcomes_pkey PRIMARY KEY (ticker, series_key, fiscal_label);

alter table public.insider_transactions add constraint insider_transactions_pkey PRIMARY KEY (accession, seq);

alter table public.ir_documents add constraint ir_documents_pkey PRIMARY KEY (id);

alter table public.ir_sites add constraint ir_sites_pkey PRIMARY KEY (ticker);

alter table public.kpi_canonical add constraint kpi_canonical_pkey PRIMARY KEY (id);

alter table public.kpi_definitions add constraint kpi_definitions_pkey PRIMARY KEY (ticker, kpi_id);

alter table public.kpi_std_map add constraint kpi_std_map_pkey PRIMARY KEY (ticker, kpi_id);

alter table public.kpi_std_map add constraint kpi_std_map_scope_check CHECK ((scope = ANY (ARRAY['total'::text, 'segment'::text, 'variant'::text])));

alter table public.kpi_values add constraint kpi_values_pkey PRIMARY KEY (ticker, kpi_id, period_kind, period_end, source_kind);

alter table public.kpi_values add constraint kpi_values_period_kind_check CHECK ((period_kind = ANY (ARRAY['Q'::text, 'A'::text, 'I'::text, 'Y'::text, 'T'::text])));

alter table public.macro_observations add constraint macro_observations_pkey PRIMARY KEY (series, d);

alter table public.macro_releases add constraint macro_releases_pkey PRIMARY KEY (release, release_date);

alter table public.macro_series add constraint macro_series_pkey PRIMARY KEY (id);

alter table public.macro_series_obs add constraint macro_series_obs_pkey PRIMARY KEY (series_id, d, vintage);

alter table public.news_items add constraint news_items_pkey PRIMARY KEY (url);

alter table public.prices_daily add constraint prices_daily_pkey PRIMARY KEY (symbol, d);

alter table public.prices_weekly add constraint prices_weekly_pkey PRIMARY KEY (symbol, d);

alter table public.return_peers add constraint return_peers_pkey PRIMARY KEY (ticker, rank);

alter table public.sector_metrics add constraint sector_metrics_pkey PRIMARY KEY (group_id, metric, quarter);

alter table public.sector_notes add constraint sector_notes_pkey PRIMARY KEY (group_id, week);

alter table public.summaries add constraint summaries_pkey PRIMARY KEY (id);

-- Foreign keys

alter table public.call_exchanges add constraint call_exchanges_call_id_fkey FOREIGN KEY (call_id) REFERENCES calls(call_id) ON DELETE CASCADE;

alter table public.call_items add constraint call_items_call_id_fkey FOREIGN KEY (call_id) REFERENCES calls(call_id) ON DELETE CASCADE;

alter table public.call_speakers add constraint call_speakers_call_id_fkey FOREIGN KEY (call_id) REFERENCES calls(call_id) ON DELETE CASCADE;

alter table public.call_turns add constraint call_turns_call_id_fkey FOREIGN KEY (call_id) REFERENCES calls(call_id) ON DELETE CASCADE;

alter table public.calls add constraint calls_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.company_metrics add constraint company_metrics_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.consensus_estimates add constraint consensus_estimates_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.facts add constraint facts_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.filings add constraint filings_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.fs_filings add constraint fs_filings_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.fs_reports add constraint fs_reports_accession_fkey FOREIGN KEY (accession) REFERENCES fs_filings(accession) ON DELETE CASCADE;

alter table public.guidance_credibility add constraint guidance_credibility_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.guidance_items add constraint guidance_items_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.guidance_outcomes add constraint guidance_outcomes_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.insider_transactions add constraint insider_transactions_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.ir_documents add constraint ir_documents_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.ir_sites add constraint ir_sites_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.kpi_definitions add constraint kpi_definitions_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.kpi_values add constraint kpi_values_ticker_fkey FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE;

alter table public.macro_series_obs add constraint macro_series_obs_series_id_fkey FOREIGN KEY (series_id) REFERENCES macro_series(id) ON DELETE CASCADE;

-- Indexes (excluding those backing constraints)

CREATE INDEX call_items_theme ON public.call_items USING btree (kind, theme);

CREATE INDEX call_speakers_name ON public.call_speakers USING btree (kind, name);

CREATE INDEX calls_ticker ON public.calls USING btree (ticker, event_date DESC);

CREATE INDEX documents_ticker ON public.documents USING btree (ticker, kind);

CREATE INDEX etl_runs_job ON public.etl_runs USING btree (job, started_at DESC);

CREATE INDEX facts_label ON public.facts USING btree (label, kind, period_end DESC);

CREATE INDEX filings_filed ON public.filings USING btree (filed DESC);

CREATE INDEX filings_form ON public.filings USING btree (form);

CREATE INDEX filings_ticker_filed ON public.filings USING btree (ticker, filed DESC);

CREATE INDEX fs_filings_period ON public.fs_filings USING btree (ticker, period DESC, form);

CREATE INDEX fs_filings_ticker ON public.fs_filings USING btree (ticker, filed DESC);

CREATE INDEX fs_reports_stmt ON public.fs_reports USING btree (stmt);

CREATE INDEX guidance_docs_ticker ON public.guidance_docs USING btree (ticker);

CREATE INDEX guidance_items_doc ON public.guidance_items USING btree (doc_id);

CREATE INDEX guidance_items_issued ON public.guidance_items USING btree (issued DESC);

CREATE INDEX guidance_items_series ON public.guidance_items USING btree (ticker, series_key, fiscal_label, issued);

CREATE INDEX insider_code ON public.insider_transactions USING btree (code, tx_date DESC);

CREATE INDEX insider_filed ON public.insider_transactions USING btree (filed DESC);

CREATE INDEX insider_ticker_date ON public.insider_transactions USING btree (ticker, tx_date DESC);

CREATE INDEX ir_docs_ticker ON public.ir_documents USING btree (ticker, period_end DESC NULLS LAST);

CREATE INDEX ir_docs_type ON public.ir_documents USING btree (doc_type);

CREATE INDEX kpi_std_map_canonical ON public.kpi_std_map USING btree (canonical_id, scope, role);

CREATE INDEX kpi_values_lookup ON public.kpi_values USING btree (kpi_id, period_kind, period_end DESC);

CREATE INDEX macro_series_section ON public.macro_series USING btree (section);

CREATE INDEX macro_series_obs_vintage ON public.macro_series_obs USING btree (vintage DESC);

CREATE INDEX news_ticker_pub ON public.news_items USING btree (ticker, published_at DESC);

CREATE INDEX sector_metrics_metric ON public.sector_metrics USING btree (metric, quarter);

-- Views and materialized views

create or replace view public.macro_series_latest as
 SELECT DISTINCT ON (series_id, d) series_id,
    d,
    value,
    vintage,
    published,
    retrieved_at
   FROM macro_series_obs
  ORDER BY series_id, d, vintage DESC;

-- Functions

CREATE OR REPLACE FUNCTION public.bump_usage(p_bucket text, p_limit integer)
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare n integer;
begin
  insert into api_usage(bucket, count, updated_at) values (p_bucket, 1, now())
  on conflict (bucket) do update set count = api_usage.count + 1, updated_at = now()
  returning count into n;
  return n;
end $function$
;

CREATE OR REPLACE FUNCTION public.macro_ingest(p_rows jsonb)
 RETURNS TABLE(series_id text, d date)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
#variable_conflict use_column
begin
  return query
  with r as (
    select distinct on (x->>'s', (x->>'d')::date) x->>'s' as s, (x->>'d')::date as dd, (x->>'v')::numeric as v,
           (x->>'vt')::date as vt, nullif(x->>'p', '')::date as p
    from jsonb_array_elements(p_rows) x
  ), cur as (
    select distinct on (o.series_id, o.d) o.series_id as cs, o.d as cd, o.value as cv
    from macro_series_obs o join r on r.s = o.series_id and r.dd = o.d
    order by o.series_id, o.d, o.vintage desc
  ), ins as (
    insert into macro_series_obs as m (series_id, d, vintage, value, published, retrieved_at)
    select r.s, r.dd, r.vt, r.v, r.p, now() from r left join cur on cur.cs = r.s and cur.cd = r.dd
    where cur.cs is null or cur.cv is distinct from r.v
    on conflict (series_id, d, vintage) do update set value = excluded.value,
      published = coalesce(excluded.published, m.published), retrieved_at = excluded.retrieved_at
    returning m.series_id, m.d
  )
  select ins.series_id, ins.d from ins;
end $function$
;

-- Triggers

-- Row level security

alter table public.api_usage enable row level security;

alter table public.call_exchanges enable row level security;

alter table public.call_items enable row level security;

alter table public.call_speakers enable row level security;

alter table public.call_turns enable row level security;

alter table public.calls enable row level security;

alter table public.companies enable row level security;

alter table public.company_metrics enable row level security;

alter table public.consensus_estimates enable row level security;

alter table public.corporate_actions enable row level security;

alter table public.documents enable row level security;

alter table public.etl_runs enable row level security;

alter table public.facts enable row level security;

alter table public.filings enable row level security;

alter table public.fs_filings enable row level security;

alter table public.fs_reports enable row level security;

alter table public.fs_std_map enable row level security;

alter table public.fs_tags enable row level security;

alter table public.guidance_credibility enable row level security;

alter table public.guidance_docs enable row level security;

alter table public.guidance_items enable row level security;

alter table public.guidance_outcomes enable row level security;

alter table public.insider_transactions enable row level security;

alter table public.ir_documents enable row level security;

alter table public.ir_sites enable row level security;

alter table public.kpi_canonical enable row level security;

alter table public.kpi_definitions enable row level security;

alter table public.kpi_std_map enable row level security;

alter table public.kpi_values enable row level security;

alter table public.macro_observations enable row level security;

alter table public.macro_releases enable row level security;

alter table public.macro_series enable row level security;

alter table public.macro_series_obs enable row level security;

alter table public.news_items enable row level security;

alter table public.prices_daily enable row level security;

alter table public.prices_weekly enable row level security;

alter table public.return_peers enable row level security;

alter table public.sector_metrics enable row level security;

alter table public.sector_notes enable row level security;

alter table public.summaries enable row level security;

-- Policies

create policy "public read" on public.call_exchanges as permissive for select to anon, authenticated using (true);

create policy "public read" on public.call_items as permissive for select to anon, authenticated using (true);

create policy "public read" on public.call_speakers as permissive for select to anon, authenticated using (true);

create policy "public read" on public.call_turns as permissive for select to anon, authenticated using (true);

create policy "public read" on public.calls as permissive for select to anon, authenticated using (true);

create policy "public read" on public.companies as permissive for select to anon, authenticated using (true);

create policy "public read" on public.company_metrics as permissive for select to anon, authenticated using (true);

create policy "public read" on public.consensus_estimates as permissive for select to anon, authenticated using (true);

create policy "public read" on public.corporate_actions as permissive for select to anon, authenticated using (true);

create policy "public read" on public.documents as permissive for select to anon, authenticated using (true);

create policy "public read" on public.facts as permissive for select to anon, authenticated using (true);

create policy "public read" on public.filings as permissive for select to anon, authenticated using (true);

create policy "public read" on public.fs_filings as permissive for select to anon, authenticated using (true);

create policy "public read" on public.fs_reports as permissive for select to anon, authenticated using (true);

create policy "public read" on public.fs_std_map as permissive for select to anon, authenticated using (true);

create policy "public read" on public.fs_tags as permissive for select to anon, authenticated using (true);

create policy "public read" on public.guidance_credibility as permissive for select to anon, authenticated using (true);

create policy "public read" on public.guidance_items as permissive for select to anon, authenticated using (true);

create policy "public read" on public.guidance_outcomes as permissive for select to anon, authenticated using (true);

create policy "public read" on public.insider_transactions as permissive for select to anon, authenticated using (true);

create policy "public read" on public.ir_documents as permissive for select to anon, authenticated using (true);

create policy "public read" on public.ir_sites as permissive for select to anon, authenticated using (true);

create policy "public read" on public.kpi_canonical as permissive for select to anon, authenticated using (true);

create policy "public read" on public.kpi_definitions as permissive for select to anon, authenticated using (true);

create policy "public read" on public.kpi_std_map as permissive for select to anon, authenticated using (true);

create policy "public read" on public.kpi_values as permissive for select to anon, authenticated using (true);

create policy "public read" on public.macro_observations as permissive for select to anon, authenticated using (true);

create policy "public read" on public.macro_releases as permissive for select to anon, authenticated using (true);

create policy "public read" on public.macro_series as permissive for select to anon, authenticated using (true);

create policy "public read" on public.macro_series_obs as permissive for select to anon, authenticated using (true);

create policy "public read" on public.news_items as permissive for select to anon, authenticated using (true);

create policy "public read" on public.prices_daily as permissive for select to anon, authenticated using (true);

create policy "public read" on public.prices_weekly as permissive for select to anon, authenticated using (true);

create policy "public read" on public.return_peers as permissive for select to anon, authenticated using (true);

create policy "public read" on public.sector_metrics as permissive for select to anon, authenticated using (true);

create policy "public read" on public.sector_notes as permissive for select to anon, authenticated using (true);

create policy "public read" on public.summaries as permissive for select to anon, authenticated using (true);

-- Table and view privileges for API roles (column-level grants listed separately)

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.api_usage to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.api_usage to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.api_usage to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_exchanges to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_exchanges to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_exchanges to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_items to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_items to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_items to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_speakers to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_speakers to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_speakers to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_turns to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_turns to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.call_turns to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.calls to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.calls to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.calls to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.companies to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.companies to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.companies to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.company_metrics to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.company_metrics to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.company_metrics to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.consensus_estimates to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.consensus_estimates to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.consensus_estimates to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.corporate_actions to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.corporate_actions to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.corporate_actions to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.documents to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.documents to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.documents to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.etl_runs to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.etl_runs to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.etl_runs to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.facts to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.facts to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.facts to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.filings to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.filings to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.filings to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_filings to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_filings to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_filings to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_reports to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_reports to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_reports to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_std_map to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_std_map to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_std_map to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_tags to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_tags to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.fs_tags to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_credibility to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_credibility to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_credibility to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_docs to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_docs to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_docs to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_items to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_items to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_items to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_outcomes to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_outcomes to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.guidance_outcomes to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.insider_transactions to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.insider_transactions to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.insider_transactions to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.ir_documents to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.ir_documents to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.ir_documents to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.ir_sites to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.ir_sites to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.ir_sites to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_canonical to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_canonical to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_canonical to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_definitions to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_definitions to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_definitions to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_std_map to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_std_map to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_std_map to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_values to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_values to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.kpi_values to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_observations to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_observations to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_observations to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_releases to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_releases to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_releases to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_series to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_series to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_series to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_series_latest to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_series_latest to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_series_latest to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_series_obs to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_series_obs to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.macro_series_obs to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.news_items to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.news_items to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.news_items to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.prices_daily to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.prices_daily to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.prices_daily to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.prices_weekly to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.prices_weekly to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.prices_weekly to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.return_peers to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.return_peers to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.return_peers to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.sector_metrics to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.sector_metrics to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.sector_metrics to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.sector_notes to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.sector_notes to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.sector_notes to service_role;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.summaries to anon;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.summaries to authenticated;

grant DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE on public.summaries to service_role;

-- Function execute privileges for API roles

grant execute on function public.bump_usage(p_bucket text, p_limit integer) to service_role;

grant execute on function public.macro_ingest(p_rows jsonb) to service_role;

-- Comments

-- Row counts (estimates, informational)

-- kpi_values: ~7321834 rows, 4584 MB

-- fs_reports: ~189428 rows, 855 MB

-- facts: ~2434521 rows, 604 MB

-- fs_filings: ~29282 rows, 268 MB

-- prices_daily: ~1657211 rows, 258 MB

-- kpi_definitions: ~393453 rows, 230 MB

-- filings: ~273687 rows, 88 MB

-- ir_documents: ~108539 rows, 65 MB

-- macro_series_obs: ~297700 rows, 47 MB

-- prices_weekly: ~119587 rows, 28 MB

-- guidance_docs: ~58730 rows, 25 MB

-- insider_transactions: ~45787 rows, 14 MB

-- sector_metrics: ~64267 rows, 12 MB

-- guidance_items: ~14579 rows, 11 MB

-- call_turns: ~6385 rows, 8328 kB

-- news_items: ~4870 rows, 6416 kB

-- kpi_std_map: ~11321 rows, 5576 kB

-- guidance_outcomes: ~4049 rows, 3776 kB

-- documents: ~5156 rows, 3696 kB

-- corporate_actions: ~7844 rows, 3248 kB

-- call_items: ~4577 rows, 2448 kB

-- summaries: ~502 rows, 1848 kB

-- macro_observations: ~9412 rows, 1600 kB

-- fs_tags: ~4171 rows, 1328 kB

-- return_peers: ~4960 rows, 960 kB

-- call_exchanges: ~892 rows, 832 kB

-- companies: ~1021 rows, 800 kB

-- macro_series: ~330 rows, 688 kB

-- company_metrics: ~800 rows, 520 kB

-- call_speakers: ~1627 rows, 496 kB

-- fs_std_map: ~1020 rows, 448 kB

-- calls: ~170 rows, 320 kB

-- guidance_credibility: ~499 rows, 248 kB

-- etl_runs: ~263 rows, 240 kB

-- ir_sites: ~500 rows, 216 kB

-- macro_releases: ~197 rows, 152 kB

-- kpi_canonical: ~75 rows, 104 kB

-- api_usage: ~28 rows, 64 kB

-- sector_notes: ~0 rows, 16 kB

-- consensus_estimates: ~0 rows, 16 kB

-- database size: 7547 MB
