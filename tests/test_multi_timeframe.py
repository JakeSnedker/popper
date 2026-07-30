"""Causal multi-timeframe DSL, alignment, runner and accounting tests."""

import numpy as np
import pandas as pd
import pytest

from popper.agents.fidelity import fidelity_errors
from popper.artifacts import Hypothesis, StrategySpec
from popper.data.bars import CANONICAL_DATASET_DIR
from popper.data.timeframes import higher_feature_alias, normalize_timeframe, timeframe_minutes
from popper.dsl import DSLValidationError, compile_to_backtest, validate_spec
from popper.dsl.compiler import align_completed_feature
from popper.dsl.validator import validate_local_data_context
from popper.experiments.runner import run
from popper.falsify.gauntlet import context_trial_count, multiple_testing_trial_count
from popper.memory.map import context_tag


def _bars(times, close) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame({
        "time": times,
        "open": open_,
        "high": np.maximum(open_, close) + 1.0,
        "low": np.minimum(open_, close) - 1.0,
        "close": close,
        "tick_volume": 100,
    })


def _spec(**alpha_overrides) -> StrategySpec:
    alpha = {
        "features": ["ema_10"],
        "higher_timeframe_features": {"5m": ["candle_size"]},
        "signal": "close > ema_10 AND htf_m5_candle_size > 0",
    }
    alpha.update(alpha_overrides)
    return StrategySpec(
        hypothesis_id="H-MTF", name="mtf",
        alpha=alpha,
        context={"universe": ["US30"], "timeframe": "1m"},
    )


def test_timeframe_normalization_and_aliases():
    assert timeframe_minutes("1h") == 60
    assert normalize_timeframe("15m") == "M15"
    assert higher_feature_alias("15m", "ema_50") == "htf_m15_ema_50"


def test_validator_accepts_declared_higher_alias_and_rejects_non_higher_context():
    validate_spec(_spec())
    validate_local_data_context(_spec())

    with pytest.raises(DSLValidationError, match="unsupported timeframe"):
        validate_spec(_spec(higher_timeframe_features={"daily": ["ema_10"]}))

    with pytest.raises(DSLValidationError, match="strict integer multiple"):
        validate_spec(StrategySpec(
            hypothesis_id="H-BAD", name="bad",
            alpha={
                "features": [],
                "higher_timeframe_features": {"1m": ["ema_10"]},
                "signal": "htf_m1_ema_10 > 0",
            },
            context={"universe": ["US30"], "timeframe": "5m"},
        ))


def test_validator_requires_signal_alias_not_unscoped_higher_feature():
    with pytest.raises(DSLValidationError, match="unknown name 'candle_size'"):
        validate_spec(_spec(signal="close > ema_10 AND candle_size > 0"))


def test_alignment_exposes_higher_bar_only_at_completion_boundary():
    base = pd.DataFrame({
        "time": pd.date_range("2025-01-06 10:00", periods=6, freq="1min", tz="UTC"),
    })
    higher = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-06 09:55Z", "2025-01-06 10:00Z"]),
    })
    values = pd.Series([10.0, 20.0])

    aligned = align_completed_feature(base, higher, values, "M1", "M5")

    assert aligned.tolist() == [10.0, 10.0, 10.0, 10.0, 20.0, 20.0]


def test_in_progress_higher_bar_is_invisible_and_alignment_is_prefix_invariant():
    base_times = pd.date_range("2025-01-06 09:55", periods=180, freq="1min", tz="UTC")
    higher_times = pd.date_range("2025-01-06 09:50", periods=38, freq="5min", tz="UTC")
    base = _bars(base_times, 100 + np.sin(np.arange(len(base_times)) / 9))
    higher = _bars(higher_times, 100 + np.arange(len(higher_times)))
    strategy = compile_to_backtest(_spec())

    full = strategy._env(base, {"M5": higher})["htf_m5_candle_size"]
    prefix_length = 97
    prefix_base = base.iloc[:prefix_length].reset_index(drop=True)
    cutoff = prefix_base.time.iloc[-1] + pd.Timedelta(minutes=1)
    prefix_higher = higher[higher.time + pd.Timedelta(minutes=5) <= cutoff].reset_index(drop=True)
    prefix = strategy._env(prefix_base, {"M5": prefix_higher})["htf_m5_candle_size"]
    pd.testing.assert_series_equal(prefix, full.iloc[:prefix_length], check_names=False)

    boundary_base = base[(base.time >= pd.Timestamp("2025-01-06 10:00Z"))
                         & (base.time <= pd.Timestamp("2025-01-06 10:04Z"))].reset_index(drop=True)
    original = strategy._env(boundary_base, {"M5": higher})["htf_m5_candle_size"]
    changed_higher = higher.copy()
    changed_higher.loc[changed_higher.time == pd.Timestamp("2025-01-06 10:00Z"), "high"] += 1000
    changed = strategy._env(boundary_base, {"M5": changed_higher})["htf_m5_candle_size"]
    assert changed.iloc[:4].equals(original.iloc[:4])
    assert changed.iloc[4] != original.iloc[4]


