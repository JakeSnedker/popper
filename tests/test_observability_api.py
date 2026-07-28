"""Phase 8 Part B acceptance: the observability endpoints expose the map
(PCA-projected points + whitespace), the recent-verdict stream (newest-first),
and per-hypothesis experiment timelines — all offline over a seeded temp store."""

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import popper.memory
from popper.api.main import app
from popper.artifacts import (
    ExperimentResult,
    Hypothesis,
    HypothesisStatus,
    Scorecard,
    StrategySpec,
    Verdict,
)
from popper.memory import LocalEmbedder
from popper.memory.sqlite_repo import SqliteMemory


def _mem() -> SqliteMemory:
    # LocalEmbedder: offline + deterministic (no OpenAI calls in tests)
    return SqliteMemory(os.path.join(tempfile.mkdtemp(), "t.db"), embedder=LocalEmbedder())


def _ts(minute: int) -> datetime:
    return datetime(2026, 1, 1, 12, minute, tzinfo=timezone.utc)


def _seed(mem: SqliteMemory) -> dict:
    """Two-experiment refinement trail on h1, one kill on h2, untested h3."""
    h1 = mem.add_hypothesis(Hypothesis(
        claim="momentum persists on US30 after a strong drive",
        mechanism="flow", required_features=["atr_14", "ema_50"]))
    s1 = mem.add_spec(StrategySpec(
        hypothesis_id=h1.id, name="m",
        alpha={"features": ["ema_50"], "signal": "close > ema_50"},
        context={"universe": ["US30"], "timeframe": "5m"}))
    e1 = mem.add_experiment(ExperimentResult(
        spec_id=s1.id, dataset="US30", period_start="2025-01-01", period_end="2025-06-01",
        trades_n=10, raw_metrics={"profit_factor": 1.1}, created_at=_ts(1)))
    e2 = mem.add_experiment(ExperimentResult(
        spec_id=s1.id, dataset="US30", period_start="2025-01-01", period_end="2025-06-01",
        trades_n=12, raw_metrics={"profit_factor": 1.4}, created_at=_ts(2)))
    mem.add_scorecard(Scorecard(experiment_id=e1.id, robustness=0.3, deflated_sharpe=0.4,
                                diagnostics={"pbo": {"value": 0.6, "passed": False}}))
    mem.add_scorecard(Scorecard(experiment_id=e2.id, robustness=0.8, deflated_sharpe=0.9,
                                diagnostics={"pbo": {"value": 0.1, "passed": True}}))
    mem.add_verdict(Verdict(hypothesis_id=h1.id, experiment_id=e1.id, type="kill",
                            reasons=["pbo>0.5"], created_at=_ts(3)))
    mem.add_verdict(Verdict(hypothesis_id=h1.id, experiment_id=e2.id, type="survive",
                            created_at=_ts(5)))
    mem.set_status(h1.id, HypothesisStatus.SURVIVED)

    h2 = mem.add_hypothesis(Hypothesis(
        claim="price mean-reverts to VWAP after a stretched deviation",
        mechanism="dealer hedging", required_features=["vwap"]))
    s2 = mem.add_spec(StrategySpec(
        hypothesis_id=h2.id, name="mr",
        alpha={"features": ["vwap"], "signal": "close < vwap"},
        context={"universe": ["US30"], "timeframe": "5m"}))
    e3 = mem.add_experiment(ExperimentResult(
        spec_id=s2.id, dataset="US30", period_start="2025-01-01", period_end="2025-06-01",
        trades_n=5, raw_metrics={"profit_factor": 0.7}, created_at=_ts(1)))
    mem.add_scorecard(Scorecard(experiment_id=e3.id, robustness=0.1, deflated_sharpe=0.05))
    mem.add_verdict(Verdict(hypothesis_id=h2.id, experiment_id=e3.id, type="kill",
                            reasons=["ruin"], created_at=_ts(4)))
    mem.set_status(h2.id, HypothesisStatus.KILLED)

    h3 = mem.add_hypothesis(Hypothesis(
        claim="US30 rises on Mondays", mechanism="calendar flow"))  # no verdict yet
    mem.add_research_event({
        "run_id": "R-test",
        "iteration": 1,
        "engine": "explore",
        "role": "hypothesis",
        "stage": "propose",
        "status": "ok",
        "message": "seeded trace event",
        "artifact_ids": {"hypothesis_id": h1.id},
        "payload": {"novelty": 0.9},
    })
    return {"h1": h1.id, "h2": h2.id, "h3": h3.id}


@pytest.fixture()
def client(monkeypatch):
    mem = _mem()
    ids = _seed(mem)
    monkeypatch.setattr(popper.memory, "_instance", mem)
    return TestClient(app), ids


# --- /api/map ---

def test_map_points_categories_whitespace(client):
    c, ids = client
    body = c.get("/api/map").json()

    assert body["categories"]["momentum"] == 1
    assert body["categories"]["mean_reversion"] == 1
    assert "breakout" in body["whitespace"] and "liquidity" in body["whitespace"]

    points = {p["id"]: p for p in body["points"]}
    assert set(points) == set(ids.values())  # every embedded hypothesis is a point
    for p in points.values():
        assert isinstance(p["x"], float) and isinstance(p["y"], float)
        assert isinstance(p["novelty"], float)
    # PCA spread: distinct embeddings must not collapse onto one coordinate
    coords = {(p["x"], p["y"]) for p in points.values()}
    assert len(coords) > 1

    assert points[ids["h1"]]["verdict"] == "survive"  # latest of kill@t3, survive@t5
    assert points[ids["h2"]]["verdict"] == "kill"
    assert points[ids["h3"]]["verdict"] is None  # untested: must not crash
    assert points[ids["h1"]]["edge_type"] == "momentum"


