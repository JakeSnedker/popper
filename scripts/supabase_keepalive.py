"""Daily cheap read against the shared Supabase project — defeats the free-tier
7-day-inactivity auto-pause. Registered as a Windows Scheduled Task
(scripts/register_supabase_keepalive_task.ps1).

Reads POPPER_SUPABASE_URL / POPPER_SUPABASE_KEY from .env (via popper.config).
Independent of POPPER_STORE: it pings the cloud even while the app runs on local
sqlite. One `select id limit 1` is enough activity to reset the pause clock.

Prints one timestamped OK/FAIL line. Exits non-zero on failure so a scheduled
task surfaces the error in its Last Run Result.
"""
import sys
from datetime import datetime, timezone

from popper.config import settings

_TABLE = "hypotheses"  # any table works; this one always exists post-migration


def main() -> int:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not settings.supabase_url or not settings.supabase_key:
        print(f"{ts} FAIL keepalive: POPPER_SUPABASE_URL / POPPER_SUPABASE_KEY not set")
        return 1
    try:
        from popper.memory.supabase_client import create_configured_client

        client = create_configured_client()
        client.table(_TABLE).select("id").limit(1).execute()
    except Exception as exc:  # noqa: BLE001 — any error means the ping didn't land
        print(f"{ts} FAIL keepalive: {type(exc).__name__}: {exc}")
        return 1
    print(f"{ts} OK keepalive: read {_TABLE} from {settings.supabase_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
