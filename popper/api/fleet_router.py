"""API route exposing the shared node fleet to any authenticated team member."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from popper import node_presence
from popper.security import TeamPrincipal, require_team_user

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


class SwitchRequest(BaseModel):
    switch: str = Field(pattern="^(on|off)$")


class ExploitRequest(BaseModel):
    exploit: str = Field(pattern="^(on|off)$")


class ProvidersRequest(BaseModel):
    providers_off: list[str] = Field(default_factory=list)


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
    # Mirror the RPC rule exactly: admins control any node, researchers only
    # their own. Enabling a toggle the database will refuse looks like a bug.
    for node in nodes:
        node["can_toggle"] = user.role == "admin" or (
            user.role == "researcher" and str(node.get("operator_id")) == str(user.user_id)
        )
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


@router.post("/{machine_id}/exploit")
def set_node_exploit(
    machine_id: str,
    req: ExploitRequest,
    user: TeamPrincipal = Depends(require_team_user),
) -> dict:
    """Toggle a node's CPU-only exploit engine, independently of its on/off switch.

    Exploit refinement burns cores but no subscription quota, so an operator
    parks it when they want their machine back without stopping research.
    """
    if user.role == "viewer":
        raise HTTPException(status_code=403, detail="viewer accounts cannot toggle nodes")
    error = node_presence.set_exploit(machine_id, req.exploit)
    if error:
        raise HTTPException(status_code=502, detail=error)
    return {"machine_id": machine_id, "exploit": req.exploit, "ok": True}


@router.post("/{machine_id}/providers")
def set_node_providers(
    machine_id: str,
    req: ProvidersRequest,
    user: TeamPrincipal = Depends(require_team_user),
) -> dict:
    """Park individual LLM providers on a node without stopping it.

    An operator who wants Claude back for their own work can park just Claude
    and keep contributing on ChatGPT and DeepSeek. Parking every provider is
    accepted here and made safe on the node, which falls back to its full
    detected set rather than stranding itself with no route.
    """
    if user.role == "viewer":
        raise HTTPException(status_code=403, detail="viewer accounts cannot toggle nodes")
    error = node_presence.set_providers(machine_id, req.providers_off)
    if error:
        raise HTTPException(status_code=502, detail=error)
    return {"machine_id": machine_id, "providers_off": sorted(req.providers_off), "ok": True}


@router.post("/{machine_id}/requeue")
def request_node_requeue(
    machine_id: str,
    user: TeamPrincipal = Depends(require_team_user),
) -> dict:
    """Ask this node to sweep the shared stranded-hypothesis backlog.

    The backlog is fleet-wide, not per-node: nothing records which machine
    stranded a hypothesis. Choosing a node chooses which machine does the work.
    The node picks the request up on its next loop, so a parked or offline
    machine simply acts on it when it next runs.
    """
    if user.role == "viewer":
        raise HTTPException(status_code=403, detail="viewer accounts cannot control nodes")
    error = node_presence.request_requeue(machine_id)
    if error:
        raise HTTPException(status_code=502, detail=error)
    return {"machine_id": machine_id, "requeue_requested": True, "ok": True}
