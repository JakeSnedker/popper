from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from popper.security.auth import TeamAuthMiddleware, TeamPrincipal, require_team_user
from scripts.check_team_security import _is_permission_denied


MIGRATIONS = Path(__file__).parent.parent / "supabase" / "migrations"
SQL = (MIGRATIONS / "0002_team_auth_rls.sql").read_text(encoding="utf-8")
ATTRIBUTION_SQL = (MIGRATIONS / "0003_research_attribution.sql").read_text(encoding="utf-8")


def test_rls_migration_covers_every_shared_table():
    for table in ("hypotheses", "specs", "experiments", "scorecards", "verdicts",
                  "research_events", "feature_gaps"):
        assert f"'{table}'" in SQL
    assert "enable row level security" in SQL
    assert "revoke all on table public.%I from anon" in SQL
    assert "public.is_popper_member()" in SQL
    assert "public.can_popper_write()" in SQL
    assert "public.is_popper_admin()" in SQL
    assert "role in ('admin', 'researcher')" in SQL


def test_attribution_migration_is_auth_owned_and_runtime_events_are_append_only():
    for column in ("operator_id", "machine_id", "model_ids", "release_id", "dataset_id"):
        assert f"add column if not exists {column}" in ATTRIBUTION_SQL
    assert "alter column operator_id set default auth.uid()" in ATTRIBUTION_SQL
    assert "operator_id = auth.uid()" in ATTRIBUTION_SQL
    assert "drop policy if exists popper_team_update on public.research_events" in ATTRIBUTION_SQL
    assert "revoke update on table public.research_events from authenticated" in ATTRIBUTION_SQL
    assert "public.popper_access_probe()" in ATTRIBUTION_SQL
    assert "revoke all on function public.popper_access_probe() from public, anon" in ATTRIBUTION_SQL


def test_security_probe_distinguishes_denial_from_missing_schema():
    class DatabaseError(Exception):
        def __init__(self, code):
            self.code = code

    assert _is_permission_denied(DatabaseError("42501"))
    assert not _is_permission_denied(DatabaseError("42P01"))


def test_local_mode_auth_dependency_remains_private_host_compatible(monkeypatch):
    monkeypatch.setattr("popper.security.auth.settings.auth_required", False)
    principal = require_team_user(None)
    assert principal == TeamPrincipal(user_id="local", email="local@popper", role="admin")


def test_viewer_cannot_mutate_api(monkeypatch):
    monkeypatch.setattr("popper.security.auth.settings.auth_required", True)
    monkeypatch.setattr(
        "popper.security.auth.verify_team_token",
        lambda _token: TeamPrincipal(user_id="U-1", email="viewer@example.test", role="viewer"),
    )
    app = FastAPI()
    app.add_middleware(TeamAuthMiddleware)

    @app.post("/api/write")
    def write():
        return {"ok": True}

    response = TestClient(app).post("/api/write", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 403


# --- role resolution: the caller's OWN row, never an arbitrary one ---

def _jwt(subject: str) -> str:
    """A syntactically valid unsigned JWT. Only the `sub` claim matters here."""
    import base64
    import json as _json

    def part(obj: dict) -> str:
        raw = _json.dumps(obj).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'sub': subject})}.sig"


def test_subject_is_read_from_the_token():
    from popper.security.auth import _subject

    assert _subject(_jwt("user-123")) == "user-123"


def test_malformed_tokens_are_rejected_not_guessed():
    import pytest

    from popper.security.auth import AuthFailure, _subject

    for bad in ("", "nodots", "a.!!!notbase64!!!.c", _jwt_no_sub()):
        with pytest.raises(AuthFailure):
            _subject(bad)


def _jwt_no_sub() -> str:
    import base64
    import json as _json
    payload = base64.urlsafe_b64encode(_json.dumps({"aud": "x"}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


def test_role_lookup_is_filtered_to_the_calling_user(monkeypatch):
    """Regression: team members may read the whole roster, so an unfiltered
    `limit=1` returned whichever row sorted first — demoting the admin and
    handing the viewer account write access."""
    import io
    import json as _json

    from popper.config import settings
    from popper.security import auth as auth_module

    roster = {
        "admin-uid": {"user_id": "admin-uid", "email": "boss@x", "role": "admin"},
        "viewer-uid": {"user_id": "viewer-uid", "email": "guest@x", "role": "viewer"},
    }
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        # The real endpoint honours the user_id filter; anything unfiltered would
        # hand back the whole roster, which is what caused the bug.
        match = next(
            (row for uid, row in roster.items() if f"user_id=eq.{uid}" in request.full_url),
            None,
        )
        body = _json.dumps([match] if match else []).encode("utf-8")
        return io.BytesIO(body)

    monkeypatch.setattr(auth_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(settings, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(settings, "supabase_key", "test-key")

    assert auth_module.verify_team_token(_jwt("admin-uid")).role == "admin"
    assert "user_id=eq.admin-uid" in seen["url"], "lookup must be scoped to the caller"
    assert auth_module.verify_team_token(_jwt("viewer-uid")).role == "viewer"

    import pytest
    with pytest.raises(auth_module.AuthFailure):
        auth_module.verify_team_token(_jwt("stranger-uid"))
