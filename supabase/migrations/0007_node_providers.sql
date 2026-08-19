-- Per-provider parking, shared with the dashboard.
-- The node switch is all-or-nothing: an operator who wants Claude back for
-- their own work had to stop contributing entirely. Parking one provider lets
-- the node keep running on the other two.
--
-- Stores the PARKED set, not the running one, so an empty value means "nothing
-- parked" and every existing row keeps its current behaviour. The node can only
-- subtract from the providers it actually detects, so a name in here for a
-- provider the operator never had is inert.
--
-- Apply after 0006_node_exploit_and_requeue.sql.

begin;

alter table public.node_status
    add column if not exists providers_off text not null default ''
    check (providers_off ~ '^(|(claude_cli|codex_cli|opencode_cli)(,(claude_cli|codex_cli|opencode_cli))*)$');

alter table public.node_status
    add column if not exists providers_requested_at timestamptz;

-- Same ownership rule as popper_set_node_switch and popper_set_node_exploit:
-- a member controls a node they own, an admin controls any node.
--
-- This function deliberately does NOT refuse a request that parks everything.
-- The node cannot be stranded by it: enabled_providers() only ever subtracts
-- from what that machine detects, and falls back to its full detected set if
-- the subtraction would empty it. Enforcing "at least one" here would need the
-- database to know which CLIs a given machine is signed into, which it cannot.
create or replace function public.popper_set_node_providers(p_machine_id text, p_providers_off text)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_owner uuid;
    v_value text := coalesce(p_providers_off, '');
begin
    if v_value !~ '^(|(claude_cli|codex_cli|opencode_cli)(,(claude_cli|codex_cli|opencode_cli))*)$' then
        raise exception 'providers_off must be a comma-separated list of known providers';
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
    set providers_off = v_value,
        providers_requested_at = now(),
        updated_at = now()
    where machine_id = p_machine_id;
    return true;
end;
$$;

revoke all on function public.popper_set_node_providers(text, text) from public, anon;
grant execute on function public.popper_set_node_providers(text, text) to authenticated;

comment on column public.node_status.providers_off is
'Comma-separated LLM providers parked on this node. Empty means all detected providers run.';
comment on column public.node_status.providers_requested_at is
'When the parked-provider set was last changed from the dashboard or the node.';

commit;
