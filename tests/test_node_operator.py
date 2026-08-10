import json
import os
from types import SimpleNamespace

import pytest

from popper.node_operator import (
    NodeAlreadyRunning,
    NodeLock,
    NodePaths,
    current_release,
    inspect_dataset,
    parse_retry_after,
    read_json,
    status_snapshot,
    write_json_atomic,
    write_switch,
)


def test_node_paths_and_atomic_state_are_local_to_requested_root(tmp_path, monkeypatch):
    monkeypatch.delenv("POPPER_NODE_STATE_DIR", raising=False)
    paths = NodePaths.for_root(tmp_path)

    write_json_atomic(paths.state_file, {"status": "idle", "pid": None})

    assert paths.state_dir == tmp_path / ".popper"
    assert read_json(paths.state_file) == {"pid": None, "status": "idle"}


def test_node_lock_blocks_duplicate_process_and_releases(tmp_path):
    lock_path = tmp_path / "worker.lock"
    first = NodeLock(lock_path, pid=os.getpid())
    first.acquire()
    try:
        with pytest.raises(NodeAlreadyRunning, match="already runs"):
            NodeLock(lock_path, pid=os.getpid()).acquire()
    finally:
        first.release()

    assert not lock_path.exists()


def test_node_lock_recovers_a_stale_pid(tmp_path):
    lock_path = tmp_path / "worker.lock"
    lock_path.write_text("99999999", encoding="ascii")

    with NodeLock(lock_path, pid=os.getpid()):
        assert lock_path.read_text(encoding="ascii") == str(os.getpid())

    assert not lock_path.exists()


def test_release_identity_marks_a_modified_checkout_dirty(monkeypatch, tmp_path):
    responses = iter([
        SimpleNamespace(returncode=0, stdout="v1.2.3\n"),
        SimpleNamespace(returncode=0, stdout=" M popper/agents/llm.py\n"),
    ])
    monkeypatch.setattr("popper.node_operator.subprocess.run", lambda *args, **kwargs: next(responses))

    assert current_release(tmp_path) == "v1.2.3-dirty"


def test_parked_worker_polls_remote_switch_until_resumed(monkeypatch, tmp_path):
    from scripts import popper_node

    paths = NodePaths.for_root(tmp_path)
    paths.ensure()
    write_switch(paths, "off")
    syncs = []

    def sync_remote(requested_paths):
        syncs.append(1)
        write_switch(requested_paths, "on")

    monkeypatch.setattr(popper_node, "_sync_remote_switch", sync_remote)
    monkeypatch.setattr(popper_node, "_update_state", lambda *args, **kwargs: {})
    monkeypatch.setattr(popper_node, "_stop_requested", lambda _paths: False)

    popper_node._wait_for_resume(paths)

    assert syncs == [1]


def test_provider_backoff_is_parsed_and_safely_bounded():
    assert parse_retry_after("PROVIDER_PAUSED retry_after=900 reason=quota") == 900
    assert parse_retry_after("PROVIDER_PAUSED retry_after=2") == 60
    assert parse_retry_after("PROVIDER_PAUSED retry_after=99999") == 7200
    assert parse_retry_after("no provider detail", default=300) == 300


