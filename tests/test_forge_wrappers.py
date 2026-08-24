"""Forge parity for the trail / break-even wrappers.

Same method as tests/test_forge_features.py, applied to control flow instead of
a series: `replay_emitted_exits` below is the EMITTED stop management written
back into Python, and it is compared against the exits popper/dsl/compiler.py
actually recorded on real US30 M5 bars. A sign error, a missed "only ever
tighten", or a stop that binds one bar too early moves an exit price, and this
fails.

The one timing claim being checked: the compiler tightens the stop AFTER its
own price checks, from that bar's data, so the new stop binds from the NEXT
bar. Pine places its exit order at the close of bar i (live at i+1) and MQL5
calls PositionModify at the open of bar i+1 from bar i's data -- the same
thing, and the same thing this replay does.
"""

import numpy as np
import pytest

from popper.artifacts import StrategySpec
from popper.data.bars import load_bars
from popper.dsl.compiler import compile_to_backtest
from popper.dsl.features import atr_14
from popper.forge.mql5 import Mql5Unsupported, generate_mql5
from popper.forge.pine import PineUnsupported, generate_pine

BARS = load_bars("US30", "M5", "2025-01-01", "2025-03-31")


def spec(**wrapper):
    w = {"stop": {"type": "atr", "mult": 1.5}, "target": {"type": "ratio", "ratio": 2.0}}
    w.update(wrapper)
    return StrategySpec.model_validate({
        "id": "S-1", "hypothesis_id": "H-1", "name": "wrappers",
        "alpha": {"features": ["atr_14", "ema_20"], "signal": "close > ema_20",
                  "signal_short": "close < ema_20"},
        "wrapper": w, "context": {"universe": ["US30"], "timeframe": "5m"},
    })


def replay_emitted_exits(bars, result, stop_mult=1.5, trail_mult=None, be_trigger=None,
                         max_bars=None):
    """Replay the emitted exit management for every trade the backtest opened.

    Line for line, this is:
      Pine  `if strategy.position_size > 0 ... strategy.exit(stop=cur_stop, ...)`
            preceded by the `barstate.isconfirmed` tighten block
      MQL5  the broker stop/target, plus `TightenStop()` at each new bar

    Entries come from the backtest's own trades, so only the exit is under test.
    """
    h = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    o = bars["open"].to_numpy()
    c = bars["close"].to_numpy()
    atr = atr_14(bars).to_numpy()
    out = []
    for trade in result["trades"]:
        direction = 1 if trade["dir"] == "long" else -1
        entry_i = trade["entry_i"]
        # the recorded entry/risk are rounded for reporting; rebuild them exactly.
        # A market order fills at the bar's open and 1R is the SIGNAL bar's ATR.
        entry = o[entry_i]
        risk = stop_mult * atr[entry_i - 1]
        stop = entry - direction * risk
        target = entry + direction * risk * 2.0
        exit_i = exit_price = reason = None
        for i in range(entry_i, len(bars)):
            # the broker's stop is checked before the target (conservative), and a
            # gap fills it worse -- min(open, stop) for a long
            if direction == 1:
                if low[i] <= stop:
                    exit_i, exit_price, reason = i, min(o[i], stop), "stop"
                elif h[i] >= target:
                    exit_i, exit_price, reason = i, max(o[i], target), "target"
            else:
                if h[i] >= stop:
                    exit_i, exit_price, reason = i, max(o[i], stop), "stop"
                elif low[i] <= target:
                    exit_i, exit_price, reason = i, min(o[i], target), "target"
            if reason is None and max_bars is not None and i > entry_i \
                    and (i - entry_i) >= max_bars:
                exit_i, exit_price, reason = i, c[i], "time"
            if reason is not None:
                break
            # --- TightenStop() / the barstate.isconfirmed block ------------------
            # runs after the exits above, from THIS bar, so it binds from the next
            if be_trigger is not None:
                favourable = (h[i] - entry) if direction == 1 else (entry - low[i])
                if favourable >= be_trigger * risk:
                    stop = max(stop, entry) if direction == 1 else min(stop, entry)
            if trail_mult is not None and not np.isnan(atr[i]):
                trailed = c[i] - direction * trail_mult * atr[i]
                stop = max(stop, trailed) if direction == 1 else min(stop, trailed)
        if reason is None:
            exit_i, exit_price, reason = len(bars) - 1, c[-1], "eod"
        out.append((exit_i, round(exit_price, 2), reason))
    return out


