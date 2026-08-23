"""An exit anchor must be priced from something knowable at the fill.

The compiler resolves a level stop/target at the FILL bar, and the fill happens
at that bar's OPEN. Three levels read the fill bar's own high/low/close --
bb_line averages in its close, the fractals confirm using its high/low -- so
reading them at bar i sets a stop from a price nobody could know yet.
"""

import numpy as np
import pandas as pd
import pytest

from popper.artifacts.strategy_spec import StrategySpec
from popper.dsl.compiler import compile_to_backtest
from popper.dsl.features.levels import INTRA_BAR_LEVELS, LEVEL_FEATURES, compute_level


def _bars(n=900, seed=5):
    rng = np.random.default_rng(seed)
    close = 40000 + np.cumsum(rng.normal(0, 6, n))
    high = close + np.abs(rng.normal(0, 4, n))
    low = close - np.abs(rng.normal(0, 4, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({
        "time": pd.date_range("2025-03-03 00:00", periods=n, freq="5min", tz="UTC"),
        "open": open_, "high": np.maximum(high, np.maximum(open_, close)),
        "low": np.minimum(low, np.minimum(open_, close)),
        "close": close, "volume": np.full(n, 100.0),
    })


BARS = _bars()


def _reads_current_bar(name, k):
    """Does perturbing ONLY bar k's high/low/close move that same bar's level?"""
    base = compute_level(name, BARS).to_numpy()
    p = BARS.copy()
    p.loc[k, "high"] = p.loc[k, "high"] + 50.0
    p.loc[k, "low"] = p.loc[k, "low"] - 50.0
    p.loc[k, "close"] = p.loc[k, "close"] + 30.0
    moved = compute_level(name, p).to_numpy()
    a, b = base[k], moved[k]
    return not (np.isnan(a) and np.isnan(b)) and not np.isclose(a, b)


@pytest.mark.parametrize("name", sorted(LEVEL_FEATURES))
def test_which_levels_read_the_current_bar(name):
    """Pins the classification the compiler relies on.

    A fractal only reads the current bar on the bars where a confirmation
    actually lands, so the property is 'happens somewhere', not 'happens
    everywhere'. A SAFE level must never read the current bar, anywhere.
    """
    probes = range(300, 800, 7)
    hits = [k for k in probes if _reads_current_bar(name, k)]
    if name in INTRA_BAR_LEVELS:
        assert hits, f"{name} is listed as intra-bar but never read the current bar"
    else:
        assert not hits, f"{name} read the current bar at {hits[:3]} but is treated as safe"


def _run_with_stop_anchor(anchor, bars):
    spec = StrategySpec.model_validate({
        "hypothesis_id": "H-LA", "name": "la",
        "alpha": {"features": ["ema_50"], "signal": "close > ema_50",
                  "direction": "long_only", "direction_rationale": "fixture"},
        "wrapper": {"stop": {"type": "level", "anchor": anchor, "offset": -5.0},
                    "target": {"type": "ratio", "ratio": 2.0}},
        "context": {"universe": ["US30"], "timeframe": "5m"},
    })
    return compile_to_backtest(spec)(bars)


@pytest.mark.parametrize("anchor", ["prior_fractal_lo", "bb_line"])
def test_a_trade_is_unaffected_by_its_own_fill_bar_extremes(anchor):
    """Widening the fill bar AFTER entry must not move the stop that was set at
    that bar's open. Before the shift, it did."""
    base = _run_with_stop_anchor(anchor, BARS)
    assert base["n_trades"] > 0, "fixture produced no trades"
    first = base["trades"][0]

    p = BARS.copy()
    k = first["entry_i"]
    # perturb everything the fill bar prints AFTER its open -- close included,
    # since bb_line averages the close and high/low alone would leave it still.
    # The open is untouched: that is the price the fill actually used.
    p.loc[k, "high"] = p.loc[k, "high"] + 40.0
    p.loc[k, "low"] = p.loc[k, "low"] - 40.0
    p.loc[k, "close"] = p.loc[k, "close"] + 25.0
    moved = _run_with_stop_anchor(anchor, p)

    assert moved["trades"], "perturbation removed every trade"
    assert moved["trades"][0]["risk"] == pytest.approx(first["risk"], rel=1e-9), (
        "the 1R distance moved when the fill bar's own extremes changed, "
        "so the stop was priced from a value unknowable at the open"
    )


def test_safe_anchors_need_no_shift():
    """A swing level is already complete at the fill bar's open, so it is used
    as-is -- the fix must not staleness-shift levels that were already correct."""
    out = _run_with_stop_anchor("prior_swing_wick_lo", BARS)
    assert out["n_trades"] > 0