def test_dataset_identity_requires_manifest_and_all_canonical_timeframes(tmp_path):
    for timeframe in ("M1", "M5", "M15"):
        (tmp_path / "bars" / timeframe).mkdir(parents=True)
    manifest = {
        "dataset_version": "dataset-v1",
        "dataset_sha256": "abc123",
        "file_count": 3,
        "total_size_bytes": 42,
    }
    (tmp_path / "dataset-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = inspect_dataset(tmp_path, "dataset-v1@abc123")

    assert result["dataset_id"] == "dataset-v1@abc123"
    with pytest.raises(ValueError, match="does not match"):
        inspect_dataset(tmp_path, "dataset-v2@wrong")


def test_status_marks_a_dead_recorded_process_as_stale(tmp_path, monkeypatch):
    monkeypatch.delenv("POPPER_NODE_STATE_DIR", raising=False)
    paths = NodePaths.for_root(tmp_path)
    write_json_atomic(paths.state_file, {"status": "researching", "pid": 99999999})

    status = status_snapshot(paths)

    assert status["status"] == "stale"
    assert status["alive"] is False


# --- shared fleet presence ---

def test_heartbeat_payload_matches_node_status_migration(monkeypatch):
    """Offline drift guard: every key publish() sends must exist as a column."""
    import re
    from pathlib import Path

    from popper import node_presence

    migrations = Path(__file__).parent.parent / "supabase" / "migrations"
    columns = set()
    for sql_path in sorted(migrations.glob("*.sql")):
        sql = sql_path.read_text(encoding="utf-8")
        if "create table if not exists public.node_status (" in sql:
            block = sql.split("create table if not exists public.node_status (")[1].split(");")[0]
            columns.update(re.findall(r"^\s*([a-z_]+)\s", block, re.M))
        # Later migrations may add columns to node_status via alter table.
        columns.update(re.findall(r"add column if not exists (\w+)", sql))

    sent: list[dict] = []

    class _Table:
        def upsert(self, row, on_conflict=None):
            sent.append(row)
            return self

        def execute(self):
            return self

    class _Client:
        def table(self, _name):
            return _Table()

    monkeypatch.setattr(node_presence, "_client", _Client())
    monkeypatch.setattr(node_presence, "_last_push", 0.0)
    monkeypatch.setattr(node_presence.settings, "store", "supabase")
    monkeypatch.setattr(node_presence.settings, "machine_id", "drift-guard")
    assert node_presence.publish({"status": "researching"}, force=True) is None
    assert sent, "heartbeat was not sent"
    missing = set(sent[0]) - columns
    assert not missing, f"node_status migration is missing columns: {sorted(missing)}"


def test_presence_failure_never_stops_research(monkeypatch):
    from popper import node_presence

    class _Broken:
        def table(self, _name):
            raise RuntimeError("supabase unreachable")

    monkeypatch.setattr(node_presence, "_client", _Broken())
    monkeypatch.setattr(node_presence, "_last_push", 0.0)
    monkeypatch.setattr(node_presence.settings, "store", "supabase")
    monkeypatch.setattr(node_presence.settings, "machine_id", "offline-node")
    assert "RuntimeError" in (node_presence.publish({"status": "idle"}, force=True) or "")


def test_presence_is_off_for_local_sqlite_nodes(monkeypatch):
    from popper import node_presence

    monkeypatch.setattr(node_presence.settings, "store", "sqlite")
    assert node_presence.enabled() is False
    assert node_presence.publish({"status": "researching"}, force=True) is None


def test_failed_heartbeat_still_throttles(monkeypatch):
    """A broken or unmigrated Supabase must not become a 5-second retry loop."""
    from popper import node_presence

    attempts = []

    class _Broken:
        def table(self, _name):
            attempts.append(1)
            raise RuntimeError("relation node_status does not exist")

    monkeypatch.setattr(node_presence, "_client", _Broken())
    monkeypatch.setattr(node_presence, "_last_push", 0.0)
    monkeypatch.setattr(node_presence.settings, "store", "supabase")
    monkeypatch.setattr(node_presence.settings, "machine_id", "unmigrated")

    assert node_presence.publish({"status": "researching"}, force=True) is not None
    # Routine heartbeats immediately after a failure are throttled, not retried.
    for _ in range(20):
        assert node_presence.publish({"status": "researching"}) is None
    assert len(attempts) == 1, f"failed heartbeat retried {len(attempts)} times"


def test_presence_error_detail_is_truncated(monkeypatch):
    """A provider outage returns an HTML page; it must not land whole in the log."""
    from popper import node_presence

    class _Outage:
        def table(self, _name):
            raise RuntimeError("<!DOCTYPE html>\n" + "<div>521 web server is down</div>\n" * 400)

    monkeypatch.setattr(node_presence, "_client", _Outage())
    monkeypatch.setattr(node_presence, "_last_push", 0.0)
    monkeypatch.setattr(node_presence.settings, "store", "supabase")
    monkeypatch.setattr(node_presence.settings, "machine_id", "outage")
    error = node_presence.publish({"status": "researching"}, force=True)
    assert error is not None
    assert len(error) < 400, f"error detail was {len(error)} chars"
    assert "\n" not in error
    assert error.endswith("...[truncated]")


# --- exploit toggle and fleet online/requeue derivation ---

def test_exploit_file_defaults_on_and_round_trips(tmp_path):
    from popper.node_operator import NodePaths, read_exploit, write_exploit

    paths = NodePaths.for_root(tmp_path)
    paths.ensure()
    assert read_exploit(paths) == "on", "a node with no exploit file must still refine"
    write_exploit(paths, "off")
    assert read_exploit(paths) == "off"
    write_exploit(paths, "on")
    assert read_exploit(paths) == "on"
    # Garbage must not silently disable the engine.
    paths.exploit_file.write_text("banana", encoding="ascii")
    assert read_exploit(paths) == "on"
    import pytest
    with pytest.raises(ValueError):
        write_exploit(paths, "maybe")


def test_fleet_marks_online_separately_from_healthy(monkeypatch):
    """A parked node is ONLINE (its machine is on and reporting) but not HEALTHY.
    Collapsing the two hid whether an operator's computer was even switched on."""
    from datetime import datetime, timedelta, timezone

    from popper import node_presence

    now = datetime.now(timezone.utc)
    rows = [
        {"machine_id": "fresh-parked", "status": "parked",
         "heartbeat_at": now.isoformat()},
        {"machine_id": "fresh-running", "status": "running",
         "heartbeat_at": now.isoformat()},
        {"machine_id": "stale", "status": "running",
         "heartbeat_at": (now - timedelta(hours=3)).isoformat()},
        {"machine_id": "requeueing", "status": "running",
         "heartbeat_at": now.isoformat(),
         "requeue_requested_at": now.isoformat()},
    ]

    class _Query:
        def select(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def execute(self):
            return type("R", (), {"data": [dict(r) for r in rows]})()

    monkeypatch.setattr(node_presence, "enabled", lambda: True)
    monkeypatch.setattr(node_presence, "_get_client",
                        lambda: type("C", (), {"table": lambda self, _n: _Query()})())

    fleet = {row["machine_id"]: row for row in node_presence.fleet()}
    assert fleet["fresh-parked"]["online"] is True
    assert fleet["fresh-parked"]["healthy"] is True   # parked is an OK state
    assert fleet["stale"]["online"] is False
    assert fleet["stale"]["healthy"] is False
    # Defaults must be present even when the columns are absent on older rows.
    assert fleet["fresh-running"]["exploit"] == "on"
    assert fleet["fresh-running"]["requeue_pending"] is False
    assert fleet["requeueing"]["requeue_pending"] is True
