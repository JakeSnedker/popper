"""Phase 1 acceptance: the 3 example specs validate AND compile to runnable
objects; the validator rejects bad specs; features are point-in-time correct;
the backtest loop's fill semantics are right on a hand-made scenario."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from popper.dsl import DSLValidationError, compile_to_backtest, load_spec, validate_spec
from popper.dsl.features import _EXACT, compute_feature, resolve_feature

EXAMPLES = sorted((Path(__file__).parent.parent / "popper" / "dsl" / "examples").glob("*.yaml"))
US30 = Path(__file__).parent.parent / "data" / "us30" / "US30_M5.parquet"


def synth_bars(n: int = 3000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 40000 + np.linspace(0, 80, n) + rng.normal(0, 6, n).cumsum()
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    wick = np.abs(rng.normal(0, 4, n)) + 1
    return pd.DataFrame({
        "time": pd.date_range("2025-01-06", periods=n, freq="5min", tz="UTC"),
        "open": open_,
        "high": np.maximum(open_, close) + wick,
        "low": np.minimum(open_, close) - wick,
        "close": close,
        "tick_volume": 100,
    })


# --- DONE-WHEN: the 3 hand-authored examples validate + compile + run ---

def test_three_examples_exist():
    assert len(EXAMPLES) == 3


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_validates_compiles_runs(path):
    spec = load_spec(path)
    validate_spec(spec)  # raises on any problem
    bt = compile_to_backtest(spec)
    result = bt(synth_bars())
    assert set(result) >= {"trades", "n_trades", "equity_curve", "total_return", "win_rate", "params"}
    assert result["n_trades"] == len(result["trades"])


@pytest.mark.skipif(not US30.exists(), reason="US30 parquet not present")
def test_examples_trade_on_real_us30():
    bars = pd.read_parquet(US30).head(5000).reset_index(drop=True)
    for path in EXAMPLES:
        result = compile_to_backtest(load_spec(path))(bars)
        assert result["n_trades"] > 0, f"{path.name} produced no trades on real bars"


# --- validator rejections ---

def _spec(**overrides) -> dict:
    base = {
        "hypothesis_id": "H-TEST", "name": "t",
        "alpha": {"features": ["ema_50", "atr_14"], "signal": "close > ema_50 AND atr_14 > p[atr_min]"},
        "param_space": {"atr_min": [0.5, 1.0]},
    }
    base.update(overrides)
    return base


def _errors(**overrides) -> str:
    with pytest.raises(DSLValidationError) as e:
        validate_spec(_spec(**overrides))
    return str(e.value)


def test_valid_spec_passes():
    validate_spec(_spec())


def test_rejects_unknown_feature():
    assert "unknown feature" in _errors(
        alpha={"features": ["magic_indicator"], "signal": "magic_indicator > 0"},
        param_space={},
    )


def test_rejects_undeclared_name_in_signal():
    assert "unknown name 'rsi_fast'" in _errors(
        alpha={"features": ["ema_50"], "signal": "close > ema_50 AND rsi_fast < 30"},
        param_space={},
    )


def test_rejects_lookahead_call():
    assert "look-ahead" in _errors(
        alpha={"features": ["ema_50"], "signal": "shift(close, -1) > ema_50"},
        param_space={},
    )


def test_rejects_malformed_signal():
    assert "malformed" in _errors(alpha={"features": ["ema_50"], "signal": "close > > ema_50"}, param_space={})


def test_rejects_missing_alpha_block():
    with pytest.raises(DSLValidationError):
        validate_spec({"hypothesis_id": "H-TEST", "name": "t"})


def test_rejects_param_not_in_space():
    assert "p[atr_min] referenced but not in param_space" in _errors(param_space={})


def test_rejects_unused_param_space_key():
    assert "not referenced" in _errors(param_space={"atr_min": [0.5], "ghost": [1, 2]})


# --- feature library: point-in-time correctness (no future leak) ---

def test_all_features_point_in_time_correct():
    bars = synth_bars(2000)
    for name in list(_EXACT) + ["ema_50"]:
        full = compute_feature(name, bars)
        prefix = compute_feature(name, bars.iloc[:1200].copy())
        pd.testing.assert_series_equal(prefix, full.iloc[:1200], check_names=False, obj=name)


def test_unknown_feature_unresolvable():
    assert resolve_feature("nope") is None
    assert resolve_feature("ema_x") is None
    assert resolve_feature("ema_50") is not None


# --- compiler fill semantics on a hand-made scenario ---

def test_long_trade_enters_next_bar_and_hits_2r_target():
    n = 40
    bars = pd.DataFrame({
        "time": pd.date_range("2025-01-06", periods=n, freq="5min", tz="UTC"),
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n,
    })
    # signal bar: close breaks 100.5 at bar 20
    bars.loc[20, ["high", "close"]] = [101.2, 101.0]
    # rally intrabar at 25 so the 2R target fills AT the target level (no gap),
    # without touching the stop
    bars.loc[25:, ["open", "high", "low", "close"]] = [100.0, 109.0, 99.5, 100.0]

    spec = _spec(alpha={"features": [], "signal": "close > 100.5"}, param_space={})
    spec["wrapper"] = {"stop": {"type": "atr", "mult": 2.0}, "target": {"type": "rr", "ratio": 2.0}}
    result = compile_to_backtest(spec)(bars)

    t = result["trades"][0]
    assert t["entry_i"] == 21  # signal on close of 20 -> fill at NEXT bar's open
    assert t["entry"] == 100.0
    assert t["reason"] == "target"
    assert t["pnl_r"] == pytest.approx(2.0)
