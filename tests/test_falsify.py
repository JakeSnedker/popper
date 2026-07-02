"""Phase 3 acceptance: the gauntlet KILLs a deliberately-overfit spec and lets a
robust one SURVIVE. Runs the REAL backtest engine over synthetic bars via an
injected run_fn (deterministic + fast); the LdP stats are unit-checked too."""

import numpy as np
import pandas as pd
import pytest

from popper.artifacts import ExperimentResult, StrategySpec, VerdictType
from popper.dsl import compile_to_backtest
from popper.experiments.runner import compute_metrics
from popper.falsify import GauntletConfig, calculate_windows, judge

START, END = "2025-01-01", "2026-08-01"


def _run_fn_over(bars: pd.DataFrame):
    """A run_fn (drop-in for experiments.run) that backtests over an in-memory
    synthetic frame sliced by period — exercises the real compiler + metrics."""
    def run_fn(spec, period=None, params=None):
        df = bars
        if period:
            s, e = period
            df = bars[(bars["time"] >= pd.Timestamp(s, tz="UTC"))
                      & (bars["time"] <= pd.Timestamp(e, tz="UTC"))].reset_index(drop=True)
        if len(df) < 5:
            res = {"trades": [], "equity_curve": [1.0], "total_return": 0.0, "n_trades": 0}
        else:
            res = compile_to_backtest(spec)(df, params)
        m = compute_metrics(res)
        m["trades"] = res["trades"]
        return ExperimentResult(
            spec_id=spec.id or "", dataset="synth",
            period_start=str(period[0]) if period else "", period_end=str(period[1]) if period else "",
            trades_n=res["n_trades"], raw_metrics=m, equity_curve=res["equity_curve"],
        )
    return run_fn


def _bars(close: np.ndarray, start=START) -> pd.DataFrame:
    n = len(close)
    time = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    wick = np.abs(np.diff(close, prepend=close[0])) + 1.0
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame({
        "time": time, "open": open_,
        "high": np.maximum(open_, close) + wick,
        "low": np.minimum(open_, close) - wick,
        "close": close, "tick_volume": 100,
    })


def _cfg() -> GauntletConfig:
    # smaller MC + slices keep the test fast; window schedule still yields >=4
    return GauntletConfig(mc_sims=500, n_slices=8)


# --- DONE-WHEN 1: robust spec SURVIVES ---

def test_robust_spec_survives():
    n = 13000
    rng = np.random.default_rng(3)
    # a real (not perfect) edge: gentle persistent uptrend + genuine noise, so a
    # momentum long wins ~40% at 2:1 across the whole series and its rr neighbours.
    close = 100 + 0.04 * np.arange(n) + rng.normal(0, 1.5, n).cumsum()
    bars = _bars(close)

    spec = StrategySpec(
        hypothesis_id="H-ROBUST", name="trend_follow",
        alpha={"features": ["ema_10"], "signal": "close > ema_10"},
        wrapper={"stop": {"type": "atr", "mult": 1.5}, "target": {"type": "rr", "ratio": "p[rr]"}},
        context={"universe": ["US30"], "timeframe": "1h"},
        param_space={"rr": [1.5, 2.0, 2.5]},
    )
    sc, v = judge(spec, run_fn=_run_fn_over(bars), period=(START, END), cfg=_cfg())
    print("ROBUST:", v.type, "reasons=", v.reasons, "pbo=", sc.pbo, "dsr=", sc.deflated_sharpe,
          "stab=", sc.parameter_stability, "robust=", sc.robustness)
    assert v.type != VerdictType.KILL, f"robust spec was killed: {v.reasons}"


# --- DONE-WHEN 2: overfit spec is KILLED ---

def test_overfit_spec_is_killed():
    n = 13000
    rng = np.random.default_rng(11)
    close = 100 + rng.normal(0, 1.0, n).cumsum()  # pure random walk: no real edge
    bars = _bars(close)

    # a fine knife-edge grid over a no-edge signal -> in-sample winners are noise
    # that flips out-of-sample: high PF dispersion + high PBO -> KILL.
    spec = StrategySpec(
        hypothesis_id="H-OVERFIT", name="rsi_knife_edge",
        alpha={"features": ["ema_20", "rsi_fast"],
               "signal": "close > ema_20 AND rsi_fast > p[rsi_lo] AND rsi_fast < p[rsi_hi]"},
        wrapper={"stop": {"type": "atr", "mult": 2.0}, "target": {"type": "rr", "ratio": 2.0}},
        context={"universe": ["US30"], "timeframe": "1h"},
        param_space={"rsi_lo": [30, 40, 45, 50], "rsi_hi": [55, 60, 70, 80]},
    )
    sc, v = judge(spec, run_fn=_run_fn_over(bars), period=(START, END), cfg=_cfg())
    print("OVERFIT:", v.type, "reasons=", v.reasons, "pbo=", sc.pbo, "dsr=", sc.deflated_sharpe,
          "stab=", sc.parameter_stability)
    assert v.type == VerdictType.KILL
    assert v.reasons  # carries falsification reasons for the hypothesis engine


# --- window schedule adapts to our data range (>=4 windows) ---

def test_walkforward_windows_meet_minimum():
    w = calculate_windows(START, END, _cfg())
    assert len(w) >= 4
    assert w[0]["opt"][0] == START
    for win in w:  # forward starts where optimisation ends (anchored, contiguous)
        assert win["fwd"][0] == win["opt"][1]
