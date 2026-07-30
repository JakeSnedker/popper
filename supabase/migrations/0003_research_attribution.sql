-- Immutable node/release/dataset/model attribution for team research telemetry.
-- Apply after 0002_team_auth_rls.sql and before setting POPPER_AUTH_REQUIRED=true.

begin;

alter table public.research_events
    add column if not exists operator_id uuid references public.team_members(user_id),
    add column if not exists machine_id text not null default 'legacy',
    add column if not exists model_ids jsonb not null default '[]'::jsonb,
    add column if not exists release_id text not null default 'legacy',
    add column if not exists dataset_id text not null default 'legacy';

alter table public.research_events
    alter column operator_id set default auth.uid();

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'research_events_model_ids_array'
          and conrelid = 'public.research_events'::regclass
    ) then
        alter table public.research_events
            add constraint research_events_model_ids_array
            check (jsonb_typeof(model_ids) = 'array');
    end if;
end
$$;

create index if not exists idx_research_events_operator_created
    on public.research_events(operator_id, created_at);
create index if not exists idx_research_events_machine_created
    on public.research_events(machine_id, created_at);

-- Runtime events are append-only. The authenticated operator UUID is supplied
-- by auth.uid() and cannot be spoofed by another team member.
drop policy if exists popper_team_insert on public.research_events;
create policy popper_team_insert on public.research_events
for insert to authenticated
with check (
    public.can_popper_write()
    and operator_id = auth.uid()
    and length(machine_id) > 0
    and length(release_id) > 0
    and length(dataset_id) > 0
);

drop policy if exists popper_team_update on public.research_events;
revoke update on table public.research_events from authenticated;

create or replace function public.popper_access_probe()
returns table(user_id uuid, email text, role text, can_write boolean, is_admin boolean)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select tm.user_id, tm.email, tm.role,
           tm.role in ('admin', 'researcher'), tm.role = 'admin'
    from public.team_members tm
    where tm.user_id = auth.uid() and tm.active;
$$;

revoke all on function public.popper_access_probe() from public, anon;
grant execute on function public.popper_access_probe() to authenticated, service_role;

comment on column public.research_events.operator_id is
'Authenticated Supabase Auth user. Defaults to auth.uid() and is enforced by insert RLS.';
comment on column public.research_events.machine_id is
'Stable operator-configured node identifier; never a credential.';
comment on column public.research_events.model_ids is
'Actual provider:model routes recorded for this event when an LLM call occurred.';
comment on column public.research_events.release_id is
'Popper tag/commit/package identifier running on the node.';
comment on column public.research_events.dataset_id is
'Versioned dataset identity including its aggregate hash.';

commit;
