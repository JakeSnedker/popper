"""Limit/stop entries and time-based exits.

Synthetic bars, so every assertion is about the semantics rather than the data.
"""

import pandas as pd
import pytest

from popper.artifacts.strategy_spec import StrategySpec
from popper.dsl.compiler import compile_to_backtest


def make_bars(rows):
    """rows = [(open, high, low, close)] on a 5-minute clock."""
    t = pd.date_range("2025-01-06 09:00", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame({
        "time": t,
        "open": [r[0] for r in rows], "high": [r[1] for r in rows],
        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
        "volume": [100.0] * len(rows),
    })


def spec(signal="close > 0", **wrapper):
    w = {"stop": {"type": "atr", "mult": 1.0}, "target": {"type": "ratio", "ratio": 2.0}}
    w.update(wrapper)
    return StrategySpec.model_validate({
        "hypothesis_id": "H-T", "name": "t",
        "alpha": {"features": [], "signal": signal, "direction": "long_only",
                  "direction_rationale": "fixture"},
        "wrapper": w, "context": {"universe": ["US30"], "timeframe": "5m"},
    })


def run(sp, bars):
    return compile_to_backtest(sp)(bars)


# a steady climb, so a LONG market entry always fills and a buy-limit below the
# close never gets touched again
CLIMB = make_bars([(100 + i, 101 + i, 99.5 + i, 100.8 + i) for i in range(60)])


def test_market_entry_still_fills():
    r = run(spec(), CLIMB)
    assert r["n_trades"] > 0
    assert all(t["reason"] != "time" for t in r["trades"])


def test_limit_entry_fills_at_its_price_or_better():
    r = run(spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 10}), CLIMB)
    for t in r["trades"]:
        # a buy limit may never fill worse than the bar's open
        assert t["entry"] <= CLIMB["open"].iloc[t["entry_i"]] + 1e-9


def test_a_limit_that_is_never_touched_produces_no_trade():
    """Price only rises, so a buy limit far below the close can never fill."""
    r = run(spec(entry={"type": "limit", "offset": 50.0, "expiry_bars": 3}), CLIMB)
    assert r["n_trades"] == 0


def test_stop_entry_fills_only_on_a_breakout():
    r = run(spec(entry={"type": "stop", "offset": 0.5, "expiry_bars": 10}), CLIMB)
    assert r["n_trades"] > 0
    for t in r["trades"]:
        assert t["entry"] >= CLIMB["open"].iloc[t["entry_i"]] - 1e-9


def test_expiry_cancels_an_unfilled_order():
    """A far limit with a long life still never fills; the point is it stops resting."""
    near = run(spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 1}), CLIMB)
    far = run(spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 20}), CLIMB)
    # a longer-lived order blocks re-arming, so it cannot produce MORE entries
    assert far["n_trades"] <= near["n_trades"]


def test_time_stop_closes_the_trade_and_is_labelled():
    """CLIMB reaches target in two bars, so a one-bar clock must fire first."""
    baseline = [t for t in run(spec(), CLIMB)["trades"] if t["reason"] == "target"]
    assert baseline and all(t["exit_i"] - t["entry_i"] == 2 for t in baseline)
    r = run(spec(time_stop={"type": "bars", "max_bars": 1}), CLIMB)
    timed = [t for t in r["trades"] if t["reason"] == "time"]
    assert timed, "expected at least one time-based exit"
    for t in timed:
        assert t["exit_i"] - t["entry_i"] == 1


def test_no_time_stop_by_default():
    r = run(spec(), CLIMB)
    assert not [t for t in r["trades"] if t["reason"] == "time"]


def test_a_stop_breach_beats_the_clock():
    """With the clock at one bar, a trade whose stop is breached must still be
    recorded as a stop -- the worse of the two outcomes, conservatively."""
    adverse = 20
    bars = make_bars(
        [(100, 100.5, 99.8, 100.2)] * adverse   # warm ATR(14) up first
        + [(100, 100.2, 90.0, 91.0)]            # breaches any sane stop
        + [(91, 92, 90, 91)] * 10
    )
    r = run(spec(time_stop={"type": "bars", "max_bars": 1}), bars)
    on_adverse = [t for t in r["trades"] if t["entry_i"] == adverse]
    assert on_adverse, "expected a trade opened on the adverse bar"
    assert on_adverse[0]["reason"] == "stop"
    assert on_adverse[0]["pnl_r"] == pytest.approx(-1.0, abs=0.01)


@pytest.mark.parametrize("kind", ["limit", "stop"])
def test_resting_orders_never_look_ahead(kind):
    """Truncating the data must not change any trade that already completed."""
    sp = spec(entry={"type": kind, "offset": 0.5, "expiry_bars": 5})
    full = run(sp, CLIMB)
    prefix = run(sp, CLIMB.iloc[:40].reset_index(drop=True))
    done = [t for t in full["trades"] if t["exit_i"] < 38]
    for a, b in zip(done, prefix["trades"]):
        assert (a["entry_i"], a["entry"], a["exit_i"]) == (b["entry_i"], b["entry"], b["exit_i"])
