"""API route exposing the shared node fleet to any authenticated team member."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from popper import node_presence
from popper.security import TeamPrincipal, require_team_user

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


class SwitchRequest(BaseModel):
    switch: str = Field(pattern="^(on|off)$")


@router.get("")
def list_nodes(user: TeamPrincipal = Depends(require_team_user)) -> dict:
    """Every operator machine and whether its heartbeat is recent.

    Viewers may read this: knowing a node is down is not privileged research
    data, and it is the only way a non-writing member can flag a dead node.
    """
    try:
        nodes = node_presence.fleet()
    except Exception as exc:  # noqa: BLE001 - a dead fleet view must not 500 the dashboard.
        return {"nodes": [], "healthy": 0, "error": f"{type(exc).__name__}: {exc}"}
    for node in nodes:
        node["can_toggle"] = (user.role in {"admin", "researcher"})
    return {
        "nodes": nodes,
        "healthy": sum(1 for node in nodes if node.get("healthy")),
        "me": {"role": user.role, "user_id": user.user_id},
        "error": None,
    }


@router.post("/{machine_id}/switch")
def set_node_switch(
    machine_id: str,
    req: SwitchRequest,
    user: TeamPrincipal = Depends(require_team_user),
) -> dict:
    """Toggle a node's switch. Researchers/admins toggle any node; viewers are
    rejected at the middleware (read-only) and here for defence in depth. The
    shared Supabase RLS restricts an operator's write to their own node unless
    admin, so the effective scope stays per-operator."""
    if user.role == "viewer":
        raise HTTPException(status_code=403, detail="viewer accounts cannot toggle nodes")
    error = node_presence.set_switch(machine_id, req.switch)
    if error:
        raise HTTPException(status_code=502, detail=error)
    return {"machine_id": machine_id, "switch": req.switch, "ok": True}
