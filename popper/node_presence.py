"""Publish this machine's worker state to the shared Supabase node_status table.

Local `.popper\\state.json` answers "is my node alive". This answers "is the
team's fleet alive" — the only way an operator learns that someone else's node
died overnight without opening their computer.

Every call is best-effort: research must never stop because presence reporting
failed. Failures are returned to the caller for local logging, not raised.
"""

from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Any

from popper.config import settings

# ponytail: one client reused per process, guarded by a lock. Worker heartbeats
# are ~1/min from a single thread; a pool would be ceremony.
_client: Any = None
_lock = threading.Lock()
_last_push = 0.0

PUSH_INTERVAL_SECONDS = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_client():
    global _client
    with _lock:
        if _client is None:
            from popper.memory.supabase_client import create_configured_client

            _client = create_configured_client()
        return _client


def enabled() -> bool:
    return settings.store == "supabase" and bool(settings.machine_id)


def publish(state: dict[str, Any], force: bool = False) -> str | None:
    """Upsert this machine's row. Returns an error string, or None on success.

    Throttled to PUSH_INTERVAL_SECONDS unless `force` is set, so a 5-second
    local heartbeat loop does not become a 5-second network call.
    """
    global _last_push
    if not enabled():
        return None

    import time

    now = time.monotonic()
    if not force and (now - _last_push) < PUSH_INTERVAL_SECONDS:
        return None

    row = {
        "machine_id": settings.machine_id,
        "operator_label": settings.operator_id,
        "status": str(state.get("status") or "unknown"),
        "release_id": settings.release_id,
        "dataset_id": settings.dataset_id,
        "switch": str(state.get("switch") or "on"),
        "switch_requested_at": state.get("switch_requested_at"),
        "last_error": state.get("last_error"),
        "started_at": state.get("started_at"),
        "heartbeat_at": _now(),
        "updated_at": _now(),
    }
    # Throttle on the attempt, not the success: an unreachable or not-yet-migrated
    # Supabase must not turn a 5-second local heartbeat into a 5-second network
    # retry loop for the length of an overnight run.
    _last_push = now
    try:
        _get_client().table("node_status").upsert(row, on_conflict="machine_id").execute()
    except Exception as exc:  # noqa: BLE001 - presence must never stop research.
        # Truncate: a provider outage answers with a full HTML error page, and an
        # unreadable 8 KB blob per failure is what makes an operator stop reading
        # the log at all.
        detail = " ".join(str(exc).split())
        if len(detail) > 300:
            detail = detail[:300] + " ...[truncated]"
        return f"{type(exc).__name__}: {detail}"
    return None


def fleet(stale_after_seconds: int = 300) -> list[dict[str, Any]]:
    """Every node this operator is allowed to see, newest heartbeat first."""
    if not enabled():
        return []
    rows = (
        _get_client().table("node_status").select("*")
        .order("heartbeat_at", desc=True).execute().data
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        try:
            beat = datetime.fromisoformat(str(row.get("heartbeat_at")).replace("Z", "+00:00"))
            age = (now - beat).total_seconds()
        except (TypeError, ValueError):
            age = None
        row["seconds_since_heartbeat"] = None if age is None else int(age)
        row["switch"] = str(row.get("switch") or "on")
        row["healthy"] = (
            age is not None
            and age <= stale_after_seconds
            and str(row.get("status")) not in {"stopped", "crashed", "unknown"}
        )
    return rows


def set_switch(machine_id: str, value: str) -> str | None:
    """Toggle an operator's own (or, for admins, any) node switch via the
    security-definer RPC. Returns an error string, or None on success.
    """
    if not enabled():
        return None
    if value not in {"on", "off"}:
        return f"switch must be on or off, got {value!r}"
    try:
        _get_client().rpc(
            "popper_set_node_switch",
            {"p_machine_id": machine_id, "p_switch": value},
        ).execute()
    except Exception as exc:  # noqa: BLE001 - never let a toggle 500 the dashboard
        return f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"
    return None


def read_node_switch(machine_id: str) -> str | None:
    """The current remote switch for one machine (None if unknown)."""
    if not enabled():
        return None
    try:
        rows = (
            _get_client().table("node_status")
            .select("switch").eq("machine_id", machine_id).execute().data
        )
    except Exception:  # noqa: BLE001 - best-effort mirroring
        return None
    return rows[0]["switch"] if rows else None


def demo() -> None:
    """Self-check without a network: throttling and payload shape."""
    global _last_push, _client

    _last_push = 0.0
    calls: list[dict[str, Any]] = []

    class _Table:
        def upsert(self, row, on_conflict=None):
            calls.append(row)
            return self

        def execute(self):
            return self

    class _Client:
        def table(self, _name):
            return _Table()

    _client = _Client()
    original_store, original_machine = settings.store, settings.machine_id
    settings.store, settings.machine_id = "supabase", "test-machine"
    try:
        assert publish({"status": "researching"}, force=True) is None
        assert len(calls) == 1, calls
        assert calls[0]["machine_id"] == "test-machine"
        assert calls[0]["status"] == "researching"
        # Throttled: a second immediate call must not hit the network.
        assert publish({"status": "researching"}) is None
        assert len(calls) == 1, "throttle failed"
        assert publish({"status": "stopped"}, force=True) is None
        assert len(calls) == 2 and calls[1]["status"] == "stopped"

        class _Broken:
            def table(self, _name):
                raise RuntimeError("network down")

        _client = _Broken()
        assert "RuntimeError" in (publish({"status": "idle"}, force=True) or "")
    finally:
        settings.store, settings.machine_id = original_store, original_machine
        _client = None
        _last_push = 0.0
    print("node_presence demo OK")


if __name__ == "__main__":
    demo()