def assert_exit_parity(sp, **replay):
    result = compile_to_backtest(sp)(BARS)
    assert result["n_trades"] > 40, "too few trades to prove anything"
    got = replay_emitted_exits(BARS, result, **replay)
    want = [(t["exit_i"], t["exit"], t["reason"]) for t in result["trades"]]
    for n, (a, b) in enumerate(zip(got, want)):
        assert a == b, (
            f"trade {n} ({result['trades'][n]['dir']} opened at bar "
            f"{result['trades'][n]['entry_i']}): emitted exit {a} vs backtest {b}"
        )
    return result


# --- the parity evidence -----------------------------------------------------

def test_trail_atr_exits_match_the_backtest():
    result = assert_exit_parity(spec(trail={"type": "atr", "mult": 2.0}), trail_mult=2.0)
    assert sum(1 for t in result["trades"] if t["reason"] == "stop") > 20


def test_break_even_after_r_exits_match_the_backtest():
    assert_exit_parity(spec(break_even={"type": "after_r", "trigger_r": 1.0}),
                       be_trigger=1.0)


def test_trail_and_break_even_together_match_the_backtest():
    assert_exit_parity(
        spec(trail={"type": "atr", "mult": 2.0},
             break_even={"type": "after_r", "trigger_r": 1.0}),
        trail_mult=2.0, be_trigger=1.0)


def test_a_trail_with_a_time_stop_matches_the_backtest():
    assert_exit_parity(
        spec(trail={"type": "atr", "mult": 2.5},
             time_stop={"type": "bars", "max_bars": 20}),
        trail_mult=2.5, max_bars=20)


def test_the_wrappers_actually_change_the_outcome():
    """A replay that agreed with the backtest because neither wrapper ever fired
    would prove nothing."""
    plain = compile_to_backtest(spec())(BARS)
    trailed = compile_to_backtest(spec(trail={"type": "atr", "mult": 2.0}))(BARS)
    breakeven = compile_to_backtest(
        spec(break_even={"type": "after_r", "trigger_r": 1.0}))(BARS)
    plain_exits = [(t["exit_i"], t["exit"]) for t in plain["trades"]]
    for other in (trailed, breakeven):
        moved = sum(1 for a, t in zip(plain_exits, other["trades"])
                    if a != (t["exit_i"], t["exit"]))
        assert moved > 20, "the wrapper never moved a stop on this fixture"


# --- the timing and the direction, asserted on the emitted source ------------

def test_the_tightened_stop_only_ever_tightens():
    """`math.max` for a long and `math.min` for a short. Reversing either would
    let the stop back away from price -- a loss the backtest never took."""
    sp = spec(trail={"type": "atr", "mult": 2.0},
              break_even={"type": "after_r", "trigger_r": 1.0})
    pine = generate_pine(sp)
    assert "cur_stop := in_long ? math.max(cur_stop, entry_price)" \
           " : math.min(cur_stop, entry_price)" in pine
    assert "cur_stop := in_long ? math.max(cur_stop, trailed)" \
           " : math.min(cur_stop, trailed)" in pine
    mql = generate_mql5(sp)
    assert "next_stop = is_long ? MathMax(next_stop, entry) : MathMin(next_stop, entry);" in mql
    assert "next_stop = is_long ? MathMax(next_stop, trailed) : MathMin(next_stop, trailed);" in mql


