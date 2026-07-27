"""Supabase bearer-token verification for the private Popper team."""

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import Header, HTTPException, Request as FastAPIRequest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from popper.config import settings


@dataclass(frozen=True)
class TeamPrincipal:
    user_id: str
    email: str
    role: str


class AuthFailure(RuntimeError):
    pass


def _bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthFailure("missing Supabase bearer token")
    return token.strip()


def verify_team_token(token: str, timeout: int = 10) -> TeamPrincipal:
    """Let Supabase verify the JWT and RLS-confirm active team membership."""
    if not settings.supabase_url or not settings.supabase_key:
        raise AuthFailure("Supabase authentication is not configured")
    url = (
        f"{settings.supabase_url.rstrip('/')}/rest/v1/team_members"
        "?select=user_id,email,role,active&active=eq.true&limit=1"
    )
    request = Request(url, headers={
        "apikey": settings.supabase_key,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed configured origin
            rows = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise AuthFailure("invalid or expired Supabase session") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AuthFailure("Supabase authentication is temporarily unavailable") from exc
    if not rows:
        raise AuthFailure("user is not an active Popper team member")
    row = rows[0]
    return TeamPrincipal(user_id=row["user_id"], email=row["email"], role=row["role"])


def require_team_user(authorization: str | None = Header(default=None)) -> TeamPrincipal:
    if not settings.auth_required:
        return TeamPrincipal(user_id="local", email="local@popper", role="admin")
    try:
        return verify_team_token(_bearer(authorization))
    except AuthFailure as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


class TeamAuthMiddleware(BaseHTTPMiddleware):
    """Protect API routes while leaving health checks and CORS preflight public."""

    PUBLIC_PATHS = {"/api/health", "/api/auth/config"}

    async def dispatch(self, request: FastAPIRequest, call_next):
        if (not settings.auth_required or request.method == "OPTIONS"
                or request.url.path in self.PUBLIC_PATHS
                or not request.url.path.startswith("/api/")):
            return await call_next(request)
        try:
            principal = verify_team_token(_bearer(request.headers.get("authorization")))
            request.state.team_user = principal
        except AuthFailure as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)
        if request.method not in {"GET", "HEAD"} and principal.role == "viewer":
            return JSONResponse({"detail": "viewer accounts are read-only"}, status_code=403)
        return await call_next(request)
