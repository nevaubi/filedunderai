-- Additive, explicit migration. Application builds never execute this file.
-- Private user research and internal leases/outbox. No public financial tables are changed.
begin;

create table public.etl_leases (
  lock_name text primary key check (length(lock_name) between 1 and 160),
  owner text not null check (length(owner) between 1 and 160),
  expires_at timestamptz not null,
  updated_at timestamptz not null default now()
);
alter table public.etl_leases enable row level security;
revoke all on public.etl_leases from public, anon, authenticated;
grant select, insert, update, delete on public.etl_leases to service_role;

create function public.acquire_etl_lease(lock_name text, owner text, lease_seconds integer)
returns boolean language plpgsql security definer set search_path = '' as $$
declare acquired boolean;
begin
  if $1 is null or length($1) not between 1 and 160 or $2 is null or length($2) not between 1 and 160
     or $3 is null or $3 < 30 or $3 > 600 then
    raise exception 'Invalid lease name, owner or duration (30–600 seconds)';
  end if;
  insert into public.etl_leases as l(lock_name, owner, expires_at, updated_at)
  values ($1, $2, clock_timestamp() + make_interval(secs => $3), clock_timestamp())
  on conflict on constraint etl_leases_pkey do update
    set owner = excluded.owner, expires_at = excluded.expires_at, updated_at = excluded.updated_at
    where l.expires_at <= clock_timestamp() or l.owner = excluded.owner
  returning true into acquired;
  return coalesce(acquired, false);
end;
$$;
create function public.release_etl_lease(lock_name text, owner text)
returns boolean language plpgsql security definer set search_path = '' as $$
declare released boolean;
begin
  delete from public.etl_leases l where l.lock_name = $1 and l.owner = $2 returning true into released;
  return coalesce(released, false);
end;
$$;
revoke all on function public.acquire_etl_lease(text,text,integer), public.release_etl_lease(text,text) from public, anon, authenticated;
grant execute on function public.acquire_etl_lease(text,text,integer), public.release_etl_lease(text,text) to service_role;

create table public.research_settings (
  user_id uuid primary key references auth.users(id) on delete cascade,
  watchlist jsonb not null default '[]'::jsonb check (jsonb_typeof(watchlist) = 'array' and jsonb_array_length(watchlist) <= 100),
  rules jsonb not null default '[]'::jsonb check (jsonb_typeof(rules) = 'array' and jsonb_array_length(rules) <= 25),
  theses jsonb not null default '[]'::jsonb check (jsonb_typeof(theses) = 'array' and jsonb_array_length(theses) <= 50),
  email_enabled boolean not null default false,
  morning_enabled boolean not null default false,
  filing_alerts boolean not null default true,
  morning_hour integer not null default 8 check (morning_hour between 0 and 23),
  timezone text not null default 'UTC' check (length(timezone) between 1 and 100),
  revision bigint not null default 1,
  updated_at timestamptz not null default now(),
  check (octet_length(watchlist::text) + octet_length(rules::text) + octet_length(theses::text) <= 200000)
);
alter table public.research_settings enable row level security;
revoke all on public.research_settings from public, anon, authenticated;
grant select, delete on public.research_settings to authenticated;
grant insert(user_id,watchlist,rules,theses,email_enabled,morning_enabled,filing_alerts,morning_hour,timezone),
      update(watchlist,rules,theses,email_enabled,morning_enabled,filing_alerts,morning_hour,timezone)
      on public.research_settings to authenticated;
grant select, insert, update, delete on public.research_settings to service_role;
create policy research_settings_owner_select on public.research_settings for select to authenticated using ((select auth.uid()) = user_id);
create policy research_settings_owner_insert on public.research_settings for insert to authenticated with check ((select auth.uid()) = user_id);
create policy research_settings_owner_update on public.research_settings for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy research_settings_owner_delete on public.research_settings for delete to authenticated using ((select auth.uid()) = user_id);
create function public.research_settings_revision() returns trigger language plpgsql set search_path = '' as $$
begin
  new.revision := old.revision + 1;
  new.updated_at := clock_timestamp();
  return new;
