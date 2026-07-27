"""API routes for explicitly running one missing hypothesis diagnostic."""

from fastapi import APIRouter, Depends, HTTPException

from popper.diagnostics import DiagnosticUnavailable, diagnostic_status, run_monte_carlo
from popper.security import TeamPrincipal, require_team_user

router = APIRouter(prefix="/api/hypotheses", tags=["diagnostics"])


@router.get("/{hypothesis_id}/diagnostics")
def list_diagnostics(
    hypothesis_id: str,
    _user: TeamPrincipal = Depends(require_team_user),
) -> dict:
    try:
        return diagnostic_status(hypothesis_id)
    except DiagnosticUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{hypothesis_id}/diagnostics/monte-carlo")
def execute_monte_carlo(
    hypothesis_id: str,
    _user: TeamPrincipal = Depends(require_team_user),
) -> dict:
    try:
        return run_monte_carlo(hypothesis_id)
    except DiagnosticUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
