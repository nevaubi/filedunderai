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

-- Statements workstream: financial statements as filed (SEC Financial Statement Data Sets), standardized templates.
-- One row per 10-K/10-Q submission of a covered company; restated figures in later filings stay with those filings.
create table if not exists fs_filings (
  accession         text primary key,
  ticker            text not null references companies(ticker) on delete cascade,
  cik               integer not null,
  registrant        text,
  form              text not null,
  period            date,
  fy                smallint,
  fp                text,
  fye               text,
  filed             date not null,
  accepted          timestamptz,
  prevrpt           boolean not null default false,
  sic               text,
  template          text,
  n_lines           integer,
  n_values          integer,
  std               jsonb,
  source_zip        text not null,
  retrieved_at      timestamptz not null default now()
);
create index if not exists fs_filings_ticker on fs_filings (ticker, filed desc);
create index if not exists fs_filings_period on fs_filings (ticker, period desc, form);

-- One row per rendered statement (R file) of a filing. lines: [line, tag, custom, plabel, negating, iord, crdr] in filed order.
-- facts: [tag, ddate, qtrs, uom, segments, coreg, value, footnote] for every value presented on that statement.
create table if not exists fs_reports (
  accession         text not null references fs_filings(accession) on delete cascade,
  report            smallint not null,
  stmt              text not null,
  inpth             boolean not null default false,
  rfile             text,
  lines             jsonb not null,
  facts             jsonb not null,
  primary key (accession, report)
);
create index if not exists fs_reports_stmt on fs_reports (stmt);

-- Standard taxonomy tags seen in covered filings: standard label and balance attributes (custom tags are kept in fs_reports.lines).
create table if not exists fs_tags (
  tag               text primary key,
  version           text not null,
  tlabel            text,
  datatype          text,
  iord              text,
  crdr              text
);

-- Standardized templates: which filed tags feed each template line, in priority order (documented in etl/stdmap.py).
create table if not exists fs_std_map (
  template          text not null,
  line_id           text not null,
  line_label        text not null,
  stmt              text not null,
  ord               smallint not null,
  tag               text not null,
  priority          smallint not null,
  sign              smallint not null default 1,
  primary key (template, line_id, tag)
);

do $$
declare t text;
begin
  foreach t in array array['fs_filings','fs_reports','fs_tags','fs_std_map'] loop
    execute format('alter table %I enable row level security', t);
    if not exists (select 1 from pg_policies where tablename = t and policyname = 'public read') then
      execute format('create policy "public read" on %I for select to anon, authenticated using (true)', t);
    end if;
  end loop;
end $$;
notify pgrst, 'reload schema';
