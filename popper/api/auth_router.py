"""Public authentication configuration and authenticated session probe."""

from fastapi import APIRouter, Depends

from popper.config import settings
from popper.security import TeamPrincipal, require_team_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/config")
def auth_config() -> dict:
    return {
        "required": settings.auth_required,
        "supabase_url": settings.supabase_url if settings.auth_required else "",
        "supabase_key": settings.supabase_key if settings.auth_required else "",
    }


@router.get("/me")
def current_user(user: TeamPrincipal = Depends(require_team_user)) -> dict:
    return {"user_id": user.user_id, "email": user.email, "role": user.role}
