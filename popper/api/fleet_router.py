"""API route exposing the shared node fleet to any authenticated team member."""

from fastapi import APIRouter, Depends

from popper import node_presence
from popper.security import TeamPrincipal, require_team_user

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


@router.get("")
def list_nodes(_user: TeamPrincipal = Depends(require_team_user)) -> dict:
    """Every operator machine and whether its heartbeat is recent.

    Viewers may read this: knowing a node is down is not privileged research
    data, and it is the only way a non-writing member can flag a dead node.
    """
    try:
        nodes = node_presence.fleet()
    except Exception as exc:  # noqa: BLE001 - a dead fleet view must not 500 the dashboard.
        return {"nodes": [], "healthy": 0, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "nodes": nodes,
        "healthy": sum(1 for node in nodes if node.get("healthy")),
        "error": None,
    }
