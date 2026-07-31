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
