-- Private team access for Popper's shared alpha memory.
-- Apply only after each worker has POPPER_SUPABASE_EMAIL/PASSWORD configured
-- and the dashboard sends a Supabase bearer token to the API.

begin;

create table if not exists public.team_members (
    user_id     uuid primary key references auth.users(id) on delete cascade,
    email       text not null unique,
    role        text not null default 'researcher' check (role in ('admin', 'researcher', 'viewer')),
    active      boolean not null default true,
    created_at  timestamptz not null default now()
);

alter table public.team_members enable row level security;

create or replace function public.is_popper_member()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select exists (
        select 1 from public.team_members
        where user_id = auth.uid() and active
    );
$$;

create or replace function public.can_popper_write()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select exists (
        select 1 from public.team_members
        where user_id = auth.uid() and active and role in ('admin', 'researcher')
    );
$$;

create or replace function public.is_popper_admin()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select exists (
        select 1 from public.team_members
        where user_id = auth.uid() and active and role = 'admin'
    );
$$;

revoke all on function public.is_popper_member() from public, anon;
revoke all on function public.can_popper_write() from public, anon;
revoke all on function public.is_popper_admin() from public, anon;
grant execute on function public.is_popper_member() to authenticated, service_role;
grant execute on function public.can_popper_write() to authenticated, service_role;
grant execute on function public.is_popper_admin() to authenticated, service_role;

revoke all on table public.team_members from anon;
grant select, insert, update, delete on table public.team_members to authenticated;

drop policy if exists popper_member_self_read on public.team_members;
create policy popper_member_self_read on public.team_members
for select to authenticated
using ((user_id = auth.uid() and active) or public.is_popper_admin());

drop policy if exists popper_admin_insert_members on public.team_members;
create policy popper_admin_insert_members on public.team_members
for insert to authenticated
with check (public.is_popper_admin());

drop policy if exists popper_admin_update_members on public.team_members;
create policy popper_admin_update_members on public.team_members
for update to authenticated
using (public.is_popper_admin())
with check (public.is_popper_admin());

drop policy if exists popper_admin_delete_members on public.team_members;
create policy popper_admin_delete_members on public.team_members
for delete to authenticated
using (public.is_popper_admin());

do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'hypotheses', 'specs', 'experiments', 'scorecards', 'verdicts',
        'research_events', 'feature_gaps'
    ] loop
        execute format('alter table public.%I enable row level security', table_name);
        execute format('revoke all on table public.%I from anon', table_name);
        execute format(
            'grant select, insert, update, delete on table public.%I to authenticated',
            table_name
        );
        execute format('drop policy if exists popper_team_select on public.%I', table_name);
        execute format(
            'create policy popper_team_select on public.%I for select to authenticated using (public.is_popper_member())',
            table_name
        );
        execute format('drop policy if exists popper_team_insert on public.%I', table_name);
        execute format(
            'create policy popper_team_insert on public.%I for insert to authenticated with check (public.can_popper_write())',
            table_name
        );
        execute format('drop policy if exists popper_team_update on public.%I', table_name);
        execute format(
            'create policy popper_team_update on public.%I for update to authenticated using (public.can_popper_write()) with check (public.can_popper_write())',
            table_name
        );
        execute format('drop policy if exists popper_admin_delete on public.%I', table_name);
        execute format(
            'create policy popper_admin_delete on public.%I for delete to authenticated using (public.is_popper_admin())',
            table_name
        );
    end loop;
end
$$;

comment on table public.team_members is
'Allowlist for Popper. Bootstrap the first admin through the SQL editor or service role after creating the Auth user.';

commit;
