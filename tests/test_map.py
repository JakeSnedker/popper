"""Phase 4 acceptance (spec/06 row 4): re-submitting a known idea is flagged
STALE (novelty gate fires), and each experiment yields a scorecard row with
novelty populated. Plus faceting / behaviour / correlation units."""

import os
import tempfile

import pytest

from popper.artifacts import ExperimentResult, Hypothesis, Scorecard, StrategySpec
from popper.memory import LocalEmbedder, complete_scorecard
from popper.memory.map import (
    behaviour_signature,
    categorize,
    correlation_to_pool,
    edge_type,
    regime_stability,
)
from popper.memory.sqlite_repo import SqliteMemory


def _mem() -> SqliteMemory:
    # LocalEmbedder: offline + deterministic (no OpenAI calls in tests)
    return SqliteMemory(os.path.join(tempfile.mkdtemp(), "t.db"), embedder=LocalEmbedder())


# --- DONE-WHEN 1: re-submitting a known idea is flagged stale ---

def test_resubmitting_known_idea_is_stale():
    mem = _mem()
    claim = "NY-open momentum on US30 persists 20 minutes after a strong opening drive"
    mem.add_hypothesis(Hypothesis(claim=claim, mechanism="liquidity vacuum",
                                  required_features=["atr_14", "session_ny", "opening_range"]))

    # same idea, re-worded trivially (same features) -> near-duplicate -> stale
    dup = Hypothesis(claim=claim + ".", mechanism="liquidity vacuum",
                     required_features=["atr_14", "session_ny", "opening_range"])
    assert mem.is_stale(dup) is True
    assert mem.novelty(dup) < 0.1

    # a genuinely different idea -> not stale, high novelty
    fresh = Hypothesis(claim="NAS100 gap-fills before 10:30 when the overnight gap is small",
                       mechanism="dealer hedging", required_features=["overnight_gap", "vwap"])
    assert mem.is_stale(fresh) is False
    assert mem.novelty(fresh) > 0.5


def test_first_hypothesis_is_maximally_novel():
    mem = _mem()
    h = mem.add_hypothesis(Hypothesis(claim="a brand new idea", mechanism="x"))
    assert h.novelty_score == 1.0  # nothing to be near
    assert h.embedding and h.tags.get("edge_type")


# --- DONE-WHEN 2: each experiment yields a scorecard row with novelty populated ---

def test_scorecard_persists_with_novelty():
    mem = _mem()
    h = mem.add_hypothesis(Hypothesis(claim="momentum persists on US30 after a strong drive",
                                      mechanism="flow", required_features=["atr_14", "ema_50"]))
    spec = mem.add_spec(StrategySpec(
        hypothesis_id=h.id, name="m",
        alpha={"features": ["ema_50"], "signal": "close > ema_50"},
        context={"universe": ["US30"], "timeframe": "5m"}))
    exp = mem.add_experiment(ExperimentResult(
        spec_id=spec.id, dataset="US30", period_start="2025-01-01", period_end="2025-06-01",
        trades_n=6, equity_curve=[1.0, 1.02, 1.01, 1.05, 1.04, 1.08, 1.10],
        raw_metrics={"pnl": 0.10, "win_rate": 0.5, "expectancy": 0.2,
                     "trades": [{"pnl_r": 2}, {"pnl_r": -1}, {"pnl_r": 2},
                                {"pnl_r": -1}, {"pnl_r": 2}, {"pnl_r": 1}]}))

    sc = complete_scorecard(Scorecard(experiment_id=exp.id), mem, h, exp)
    assert sc.novelty > 0.0  # populated from the map
    stored = mem.add_scorecard(sc)

    # reload from the store and confirm novelty + behaviour persisted
    trail = mem.record(h.id)
    row = trail["scorecards"][0]
    assert row["id"] == stored.id
    assert row["novelty"] == sc.novelty and row["novelty"] > 0.0
    assert row["behaviour_signature"]["profitable"] is True
    assert "correlation" in row


# --- faceting / behaviour / correlation units ---

def test_edge_type_and_categorize():
    assert edge_type(Hypothesis(claim="momentum continues after the drive", mechanism="m")) == "momentum"
    assert edge_type(Hypothesis(claim="price mean-reverts to VWAP", mechanism="m")) == "mean_reversion"
    spec = StrategySpec(hypothesis_id="H", name="n",
                        alpha={"features": ["ema_50"], "signal": "close > ema_50"},
                        context={"universe": ["US30"], "timeframe": "5m"})
    tags = categorize(Hypothesis(claim="breakout of the opening range", mechanism="m"), spec)
    assert tags == {"edge_type": "breakout", "context": "US30:5m"}


def test_regime_stability_and_behaviour():
    steady = [2, -1, 2, -1, 2, -1, 2, -1]      # profitable in every chunk
    lumpy = [2, 2, 2, 2, -1, -1, -1, -1.5]     # all profit up front, then bleeds
    assert regime_stability(steady, k=4) == 1.0
    assert regime_stability(lumpy, k=4) < 1.0

    exp = ExperimentResult(spec_id="S", dataset="d", period_start="a", period_end="b",
                           raw_metrics={"pnl": -0.05, "win_rate": 0.3, "expectancy": -0.1,
                                        "trades": [{"pnl_r": -1}] * 4})
    sig = behaviour_signature(exp)
    assert sig["profitable"] is False and sig["win_rate_band"] == "low"


def test_correlation_to_pool():
    up = [1.0, 1.1, 1.2, 1.3, 1.4]
    assert correlation_to_pool(up, []) == 0.0            # empty pool -> diversifying
    assert correlation_to_pool(up, [up]) == pytest.approx(1.0, abs=1e-6)  # identical -> 1
    down = [1.4, 1.3, 1.2, 1.1, 1.0]
    assert correlation_to_pool(up, [down]) == pytest.approx(1.0, abs=1e-6)  # |corr| of anti-correlated
