"""Download routes for promoted strategies compiled by Strategy Forge."""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from popper.forge import ForgeUnsupported, forge_record
from popper.memory import get_memory
from popper.security import TeamPrincipal, require_team_user

router = APIRouter(prefix="/api/hypotheses", tags=["strategy-forge"])


def _bundle(hypothesis_id: str, targets=("pine", "mql5")):
    record = get_memory().record(hypothesis_id)
    if not record:
        raise HTTPException(status_code=404, detail="hypothesis not found")
    try:
        return forge_record(record, targets=targets)
    except ForgeUnsupported as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{hypothesis_id}/forge")
def forge_manifest(
    hypothesis_id: str,
    _user: TeamPrincipal = Depends(require_team_user),
) -> dict:
    bundle = _bundle(hypothesis_id)
    return {
        "hypothesis_id": bundle.hypothesis_id,
        "spec_id": bundle.spec_id,
        "files": list(bundle.files),
        "warnings": list(bundle.warnings),
    }


@router.get("/{hypothesis_id}/forge/{target}")
def download_target(
    hypothesis_id: str,
    target: str,
    _user: TeamPrincipal = Depends(require_team_user),
):
    normalized = {"pine": "pine", "pinescript": "pine", "mql5": "mql5"}.get(target.lower())
    if not normalized:
        raise HTTPException(status_code=404, detail="target must be pine or mql5")
    bundle = _bundle(hypothesis_id, targets=(normalized,))
    name, content = next((name, content) for name, content in bundle.files.items()
                         if name != "README.txt")
    media = "text/plain; charset=utf-8"
    return Response(content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{name}"',
    })


@router.get("/{hypothesis_id}/forge-download")
def download_bundle(
    hypothesis_id: str,
    _user: TeamPrincipal = Depends(require_team_user),
):
    bundle = _bundle(hypothesis_id)
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, content in bundle.files.items():
            archive.writestr(name, content)
    filename = f"popper_{hypothesis_id}_forge.zip"
    return Response(output.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
    })
