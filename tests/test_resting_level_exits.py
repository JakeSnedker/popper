"""A resting order's risk is fixed when the order is placed.

An ATR stop already used `signal_atr` from the bar the signal fired, so a
resting order's 1R was settled at placement. A LEVEL stop read the anchor at the
FILL bar instead -- the same trade measured two different ways, and undeployable
besides, because a broker fixes order volume at placement and 1R sets it.
"""

import numpy as np
import pandas as pd
import pytest

from popper.artifacts.strategy_spec import StrategySpec
from popper.dsl.compiler import compile_to_backtest
from popper.dsl.features.levels import compute_level


def make_bars(rows):
    t = pd.date_range("2025-04-07 08:00", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame({
        "time": t,
        "open": [r[0] for r in rows], "high": [r[1] for r in rows],
        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
        "volume": [100.0] * len(rows),
    })


def spec(entry, stop, target=None):
    return StrategySpec.model_validate({
        "hypothesis_id": "H-RL", "name": "rl",
        "alpha": {"features": [], "signal": "close > 0", "direction": "long_only",
                  "direction_rationale": "fixture"},
        "wrapper": {"entry": entry, "stop": stop,
                    "target": target or {"type": "ratio", "ratio": 2.0}},
        "context": {"universe": ["US30"], "timeframe": "5m"},
    })


# a drifting series so a swing level keeps moving between placement and fill
DRIFT = make_bars([(100 + i * 0.4, 101 + i * 0.4, 99 + i * 0.4, 100.3 + i * 0.4)
                   for i in range(120)])


def _trades(sp, bars):
    return compile_to_backtest(sp)(bars)["trades"]


def test_a_market_level_stop_is_unchanged():
    """Market entries fill on the signal's next bar and keep their old timing."""
    sp = spec({"type": "market"}, {"type": "level", "anchor": "prior_swing_wick_lo"})
    assert _trades(sp, DRIFT), "fixture produced no trades"


def test_resting_entry_risk_is_settled_at_placement_not_fill():
    """The stop must equal the anchor level from a bar BEFORE the fill.

    Asserted directly rather than by perturbation: nudging the data changes ATR,
    which moves the resting price, which moves the fill bar -- so a perturbation
    test here measures the cascade instead of the invariant.
    """
    sp = spec({"type": "stop", "offset": 0.5, "expiry_bars": 8},
              {"type": "level", "anchor": "prior_swing_wick_lo"})
    trades = _trades(sp, DRIFT)
    assert trades, "fixture produced no resting-entry trades"

    level = compute_level("prior_swing_wick_lo", DRIFT).to_numpy()
    checked = 0
    for t in trades:
        j = t["entry_i"]
        stop_price = t["entry"] - t["risk"]          # long side
        if np.isnan(level[j]):
            continue
        # the level rises monotonically on DRIFT, so placement and fill differ
        if np.isclose(level[j], stop_price):
            raise AssertionError(
                f"trade at bar {j} priced its stop from the FILL bar's level; "
                f"order volume could not have been set at placement"
            )
        earlier = [k for k in range(max(0, j - 9), j) if np.isclose(level[k], stop_price)]
        assert earlier, (
            f"stop {stop_price:.4f} at bar {j} matches no level in the placement window"
        )
        checked += 1
    assert checked, "no trade exercised the invariant"


def test_an_unformed_anchor_still_declines_the_trade():
    """Warm-up must not silently produce a trade with a NaN stop."""
    short = make_bars([(100, 101, 99, 100.5)] * 8)     # < the 20-bar swing window
    sp = spec({"type": "limit", "offset": 0.5, "expiry_bars": 5},
              {"type": "level", "anchor": "prior_swing_wick_lo"})
    out = compile_to_backtest(sp)(short)
    assert out["n_trades"] == 0


def test_level_target_with_a_resting_entry_runs():
    """The combination the emitters used to refuse outright."""
    sp = spec({"type": "limit", "offset": 0.3, "expiry_bars": 6},
              {"type": "level", "anchor": "prior_swing_wick_lo"},
              {"type": "level", "anchor": "prior_swing_wick_hi"})
    out = compile_to_backtest(sp)(DRIFT)
    assert out["n_trades"] >= 0          # may be zero, must not raise
    for t in out["trades"]:
        assert t["risk"] > 0