def test_map_degenerate_cases(monkeypatch):
    empty = _mem()
    monkeypatch.setattr(popper.memory, "_instance", empty)
    assert TestClient(app).get("/api/map").json()["points"] == []

    single = _mem()
    single.add_hypothesis(Hypothesis(claim="one lonely idea", mechanism="x"))
    monkeypatch.setattr(popper.memory, "_instance", single)
    (point,) = TestClient(app).get("/api/map").json()["points"]
    assert point["x"] == 0.0 and point["y"] == 0.0


# --- /api/verdicts ---

def test_verdicts_newest_first_with_stats(client):
    c, ids = client
    rows = c.get("/api/verdicts").json()["verdicts"]

    assert [r["type"] for r in rows] == ["survive", "kill", "kill"]  # t5, t4, t3
    assert [r["created_at"] for r in rows] == sorted((r["created_at"] for r in rows), reverse=True)

    newest = rows[0]
    assert newest["hypothesis_id"] == ids["h1"]
    assert newest["profit_factor"] == 1.4  # from the verdict's experiment
    assert newest["deflated_sharpe"] == 0.9  # from that experiment's scorecard
    assert rows[2]["reasons"] == ["pbo>0.5"]

    assert len(c.get("/api/verdicts", params={"limit": 1}).json()["verdicts"]) == 1


# --- /api/timeline ---

def test_timeline_ordered_and_404(client):
    c, ids = client
    exps = c.get("/api/timeline", params={"hypothesis_id": ids["h1"]}).json()["experiments"]

    assert [e["profit_factor"] for e in exps] == [1.1, 1.4]  # creation order = refinement trend
    assert [e["verdict"] for e in exps] == ["kill", "survive"]
    assert [e["deflated_sharpe"] for e in exps] == [0.4, 0.9]
    assert [e["robustness"] for e in exps] == [0.3, 0.8]

    assert c.get("/api/timeline", params={"hypothesis_id": "H-9999"}).status_code == 404


# --- /api/hypotheses/{id}: scorecards surface diagnostics (Part A glass box) ---

def test_record_scorecards_carry_diagnostics(client):
    c, ids = client
    rec = c.get(f"/api/hypotheses/{ids['h1']}").json()
    diags = [sc["diagnostics"] for sc in rec["scorecards"]]
    assert all(isinstance(d, dict) for d in diags)
    assert diags[0]["pbo"]["passed"] is False and diags[1]["pbo"]["passed"] is True


def test_research_events_endpoint(client):
    c, ids = client
    rows = c.get("/api/research-events", params={"run_id": "R-test"}).json()["events"]
    assert len(rows) == 1
    assert rows[0]["role"] == "hypothesis"
    assert rows[0]["artifact_ids"] == {"hypothesis_id": ids["h1"]}
    assert rows[0]["payload"] == {"novelty": 0.9}


def test_analytics_and_survivors_are_honest_about_coverage(client):
    c, ids = client
    body = c.get("/api/analytics").json()

    assert body["totals"] == {
        "generated": 3,
        "tests": 3,
        "tested_hypotheses": 2,
        "awaiting_verdict": 1,
        "killed": 2,
        "survived": 1,
        "promoted": 0,
        "pass_rate": 33.33,
        "fail_rate": 66.67,
    }
    failures = {row["key"]: row["count"] for row in body["failure_gates"]}
    assert failures == {"gate3_mc_ruin": 1, "gate4_multiple_testing": 1}
    assert body["attribution"]["attributed_tests"] == 0
    assert body["attribution"]["unattributed_tests"] == 3

    survivors = c.get("/api/survivors").json()["survivors"]
    assert [row["hypothesis_id"] for row in survivors] == [ids["h1"]]
    assert survivors[0]["verdict"] == "survive"
    assert survivors[0]["evidence"]["diagnostics_recorded"] is True
    assert survivors[0]["proposer"]["model"] is None


# --- /api/data-sources: only real parquets are offered (no silent path drift) ---

def test_llm_config_exposes_routes_without_credentials():
    body = TestClient(app).get("/api/llm-config").json()
    assert set(body) == {"backend", "policy", "batch_size", "fidelity_mode",
                         "survivor_audit", "routes"}
    assert "key" not in json.dumps(body).lower()


def test_data_sources_lists_only_present_parquets():
    r = TestClient(app).get("/api/data-sources")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["data_dir"], str) and isinstance(body["sources"], list)
    for s in body["sources"]:
        assert set(s) == {"symbol", "timeframe", "rows"}
        assert s["symbol"] and s["timeframe"]  # parsed from <SYM>_<TF>.parquet
    from popper.data.bars import DATA_DIR
    if (DATA_DIR / "us30" / "US30_M5.parquet").exists():  # ships in-repo
        assert any(s["symbol"] == "US30" and s["timeframe"] == "M5" for s in body["sources"])