def test_break_even_measures_against_the_original_risk():
    """`risk` in compiler.py is set once at try_open and never updated, so the
    trigger must not be measured against the already-trailed distance."""
    sp = spec(trail={"type": "atr", "mult": 2.0},
              break_even={"type": "after_r", "trigger_r": 1.5})
    pine = generate_pine(sp)
    assert "risk_distance := nz(signal_atr, atr_14) * 1.5" in pine
    assert "if favourable >= 1.5 * risk_distance" in pine
    # risk_distance is written once per position, on the fill bar only
    assert pine.count("risk_distance :=") == 2   # the reset to na, and the fill
    mql = generate_mql5(sp)
    assert "if(favourable >= 1.5 * managed_risk)" in mql
    assert mql.count("managed_risk = MathAbs(") == 1
    # ...and captured INSIDE the first-sight guard. Recapturing it every bar would
    # re-measure 1R from an already-trailed stop and re-arm the break-even early.
    guard = mql.index("if(opened != managed_entry)")
    assert guard < mql.index("managed_risk = MathAbs(") < mql.index("      }", guard)


def test_the_trail_reads_the_last_completed_bar_in_both_targets():
    """Pine tightens at the close of bar i and its exit order is live at i+1;
    MQL5 tightens at the OPEN of bar i+1 from shift 1. Same bar, same timing --
    reading shift 0 in MQL5 would price the trail off an unfinished bar."""
    sp = spec(trail={"type": "atr", "mult": 2.0})
    mql = generate_mql5(sp)
    tighten = mql[mql.index("void TightenStop"):]
    assert "iClose(_Symbol, _Period, 1)" in tighten
    assert "iClose(_Symbol, _Period, 0)" not in tighten
    assert "BufferValue(hATR, 1)" in tighten
    # Pine must not trail off a partial close during the on-fill recalculation
    assert "if barstate.isconfirmed and strategy.position_size != 0" in generate_pine(sp)


def test_a_time_stop_closes_instead_of_tightening():
    """compiler.py tightens only when the trade is still open after its time
    stop, so the EA must not modify a position it just closed."""
    mql = generate_mql5(spec(trail={"type": "atr", "mult": 2.0},
                             time_stop={"type": "bars", "max_bars": 20}))
    assert "         trade.PositionClose(_Symbol);\n      else\n         TightenStop();" in mql


def test_a_level_stop_can_also_be_trailed():
    sp = spec(stop={"type": "level", "anchor": "prior_swing_wick_lo"},
              trail={"type": "atr", "mult": 2.0})
    pine = generate_pine(sp)
    assert "risk_distance := math.abs(entry_price - new_stop)" in pine
    assert "lvl_stop := in_long ? math.max(lvl_stop, trailed)" in pine
    assert "TightenStop();" in generate_mql5(sp)


def test_a_spec_with_neither_wrapper_keeps_the_stateless_exit():
    """No trail and no break-even means the stop never moves, so the emitters
    keep recomputing it -- no state, and byte-identical to what they always
    emitted for the promoted strategies."""
    pine = generate_pine(spec())
    assert "cur_stop" not in pine and "risk_distance" not in pine
    assert "stop_distance = nz(signal_atr, atr_14) * 1.5" in pine
    mql = generate_mql5(spec())
    assert "TightenStop" not in mql and "managed_risk" not in mql


# --- what stays refused, and why ---------------------------------------------

def test_no_trade_still_fails_closed_in_both_targets():
    """Not a Forge gap. popper/dsl/compiler.py never reads constraints.no_trade,
    so the backtest traded straight through those windows; an EA that honoured
    the tag would be a different strategy from the one that was falsified."""
    sp = spec(constraints={"no_trade": ["news_high_impact"]})
    with pytest.raises(PineUnsupported, match="does not enforce it"):
        generate_pine(sp)
    with pytest.raises(Mql5Unsupported, match="does not enforce it"):
        generate_mql5(sp)


def test_the_compiler_really_does_ignore_no_trade():
    """The premise of the refusal above, checked rather than assumed."""
    tagged = compile_to_backtest(spec(constraints={"no_trade": ["news_high_impact"]}))(BARS)
    plain = compile_to_backtest(spec())(BARS)
    assert tagged["trades"] == plain["trades"]