end;
$$;
revoke all on function public.research_settings_revision() from public, anon, authenticated;
create trigger research_settings_revision before update on public.research_settings for each row execute function public.research_settings_revision();

create table public.research_alerts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.research_settings(user_id) on delete cascade,
  event_key text not null check (length(event_key) between 1 and 500),
  kind text not null check (kind in ('filing','release','rule','thesis','brief','coverage')),
  title text not null check (length(title) between 1 and 300),
  content jsonb not null default '{}'::jsonb,
  read_at timestamptz,
  created_at timestamptz not null default now(),
  unique (user_id, event_key)
);
create index research_alerts_owner_created on public.research_alerts(user_id, created_at desc);
alter table public.research_alerts enable row level security;
revoke all on public.research_alerts from public, anon, authenticated;
grant select, update(read_at) on public.research_alerts to authenticated;
grant select, insert, update, delete on public.research_alerts to service_role;
create policy research_alerts_owner_select on public.research_alerts for select to authenticated using ((select auth.uid()) = user_id);
create policy research_alerts_owner_update on public.research_alerts for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

create table public.research_monitor_state (
  user_id uuid primary key references public.research_settings(user_id) on delete cascade,
  last_checked_at timestamptz,
  last_brief_date date,
  next_check_at timestamptz not null default now(),
  last_error text,
  updated_at timestamptz not null default now()
);
create index research_monitor_due on public.research_monitor_state(next_check_at);
alter table public.research_monitor_state enable row level security;
revoke all on public.research_monitor_state from public, anon, authenticated;
grant select, insert, update, delete on public.research_monitor_state to service_role;

create table public.research_outbox (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.research_settings(user_id) on delete cascade,
  event_key text not null,
  subject text not null check (length(subject) between 1 and 300),
  content jsonb not null,
  status text not null default 'pending' check (status in ('pending','sending','accepted','failed','cancelled','needs_review')),
  attempts integer not null default 0 check (attempts >= 0),
  next_attempt_at timestamptz not null default now(),
  last_attempt_at timestamptz,
  last_error text,
  provider_id text,
  accepted_at timestamptz,
  created_at timestamptz not null default now(),
  unique(user_id,event_key)
);
create index research_outbox_due on public.research_outbox(next_attempt_at) where status in ('pending','failed','sending');
alter table public.research_outbox enable row level security;
revoke all on public.research_outbox from public, anon, authenticated;
grant select, insert, update, delete on public.research_outbox to service_role;

create function public.due_research_settings(p_limit integer default 25)
returns table(settings jsonb, last_checked_at timestamptz, last_brief_date date)
language sql stable security definer set search_path = '' as $$
  select to_jsonb(s), ms.last_checked_at, ms.last_brief_date
  from public.research_settings s
  left join public.research_monitor_state ms on ms.user_id = s.user_id
  where coalesce(ms.next_check_at, '-infinity'::timestamptz) <= now()
    and (jsonb_array_length(s.watchlist) > 0 or jsonb_array_length(s.rules) > 0 or jsonb_array_length(s.theses) > 0)
  order by coalesce(ms.next_check_at, '-infinity'::timestamptz), s.user_id
  limit greatest(1, least(coalesce(p_limit,25),50));
$$;
revoke all on function public.due_research_settings(integer) from public, anon, authenticated;
grant execute on function public.due_research_settings(integer) to service_role;

comment on table public.research_outbox is 'Verified opt-in monitoring email. accepted means provider acceptance, not inbox delivery. No unverified addresses are stored here.';
comment on table public.etl_leases is 'Short-lived mutually exclusive aggregate publication leases. Acquire and renew using the service-only RPC.';
commit;
