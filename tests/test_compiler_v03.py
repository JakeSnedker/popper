"""DSL v0.3 compiler (spec §4): level-anchored stops/targets resolve in the
trade loop, incoherent entries (NaN anchor / wrong-side level) are SKIPPED,
p[...] anchors resolve from param_space, and — non-negotiable — a level-based
spec is CAUSAL: truncating future bars never changes early trades."""

import numpy as np
import pandas as pd
import pytest

from popper.dsl import compile_to_backtest


def _flat_bars(n=60) -> pd.DataFrame:
    return pd.DataFrame({
        "time": pd.date_range("2025-01-06", periods=n, freq="5min", tz="UTC"),
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n,
    })


def _rand_bars(n=900, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    times = pd.date_range("2025-01-06 00:00", periods=n, freq="5min", tz="UTC")
    price = 40000 + np.cumsum(rng.normal(0, 10, n))
    open_ = price + rng.uniform(-3, 3, n)
    close = price + rng.uniform(-3, 3, n)
    high = np.maximum.reduce([open_, close, price]) + rng.uniform(1, 6, n)
    low = np.minimum.reduce([open_, close, price]) - rng.uniform(1, 6, n)
    return pd.DataFrame({"time": times, "open": open_, "high": high, "low": low, "close": close})


def _spec(wrapper, signal="close > 100.5", features=(), param_space=None) -> dict:
    return {
        "hypothesis_id": "H-C3", "name": "t",
        "alpha": {"features": list(features), "signal": signal},
        "wrapper": wrapper,
        "param_space": param_space or {},
    }


# --- deterministic fill semantics with structural exits ---

def _one_signal_bars():
    """Flat 100/101/99 bars; a single long signal on bar 30's close."""
    bars = _flat_bars()
    bars.loc[30, ["high", "close"]] = [101.2, 101.0]
    return bars


def test_level_stop_fills_at_prior_swing_low():
    bars = _one_signal_bars()
    bars.loc[35, "low"] = 98.0  # dive through the structural stop
    spec = _spec({"stop": {"type": "level", "anchor": "prior_swing_wick_lo"},
                  "target": {"type": "ratio", "ratio": 2.0}})
    t = compile_to_backtest(spec)(bars)["trades"][0]
    assert t["entry_i"] == 31 and t["entry"] == 100.0
    # stop = prior 20-bar wick low = 99.0 -> risk 1.0 -> stopped at exactly -1R
    assert t["reason"] == "stop" and t["exit"] == 99.0
    assert t["pnl_r"] == pytest.approx(-1.0)


def test_level_target_fills_at_prior_swing_high_emergent_rr():
    bars = _one_signal_bars()
    bars.loc[31:, "low"] = 99.5   # hold above the 99 structural stop after entry
    bars.loc[35, "high"] = 105.0  # rally through the structural target
    spec = _spec({"stop": {"type": "level", "anchor": "prior_swing_wick_lo"},
                  "target": {"type": "level", "anchor": "prior_swing_wick_hi"}})
    t = compile_to_backtest(spec)(bars)["trades"][0]
    # SL = 99 (swing lo), TP = 101.2 (swing hi incl. the signal bar's spike):
    # emergent RR = 1.2 — set by structure, not swept
    assert t["reason"] == "target" and t["exit"] == pytest.approx(101.2)
    assert t["pnl_r"] == pytest.approx(1.2)


def test_offset_moves_the_level():
    bars = _one_signal_bars()
    bars.loc[35, "low"] = 98.0
    spec = _spec({"stop": {"type": "level", "anchor": "prior_swing_wick_lo", "offset": -0.5},
                  "target": {"type": "ratio", "ratio": 10.0}})
    t = compile_to_backtest(spec)(bars)["trades"][0]
    assert t["exit"] == pytest.approx(98.5)  # 99 - 0.5
    assert t["pnl_r"] == pytest.approx(-1.0)  # risk widened with the stop


# --- guards: never open an incoherent trade ---

def test_nan_anchor_skips_entry():
    # signal fires from bar 0, but the swing level needs 20 prior bars: every
    # entry before the level forms must be skipped, not opened with a NaN stop
    bars = _rand_bars()
    spec = _spec({"stop": {"type": "level", "anchor": "prior_swing_wick_lo"},
                  "target": {"type": "ratio", "ratio": 2.0}},
                 signal="close > 0")
    res = compile_to_backtest(spec)(bars)
    assert res["n_trades"] > 0
    assert min(t["entry_i"] for t in res["trades"]) >= 20


def test_wrong_side_stop_skips_entry():
    # bb_line + huge positive offset puts the "stop" above every long entry
    bars = _rand_bars()
    spec = _spec({"stop": {"type": "level", "anchor": "bb_line", "offset": 1e6},
                  "target": {"type": "ratio", "ratio": 2.0}},
                 signal="close > 0")
    assert compile_to_backtest(spec)(bars)["n_trades"] == 0


def test_wrong_side_target_skips_entry():
    bars = _rand_bars()
    spec = _spec({"stop": {"type": "atr", "mult": 2.0},
                  "target": {"type": "level", "anchor": "bb_line", "offset": -1e6}},
                 signal="close > 0")
    assert compile_to_backtest(spec)(bars)["n_trades"] == 0


# --- p[...] anchor / offset resolution ---

def test_param_ref_anchor_matches_literal():
    bars = _rand_bars()
    lit = _spec({"stop": {"type": "level", "anchor": "prior_swing_wick_lo"},
                 "target": {"type": "level", "anchor": "prior_swing_wick_hi"}},
                signal="close > 0")
    ref = _spec({"stop": {"type": "level", "anchor": "p[anc]", "offset": "p[off]"},
                 "target": {"type": "level", "anchor": "prior_swing_wick_hi"}},
                signal="close > 0",
                param_space={"anc": ["prior_swing_wick_lo"], "off": [0.0]})
    assert compile_to_backtest(lit)(bars)["trades"] == compile_to_backtest(ref)(bars)["trades"]


# --- causality (the non-negotiable): future bars never change early trades ---

@pytest.mark.parametrize("wrapper", [
    {"stop": {"type": "level", "anchor": "prior_swing_wick_lo"},
     "target": {"type": "level", "anchor": "prior_swing_wick_hi"}},
    {"stop": {"type": "level", "anchor": "prior_fractal_lo", "offset": -2.0},
     "target": {"type": "level", "anchor": "session_ny_hi"}},
    {"stop": {"type": "atr", "mult": 1.5},
     "target": {"type": "level", "anchor": "prior_swing_body_hi"}},
], ids=["swing/swing", "fractal/session", "atr/body"])
def test_level_exits_no_lookahead(wrapper):
    """Mirror of gauntlet._detect_lookahead: trades that opened AND closed in
    the first half must be identical when the second half is truncated away."""
    bars = _rand_bars(n=900, seed=3)
    spec = _spec(wrapper, signal="close > ema_10", features=["ema_10"])
    bt = compile_to_backtest(spec)
    half = len(bars) // 2
    full = bt(bars)["trades"]
    trunc = bt(bars.iloc[:half].reset_index(drop=True))["trades"]
    early = [(t["entry_i"], t["exit_i"], t["pnl_r"]) for t in full if t["exit_i"] < half]
    early_t = [(t["entry_i"], t["exit_i"], t["pnl_r"]) for t in trunc if t["exit_i"] < half]
    assert early == early_t, f"level exit {wrapper} leaked the future into early trades"
