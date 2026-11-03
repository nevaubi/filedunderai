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
  foreach t in array array['companies','company_metrics','filings','facts','prices_daily','macro_observations','news_items','summaries','documents','insider_transactions'] loop
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
insert into storage.buckets (id, name, public) values ('raw', 'raw', false)
  on conflict (id) do nothing;

notify pgrst, 'reload schema';
