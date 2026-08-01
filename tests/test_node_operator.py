import json
import os

import pytest

from popper.node_operator import (
    NodeAlreadyRunning,
    NodeLock,
    NodePaths,
    inspect_dataset,
    parse_retry_after,
    read_json,
    status_snapshot,
    write_json_atomic,
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

    sql = (Path(__file__).parent.parent / "supabase" / "migrations"
           / "0004_node_status.sql").read_text(encoding="utf-8")
    block = sql.split("create table if not exists public.node_status (")[1].split(");")[0]
    columns = set(re.findall(r"^\s*([a-z_]+)\s", block, re.M))

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
