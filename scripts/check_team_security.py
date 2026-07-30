"""Verify live Supabase anon denial, team membership and role capabilities."""

import sys
from uuid import uuid4

from popper.config import settings


def main() -> int:
    if not settings.supabase_url or not settings.supabase_key:
        print("FAIL: POPPER_SUPABASE_URL / POPPER_SUPABASE_KEY are not configured")
        return 1
    try:
        from supabase import create_client
    except ImportError:
        print("FAIL: install Popper with the supabase extra")
        return 1

    anon = create_client(settings.supabase_url, settings.supabase_key)
    try:
        rows = anon.table("team_members").select("user_id").limit(1).execute().data
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: team_members/RLS migration is absent: {type(exc).__name__}")
        return 1
    if rows:
        print("FAIL: anonymous client can read team_members")
        return 1

    probe_id = f"RE-security-probe-{uuid4().hex[:8]}"
    probe = {
        "id": probe_id,
        "run_id": "SECURITY-PROBE",
        "iteration": 0,
        "engine": "probe",
        "role": "probe",
        "stage": "anonymous-insert",
        "status": "probe",
        "message": "temporary anonymous denial probe",
        "machine_id": "security-probe",
        "release_id": "security-probe",
        "dataset_id": "security-probe",
    }
    try:
        anon.table("research_events").insert(probe).execute()
    except Exception:  # Expected: privilege or RLS denial.
        pass
    else:
        try:
            anon.table("research_events").delete().eq("id", probe_id).execute()
        finally:
            print("FAIL: anonymous client can write research_events")
        return 1

    if not settings.supabase_email or not settings.supabase_password:
        print("BLOCKED: anon access is denied, but operator email/password are not configured")
        return 1

    from popper.memory.supabase_client import create_configured_client

    client = create_configured_client()
    rows = client.rpc("popper_access_probe").execute().data
    if not rows:
        print("FAIL: authenticated user is not an active Popper team member")
        return 1
    principal = rows[0]
    print(
        "OK: anon read/write denied; authenticated member "
        f"role={principal['role']} can_write={principal['can_write']} "
        f"is_admin={principal['is_admin']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