def test_compiled_multitimeframe_trades_are_prefix_invariant():
    rng = np.random.default_rng(4)
    base_times = pd.date_range("2025-01-06", periods=600, freq="1min", tz="UTC")
    higher_times = pd.date_range("2025-01-05 23:55", periods=122, freq="5min", tz="UTC")
    base = _bars(base_times, 100 + rng.normal(0, 0.4, len(base_times)).cumsum())
    higher = _bars(higher_times, 100 + rng.normal(0, 1.0, len(higher_times)).cumsum())
    strategy = compile_to_backtest(_spec())
    half = len(base) // 2
    cutoff = base.time.iloc[half - 1] + pd.Timedelta(minutes=1)
    truncated_higher = higher[
        higher.time + pd.Timedelta(minutes=5) <= cutoff
    ].reset_index(drop=True)

    full = strategy(base, higher_timeframe_bars={"M5": higher})["trades"]
    truncated = strategy(
        base.iloc[:half].reset_index(drop=True),
        higher_timeframe_bars={"M5": truncated_higher},
    )["trades"]
    # Exclude the prefix's synthetic end-of-data close at half - 1.
    early_full = [
        (t["entry_i"], t["exit_i"], t["pnl_r"]) for t in full if t["exit_i"] < half - 1
    ]
    early_truncated = [
        (t["entry_i"], t["exit_i"], t["pnl_r"])
        for t in truncated if t["exit_i"] < half - 1
    ]
    assert early_full == early_truncated


def test_hypothesis_fidelity_accepts_required_feature_bound_to_higher_timeframe():
    hypothesis = Hypothesis(
        claim="M15 volatility confirms M1 momentum",
        mechanism="persistent cross-scale flow",
        required_features=["candle_size"],
    )
    assert fidelity_errors(hypothesis, _spec(features=[]), mode="strict") == []


def test_context_trial_accounting_counts_bindings_without_cloning_hypothesis():
    spec = _spec(higher_timeframe_features={"5m": ["candle_size", "ema_10"], "15m": ["atr_14"]})
    assert context_trial_count(spec) == 4
    assert multiple_testing_trial_count(spec, parameter_trials=3, exit_trials=27) == 324
    assert spec.hypothesis_id == "H-MTF"
    assert context_tag(spec) == "US30:1m+M5+M15"


def test_higher_context_loader_reads_each_timeframe_once(monkeypatch):
    from popper.data import bars as bars_module

    calls = []

    def fake_load(symbol, timeframe, start=None, end=None):
        calls.append((symbol, timeframe, start, end))
        return pd.DataFrame()

    monkeypatch.setattr(bars_module, "load_bars", fake_load)
    result = bars_module.load_higher_timeframe_bars(
        "US30", {"15m": ["ema_10", "atr_14"]}, end="2025-01-31",
    )
    assert list(result) == ["M15"]
    assert calls == [("US30", "15m", None, "2025-01-31")]


@pytest.mark.skipif(
    not (CANONICAL_DATASET_DIR / "dataset-manifest.json").exists(),
    reason="canonical FTMO dataset not present",
)
def test_real_runner_loads_canonical_m5_with_completed_m15_context():
    spec = StrategySpec(
        hypothesis_id="H-REAL-MTF", name="real_mtf",
        alpha={
            "features": ["ema_10"],
            "higher_timeframe_features": {"15m": ["ema_10"]},
            "signal": "close > ema_10 AND htf_m15_ema_10 > 0",
        },
        context={"universe": ["US30"], "timeframe": "5m"},
    )
    result = run(spec, period=("2024-01-02", "2024-01-05"))
    assert result.dataset.startswith("US30 5m+M15")
    assert result.period_start == "2024-01-02"
