"""Phase 0 acceptance: schemas import and a full artifact trail round-trips through the store."""

import os
import tempfile

from popper.artifacts import (
    ExperimentResult,
    Hypothesis,
    HypothesisStatus,
    Scorecard,
    StrategySpec,
    Verdict,
    VerdictType,
)
from popper.memory.sqlite_repo import SqliteMemory


def _mem() -> SqliteMemory:
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    return SqliteMemory(path)


def test_hypothesis_roundtrips_and_gets_id():
    mem = _mem()
    h = mem.add_hypothesis(Hypothesis(claim="X reverts to mean", mechanism="liquidity"))
    assert h.id == "H-0001"
    got = mem.get_hypothesis(h.id)
    assert got is not None and got.claim == "X reverts to mean"


def test_full_trail_record():
    mem = _mem()
    h = mem.add_hypothesis(Hypothesis(claim="momentum persists", mechanism="flow"))
    s = mem.add_spec(StrategySpec(hypothesis_id=h.id, name="m", universe=["US30"], timeframe="5m"))
    e = mem.add_experiment(ExperimentResult(
        spec_id=s.id, dataset="US30", period_start="2024-01-01", period_end="2024-12-31",
        trades_n=10, raw_metrics={"sharpe": 1.2}, equity_curve=[0, 1, 2]))
    mem.add_scorecard(Scorecard(experiment_id=e.id, pbo=0.2, deflated_sharpe=1.1))
    mem.add_verdict(Verdict(hypothesis_id=h.id, experiment_id=e.id, type=VerdictType.SURVIVE))

    rec = mem.record(h.id)
    assert rec["hypothesis"]["claim"] == "momentum persists"
    assert len(rec["specs"]) == 1
    assert len(rec["experiments"]) == 1
    assert rec["experiments"][0]["equity_curve"] == [0, 1, 2]
    assert len(rec["scorecards"]) == 1
    assert rec["verdicts"][0]["type"] == "survive"


def test_stats_and_staleness():
    mem = _mem()
    mem.add_hypothesis(Hypothesis(claim="unique idea", mechanism="m", status=HypothesisStatus.PROMOTED))
    assert mem.stats()["counts"]["hypotheses"] == 1
    assert mem.stats()["hypotheses_by_status"]["promoted"] == 1
    assert mem.is_stale(Hypothesis(claim="UNIQUE IDEA", mechanism="m")) is True
    assert mem.is_stale(Hypothesis(claim="totally different", mechanism="m")) is False
