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

-- ---------------------------------------------------------------- macro workstream: generic series store
-- One row per series with provenance; observations keep every published vintage (point in time).
create table if not exists macro_series (
  id                text primary key,
  source            text not null,
  provider          text not null,
  title             text not null,
  units             text,
  frequency         text,
  seasonal_adj      text,
  geography         text,
  source_url        text,
  notes             text,
  data_url          text,
  source_key        text,
  section           text,
  release           text,
  meta              jsonb,
  stats             jsonb,
  first_date        date,
  last_date         date,
  last_value        numeric,
  obs_count         integer,
  updated_at        timestamptz not null default now()
);
create index if not exists macro_series_section on macro_series (section);

create table if not exists macro_series_obs (
  series_id         text not null references macro_series(id) on delete cascade,
  d                 date not null,
  vintage           date not null,
  value             numeric not null,
  published         date,
  retrieved_at      timestamptz not null default now(),
  primary key (series_id, d, vintage)
);
create index if not exists macro_series_obs_vintage on macro_series_obs (vintage desc);

-- Current value per observation date: the latest vintage.
create or replace view macro_series_latest with (security_invoker = true) as
  select distinct on (series_id, d) series_id, d, value, vintage, published, retrieved_at
  from macro_series_obs order by series_id, d, vintage desc;

-- Publisher release calendar (dates taken from the publisher's own schedule pages).
create table if not exists macro_releases (
  release           text not null,
  release_date      date not null,
  release_time      text,
  provider          text not null,
  title             text not null,
  source_url        text not null,
  retrieved_at      timestamptz not null default now(),
  primary key (release, release_date)
);

-- Inserts observations whose value differs from the latest stored vintage (or that are new).
-- Rows: [{"s": series_id, "d": "YYYY-MM-DD", "v": "1.23", "vt": vintage date, "p": published date or null}].
create or replace function macro_ingest(p_rows jsonb)
returns table(series_id text, d date) language plpgsql security definer set search_path = public as $$
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
end $$;
revoke all on function macro_ingest(jsonb) from public, anon, authenticated;

do $$
declare t text;
begin
  foreach t in array array['macro_series','macro_series_obs','macro_releases'] loop
    execute format('alter table %I enable row level security', t);
    if not exists (select 1 from pg_policies where tablename = t and policyname = 'public read') then
      execute format('create policy "public read" on %I for select to anon, authenticated using (true)', t);
    end if;
  end loop;
end $$;
notify pgrst, 'reload schema';
