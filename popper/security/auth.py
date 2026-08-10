"""Supabase bearer-token verification for the private Popper team."""

from base64 import urlsafe_b64decode
from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
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


def _subject(token: str) -> str:
    """The `sub` claim of a Supabase JWT, used only to scope the roster lookup.

    Deliberately does NOT verify the signature: Supabase does that when it serves
    the request below, and rejects a tampered token outright. A forged `sub` can
    therefore only ever select a row the real token was never allowed to fetch,
    which comes back empty rather than authenticated.
    """
    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        subject = json.loads(urlsafe_b64decode(padded).decode("utf-8"))["sub"]
    except (IndexError, ValueError, KeyError, UnicodeDecodeError) as exc:
        raise AuthFailure("malformed Supabase bearer token") from exc
    if not isinstance(subject, str) or not subject:
        raise AuthFailure("Supabase token carries no subject")
    return subject


def _bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthFailure("missing Supabase bearer token")
    return token.strip()


def verify_team_token(token: str, timeout: int = 10) -> TeamPrincipal:
    """Let Supabase verify the JWT and RLS-confirm active team membership."""
    if not settings.supabase_url or not settings.supabase_key:
        raise AuthFailure("Supabase authentication is not configured")
    # Scope the lookup to the caller. Team members are allowed to READ the whole
    # roster (they need to see teammates), so an unfiltered `limit=1` returns an
    # ARBITRARY row and hands the caller whatever role sorts first: it demoted the
    # admin to researcher and would have handed the viewer account write access.
    # Role must come from the caller's own row or not at all.
    subject = _subject(token)
    url = (
        f"{settings.supabase_url.rstrip('/')}/rest/v1/team_members"
        f"?select=user_id,email,role,active&active=eq.true&user_id=eq.{quote(subject)}&limit=1"
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
    # Belt and braces: PostgREST verified the token's signature before returning
    # anything, so `subject` is authentic by the time we get here -- but never let
    # a filter mismatch through silently.
    if row["user_id"] != subject:
        raise AuthFailure("token subject does not match the returned team member")
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
