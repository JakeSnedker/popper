-- Cross-node presence. Every operator machine upserts one row so the team can
-- see which nodes are alive without opening anyone else's computer.
-- Apply after 0003_research_attribution.sql.

begin;

create table if not exists public.node_status (
    machine_id text primary key,
    operator_id uuid not null default auth.uid() references public.team_members(user_id),
    operator_label text not null default '',
    status text not null default 'unknown',
    release_id text not null default '',
    dataset_id text not null default '',
    last_error text,
    started_at timestamptz,
    heartbeat_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.node_status enable row level security;

create index if not exists idx_node_status_heartbeat
    on public.node_status(heartbeat_at desc);

-- Any active member (including viewers) may see the fleet.
drop policy if exists popper_node_status_select on public.node_status;
create policy popper_node_status_select on public.node_status
for select to authenticated
using (exists (
    select 1 from public.team_members tm
    where tm.user_id = auth.uid() and tm.active
));

-- A node may only create or move its own row, and only under its own identity.
drop policy if exists popper_node_status_insert on public.node_status;
create policy popper_node_status_insert on public.node_status
for insert to authenticated
with check (
    public.can_popper_write()
    and operator_id = auth.uid()
    and length(machine_id) > 0
);

drop policy if exists popper_node_status_update on public.node_status;
create policy popper_node_status_update on public.node_status
for update to authenticated
using (operator_id = auth.uid() and public.can_popper_write())
with check (operator_id = auth.uid());

-- Only an admin retires a machine.
drop policy if exists popper_node_status_delete on public.node_status;
create policy popper_node_status_delete on public.node_status
for delete to authenticated
using (public.is_popper_admin());

revoke all on table public.node_status from anon;
grant select, insert, update on table public.node_status to authenticated;

comment on table public.node_status is
'One row per operator machine. Heartbeat only; never holds credentials or market data.';
comment on column public.node_status.machine_id is
'Stable operator-configured node identifier. One operator may run several machines.';
comment on column public.node_status.status is
'Worker lifecycle: starting, running, researching, idle, provider_backoff, error_backoff, stopped, crashed.';

commit;
