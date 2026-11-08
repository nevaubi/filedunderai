-- Additive, explicit migration. Application builds never execute this file.
-- Full-text search over stored IR documents (extracted page text and Docling tables), filled by etl/doc_index.py.
-- Service-role only: the app searches through /api/app-docsearch, which checks the user's session first.
begin;

create table public.doc_chunks (
  id bigint generated always as identity primary key,
  doc_id text not null check (length(doc_id) between 1 and 64),
  ticker text not null check (length(ticker) between 1 and 32),
  doc_type text,
  title text,
  url text,
  published date,
  period_end date,
  fiscal_label text,
  page integer,
  kind text not null default 'text' check (kind in ('text', 'table')),
  chunk integer not null check (chunk >= 0),
  content text not null check (length(content) between 1 and 8000),
  tsv tsvector generated always as (
    setweight(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(fiscal_label, '')), 'B') ||
    setweight(to_tsvector('english', content), 'A')
  ) stored,
  unique (doc_id, kind, chunk)
);
create index doc_chunks_tsv_idx on public.doc_chunks using gin (tsv);
create index doc_chunks_ticker_idx on public.doc_chunks (ticker, published desc);
create index doc_chunks_type_idx on public.doc_chunks (doc_type, published desc);

create table public.doc_index_state (
  doc_id text primary key check (length(doc_id) between 1 and 64),
  ticker text not null,
  source text not null check (source in ('text', 'docling', 'text+docling')),
  chunks integer not null default 0,
  chars integer not null default 0,
  indexed_at timestamptz not null default now()
);
create index doc_index_state_ticker_idx on public.doc_index_state (ticker);

alter table public.doc_chunks enable row level security;
alter table public.doc_index_state enable row level security;
revoke all on public.doc_chunks, public.doc_index_state from public, anon, authenticated;
grant select, insert, update, delete on public.doc_chunks, public.doc_index_state to service_role;

-- websearch syntax: quoted phrases, OR, -exclusions. Results ranked by ts_rank_cd, one snippet per chunk.
create function public.search_doc_chunks(
  q text,
  tickers text[] default null,
  doc_types text[] default null,
  since date default null,
  lim integer default 30
)
returns table (
  doc_id text, ticker text, doc_type text, title text, url text, published date, period_end date,
  fiscal_label text, page integer, kind text, snippet text, rank real
)
language sql stable security definer set search_path = '' as $$
  with query as (select websearch_to_tsquery('english', q) as tq),
  top as (
    select c.id, ts_rank_cd(c.tsv, query.tq) as rank
    from public.doc_chunks c, query
    where length(coalesce(q, '')) between 2 and 300
      and c.tsv @@ query.tq
      and (tickers is null or c.ticker = any(tickers))
      and (doc_types is null or c.doc_type = any(doc_types))
      and (since is null or c.published >= since)
    order by rank desc, c.published desc nulls last
    limit least(greatest(coalesce(lim, 30), 1), 100)
  )
  -- snippets only for the rows returned (ts_headline is the expensive part)
  select c.doc_id, c.ticker, c.doc_type, c.title, c.url, c.published, c.period_end, c.fiscal_label, c.page, c.kind,
         ts_headline('english', c.content, query.tq, 'MaxFragments=2, MaxWords=35, MinWords=12, StartSel=«, StopSel=»') as snippet,
         top.rank
  from top join public.doc_chunks c on c.id = top.id, query
  order by top.rank desc, c.published desc nulls last;
$$;
revoke all on function public.search_doc_chunks(text, text[], text[], date, integer) from public, anon, authenticated;
grant execute on function public.search_doc_chunks(text, text[], text[], date, integer) to service_role;

commit;
