import os
import tempfile

import pytest

from popper.artifacts import Hypothesis, StrategySpec
from popper.dsl import DSLValidationError
from popper.feature_gaps import feature_gaps_from_error
from popper.memory import LocalEmbedder
from popper.memory.sqlite_repo import SqliteMemory
from popper.research import evaluate


def _mem() -> SqliteMemory:
    return SqliteMemory(os.path.join(tempfile.mkdtemp(), "t.db"), embedder=LocalEmbedder())


def _spec(**overrides) -> StrategySpec:
    base = {
        "hypothesis_id": "",
        "name": "s",
        "alpha": {"features": ["ema_10"], "signal": "close > ema_10"},
        "context": {"universe": ["US30"], "timeframe": "5m"},
        "param_space": {},
    }
    base.update(overrides)
    return StrategySpec(**base)


def test_feature_gap_extraction_from_validation_errors():
    err = DSLValidationError([
        "unknown feature: 'vwap'",
        "alpha.signal: function calls not allowed in signal (look-ahead guard): 'abs(close - equilibrium) > p[x]'",
        "alpha.signal: unknown name 'abs' in signal (not a declared feature or bar column)",
        "context.timeframe '15m' has no local data; use '5m'",
    ])
    gaps = {(g["name"], g["kind"]) for g in feature_gaps_from_error(err)}
    assert ("vwap", "feature") in gaps
    assert ("abs", "dsl_function") in gaps
    assert ("abs", "dsl_name") in gaps
    assert ("timeframe:15m", "data_context") in gaps


def test_sqlite_feature_gap_ledger_aggregates_repeats():
    mem = _mem()
    mem.add_feature_gap("vwap", "feature", "test", "unknown feature: 'vwap'", "claim one")
    mem.add_feature_gap("vwap", "feature", "test", "unknown feature: 'vwap'", "claim two")
    gaps = mem.list_feature_gaps()
    assert len(gaps) == 1
    assert gaps[0]["count"] == 2
    assert gaps[0]["examples"] == ["claim two", "claim one"]


def test_invalid_spec_logs_feature_gap_before_storage():
    mem = _mem()
    h = Hypothesis(claim="US30 VWAP mean reversion", mechanism="institutional benchmark", required_features=["vwap"])
    spec = _spec(alpha={"features": ["vwap"], "signal": "close > vwap"})

    with pytest.raises(DSLValidationError):
        evaluate(h, spec, mem=mem)

    gaps = mem.list_feature_gaps()
    assert any(g["name"] == "vwap" and g["kind"] == "feature" for g in gaps)
    assert mem.list_hypotheses() == []


def test_stale_hypothesis_blocks_before_storage():
    mem = _mem()
    mem.add_hypothesis(Hypothesis(
        claim="US30 momentum persists above the 10 EMA",
        mechanism="trend following flow",
        required_features=["ema_10"],
    ))
    h = Hypothesis(
        claim="US30 momentum persists above the 10 EMA",
        mechanism="trend following flow",
        required_features=["ema_10"],
    )

    with pytest.raises(ValueError, match="stale hypothesis"):
        evaluate(h, _spec(), mem=mem)

    assert len(mem.list_hypotheses()) == 1
