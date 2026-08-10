-- Operator-controlled on/off switch per node, shared with the dashboard.
-- A researcher/admin can park any node they own (admins any node) so it
-- stops burning subscription quota while the operator uses Claude themselves.
-- The worker mirrors the remote switch into its local `.popper/switch` file.
-- Apply after 0004_node_status.sql.

begin;

alter table public.node_status
    add column if not exists switch text not null default 'on'
    check (switch in ('on', 'off'));

alter table public.node_status
    add column if not exists switch_requested_at timestamptz;

-- Security-definer RPC: a member may toggle a node they own; an admin may
-- toggle any node. Viewers are rejected at the API layer (and cannot write
-- because the function requires can_popper_write / admin).
create or replace function public.popper_set_node_switch(p_machine_id text, p_switch text)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_owner uuid;
begin
    if p_switch not in ('on', 'off') then
        raise exception 'switch must be on or off';
    end if;
    select operator_id into v_owner
    from public.node_status
    where machine_id = p_machine_id;
    if v_owner is null then
        raise exception 'node not found';
    end if;
    if not (public.is_popper_admin() or v_owner = auth.uid()) then
        raise exception 'not allowed to control this machine';
    end if;
    update public.node_status
    set switch = p_switch,
        switch_requested_at = now(),
        updated_at = now()
    where machine_id = p_machine_id;
    return true;
end;
$$;

revoke all on function public.popper_set_node_switch(text, text) from public, anon;
grant execute on function public.popper_set_node_switch(text, text) to authenticated;

comment on column public.node_status.switch is
'Operator on/off switch. OFF parks the node after its current hunt (no new LLM calls); ON resumes.';
comment on column public.node_status.switch_requested_at is
'When the switch was last changed, for monitoring who/when a node was paused.';

commit;