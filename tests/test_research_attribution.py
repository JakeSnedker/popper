import sqlite3

import pytest

from popper.api.main import _proposer_attribution
from popper.memory.sqlite_repo import SqliteMemory
from popper.memory.supabase_repo import SupabaseMemory
from popper.runtime import attributed_event, runtime_attribution


@pytest.fixture(autouse=True)
def _clear_runtime_cache():
    runtime_attribution.cache_clear()
    yield
    runtime_attribution.cache_clear()


def _configure(monkeypatch):
    monkeypatch.setattr("popper.runtime.settings.machine_id", "node-jake")
    monkeypatch.setattr("popper.runtime.settings.release_id", "v0.1.0-test")
    monkeypatch.setattr("popper.runtime.settings.dataset_id", "dataset@test-hash")
    monkeypatch.setattr("popper.memory.sqlite_repo.settings.operator_id", "operator-jake")
    runtime_attribution.cache_clear()


def test_event_attribution_extracts_actual_model_routes(monkeypatch):
    _configure(monkeypatch)
    event = attributed_event({
        "run_id": "R-1",
        "payload": {"llm_calls": [
            {"provider": "claude_cli", "model": "sonnet", "status": "ok"},
            {"provider": "codex_cli", "model": "default", "status": "error"},
            {"provider": "claude_cli", "model": "sonnet", "status": "ok"},
        ]},
    }, operator_id="operator-jake")

    assert event["operator_id"] == "operator-jake"
    assert event["machine_id"] == "node-jake"
    assert event["release_id"] == "v0.1.0-test"
    assert event["dataset_id"] == "dataset@test-hash"
    assert event["model_ids"] == ["claude_cli:sonnet", "codex_cli:default"]


def test_evaluated_hypothesis_is_reportable_by_model_operator_node_release_and_dataset():
    key = {"run_id": "R-3", "iteration": 2, "engine": "explore"}
    events = [
        {
            **key, "role": "hypothesis", "stage": "propose", "status": "ok",
            "operator_id": "auth-user", "machine_id": "node-1",
            "release_id": "v1", "dataset_id": "data@hash",
            "payload": {"llm_calls": [{
                "provider": "claude_cli", "model": "sonnet", "role": "hypothesis",
                "status": "ok",
            }]},
        },
        {
            **key, "role": "gauntlet", "stage": "evaluate", "status": "survive",
            "artifact_ids": {"hypothesis_id": "H-3"},
        },
    ]

    attribution, _ = _proposer_attribution(None, events=events)

    assert attribution["H-3"] == {
        "provider": "claude_cli", "model": "sonnet", "role": "hypothesis",
        "operator_id": "auth-user", "machine_id": "node-1",
        "release_id": "v1", "dataset_id": "data@hash",
    }


def test_sqlite_research_events_roundtrip_all_attribution(monkeypatch, tmp_path):
    _configure(monkeypatch)
    memory = SqliteMemory(str(tmp_path / "memory.db"))

    stored = memory.add_research_event({
        "run_id": "R-2", "iteration": 1, "engine": "explore",
        "role": "hypothesis", "stage": "propose", "status": "ok", "message": "test",
        "artifact_ids": {"hypothesis_id": "H-1"},
        "payload": {"llm_calls": [
            {"provider": "claude_cli", "model": "sonnet", "status": "ok"},
        ]},
    })
    (loaded,) = memory.list_research_events(run_id="R-2")

    assert loaded == stored
    assert loaded["operator_id"] == "operator-jake"
    assert loaded["model_ids"] == ["claude_cli:sonnet"]


def test_authenticated_supabase_event_uses_database_owned_operator(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr("popper.memory.supabase_repo.settings.auth_required", True)

    class Query:
        def __init__(self):
            self.row = None

        def insert(self, row):
            self.row = row
            return self

        def execute(self):
            return self

    class Client:
        def __init__(self):
            self.query = Query()

        def table(self, _name):
            return self.query

    memory = object.__new__(SupabaseMemory)
    memory.client = Client()
    stored = memory.add_research_event({
        "run_id": "R-cloud", "payload": {"llm_calls": [{
            "provider": "codex_cli", "model": "default",
        }]},
    })

    assert stored["machine_id"] == "node-jake"
    assert stored["model_ids"] == ["codex_cli:default"]
    assert "operator_id" not in stored  # PostgreSQL defaults/enforces auth.uid().


def test_existing_sqlite_event_table_migrates_without_data_loss(monkeypatch, tmp_path):
    _configure(monkeypatch)
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE research_events ("
            "id TEXT PRIMARY KEY, run_id TEXT, iteration INTEGER, engine TEXT, role TEXT, "
            "stage TEXT, status TEXT, message TEXT, artifact_ids TEXT, payload TEXT, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO research_events VALUES "
            "('RE-old', 'R-old', 0, 'legacy', 'legacy', 'legacy', 'ok', 'old', '{}', '{}', '2025-01-01')"
        )

    memory = SqliteMemory(str(path))
    memory.add_research_event({
        "run_id": "R-new", "iteration": 1, "engine": "new", "role": "new",
        "stage": "new", "status": "ok", "message": "new",
    })

    assert memory.list_research_events(run_id="R-old")[0]["operator_id"] is None
    assert memory.list_research_events(run_id="R-new")[0]["machine_id"] == "node-jake"
