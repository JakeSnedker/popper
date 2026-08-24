"""Forge parity for a level exit combined with a RESTING entry.

Both targets used to refuse the pairing: the compiler measured 1R from the fill
bar's level, and a broker fixes order volume when the order is sent. Commit
d4d5f97 moved the resolution to the placement bar (`try_open`'s `anchor_i`), so
the pairing is deployable -- provided the emitted code really does capture the
level when the order is PLACED and hold it until the fill.

Getting that wrong reintroduces the exact mismatch d4d5f97 removed, and it is
invisible in a string check, because "read the level" looks the same whichever
bar you read it on. So the number that matters -- the bar offset -- is PARSED
OUT OF THE EMITTED SOURCE and fed to a Python mirror of the order lifecycle,
which is then compared trade for trade against popper/dsl/compiler.py on real
US30 M5 bars. Break the emitter's timing and the parsed offset changes, the
mirror diverges, and these tests fail; tests/test_forge_resting_level_exits.py
was mutation-checked against exactly that (see the module docstring of
tests/test_forge_levels.py for the same method applied to the levels themselves).
"""

import re

import numpy as np
import pytest

from popper.artifacts import StrategySpec
from popper.data.bars import load_bars
from popper.dsl.compiler import compile_to_backtest
from popper.dsl.features import atr_14
from popper.dsl.features.levels import INTRA_BAR_LEVELS, LEVEL_FEATURES, compute_level
from popper.forge import forge_spec
from popper.forge.mql5 import generate_mql5
from popper.forge.pine import generate_pine

BARS = load_bars("US30", "M5", "2025-01-01", "2025-03-31")


def spec(**wrapper):
    w = {"stop": {"type": "atr", "mult": 1.5}, "target": {"type": "ratio", "ratio": 2.0}}
    w.update(wrapper)
    return StrategySpec.model_validate({
        "id": "S-1", "hypothesis_id": "H-1", "name": "resting level",
        # a signal computable straight off the bars, so the mirror needs no
        # feature machinery of its own
        "alpha": {"features": [], "signal": "close > open", "signal_short": "close < open"},
        "wrapper": w, "context": {"universe": ["US30"], "timeframe": "5m"},
    })


# --- what the emitted source says, read back out of it ------------------------

def pine_latch(source: str, var: str) -> tuple[str, int, float]:
    """`signal_stop_lvl := prior_fractal_lo[1] + (-3)` -> (name, 1, -3.0).

    Pine offset k at bar p is index p - k, so the second element IS the number of
    bars back from the placement bar that the emitted code prices the exit from.

    Reading the latch alone would be vacuous -- an emitter can latch a value and
    then ignore it -- so this also insists the exit block CONSUMES the var rather
    than re-reading the level on the fill bar.
    """
    line = next(l for l in source.splitlines() if l.strip().startswith(f"{var} :="))
    m = re.search(r":=\s*(\w+)(?:\[(\d+)\])?\s*\+\s*\((-?[\d.]+)\)", line)
    assert m, f"cannot read the latch out of {line!r}"
    field = "new_stop" if var == "signal_stop_lvl" else "new_target"
    assert f"float {field} = {var}" in source, (
        f"the exit block does not use {var}: it re-reads the level at the fill, "
        "which is the mismatch d4d5f97 removed"
    )
    return m.group(1), int(m.group(2) or 0), float(m.group(3))


def pine_latch_is_at_placement(source: str, var: str) -> None:
    """The latch must sit in the ENTRY block, indented under the signal branch --
    not in the exit block, which runs at the fill."""
    lines = source.splitlines()
    i = next(n for n, l in enumerate(lines) if l.strip().startswith(f"{var} :="))
    assert lines[i].startswith("    "), "the latch is not inside the entry branch"
    assert "strategy.entry(" in lines[i + 3], "the latch is not on the order's own bar"
    assert f"var float {var} = na" in source, f"{var} does not persist while the order rests"


def _level_shift(block: str, field: str) -> tuple[int, float]:
    m = re.search(rf"double {field} = Level\w+\((\d+)[^)]*\) \+ \((-?[\d.]+)\)", block)
    assert m, f"no level-priced {field} in this block"
    return int(m.group(1)), float(m.group(2))


def mql5_placement_shift(source: str, field: str) -> tuple[int, float]:
    """`double stop = LevelFractal(2, false) + (-3);` inside PlacePending -> (2, -3).

    MQL5 shift s is s bars back from "now". PlacePending runs on the tick that
    opens the bar AFTER the signal, so shift 1 is the placement bar itself and
    shift s is s - 1 bars before it.

    As with the Pine latch, the value has to be USED: the fill handler must
    replay what the order was sent with, and the take-it-at-market branch must
    price from the same bar, or the parsed number proves nothing.
    """
    shift, offset = _level_shift(source.split("bool PlacePending")[1], field)
    reprice = source.split("void RepriceStops")[1].split("bool PlacePending")[0]
    assert f"double {field} = pending_{field};" in reprice, (
        f"RepriceStops recomputes {field} on the FILL bar instead of replaying the "
        "value the order -- and its volume -- were committed with"
    )
    opener = source.split("bool OpenPosition")[1].split("void RepriceStops")[0]
    assert _level_shift(opener, field) == (shift, offset), (
        f"OpenPosition prices {field} from a different bar than PlacePending does"
    )
    return shift, offset


# --- the mirror ---------------------------------------------------------------

def mirror_trades(bars, sp, stop_back: int, stop_off: float,
                  target_back: int | None = None, target_off: float = 0.0):
    """The emitted algorithm's entry pricing, replayed bar by bar in Python.

    The order lifecycle -- the touch rule, the fill price, the expiry -- is the
    one both targets were already shown to match (tests/test_forge_resting_
    entries.py). What is under test here is which bar the exit level comes from,
    so that is not assumed: `stop_back` / `target_back` are the offsets parsed
    out of the emitted source, counted back from the PLACEMENT bar.
    """
    w = sp.wrapper
    o = bars["open"].to_numpy()
    h = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    c = bars["close"].to_numpy()
    atr = atr_14(bars).to_numpy()
    stop_lvl = (compute_level(w.stop.anchor, bars).to_numpy()
                if w.stop.type == "level" else None)
    tgt_lvl = (compute_level(w.target.anchor, bars).to_numpy()
               if w.target.type == "level" else None)
    kind = w.entry.type
    entry_off = float(w.entry.offset)
    expiry = max(1, int(w.entry.expiry_bars))
    rr = float(w.target.ratio) if w.target.type == "ratio" else None
    sl_mult = float(w.stop.mult) if w.stop.type == "atr" else None

    long_sig, short_sig = c > o, c < o
    trades = []
    pos = 0
    entry = stop = target = risk = np.nan
    rest_dir, rest_px, rest_left, rest_p = 0, np.nan, 0, 0
    signal_atr = np.nan

    def close_trade(i, price):
        nonlocal pos
        trades.append({"entry_i": entry_i, "exit_i": i, "risk": round(risk, 6),
                       "entry": round(entry, 2)})
        pos = 0

    entry_i = 0
    for i in range(len(bars)):
        if pos == 0 and rest_dir != 0:
            touched = fill = None
            if kind == "limit":
                if rest_dir == 1 and low[i] <= rest_px:
                    touched, fill = True, min(o[i], rest_px)
                elif rest_dir == -1 and h[i] >= rest_px:
                    touched, fill = True, max(o[i], rest_px)
            else:
                if rest_dir == 1 and h[i] >= rest_px:
                    touched, fill = True, max(o[i], rest_px)
                elif rest_dir == -1 and low[i] <= rest_px:
                    touched, fill = True, min(o[i], rest_px)
            if touched:
                # THE LINE UNDER TEST: the level is read `stop_back` bars before
                # the bar the order was placed on, never from the fill bar i
                if sl_mult is not None:
                    s = (fill - rest_dir * sl_mult * signal_atr
                         if not np.isnan(signal_atr) and signal_atr > 0 else np.nan)
                else:
                    a = rest_p - stop_back
                    s = stop_lvl[a] + stop_off if a >= 0 else np.nan
                ok = not np.isnan(s) and (s < fill if rest_dir == 1 else s > fill)
                if ok:
                    r_ = abs(fill - s)
                    if rr is not None:
                        tp = fill + rest_dir * rr * r_
                    else:
                        a = rest_p - target_back
                        tp = tgt_lvl[a] + target_off if a >= 0 else np.nan
                        ok = not np.isnan(tp) and (tp > fill if rest_dir == 1 else tp < fill)
                    if ok and r_ > 0:
                        pos, entry, entry_i = rest_dir, fill, i
                        risk, stop, target = r_, s, tp
                rest_dir, rest_px, rest_left = 0, np.nan, 0
            else:
                rest_left -= 1
                if rest_left <= 0:
                    rest_dir, rest_px, rest_left = 0, np.nan, 0

        if pos == 1:
            if low[i] <= stop:
                close_trade(i, min(o[i], stop))
            elif h[i] >= target:
                close_trade(i, max(o[i], target))
        elif pos == -1:
            if h[i] >= stop:
                close_trade(i, max(o[i], stop))
            elif low[i] <= target:
                close_trade(i, min(o[i], target))

        if pos == 0 and rest_dir == 0:
            direction = 1 if long_sig[i] else (-1 if short_sig[i] else 0)
            if direction and not np.isnan(atr[i]):
                signal_atr = atr[i]
                away = entry_off * atr[i]
                sign = -1 if kind == "limit" else 1
                rest_px = c[i] + direction * sign * away
                rest_dir, rest_left, rest_p = direction, expiry, i
    if pos != 0:                       # the backtest's "eod" close of a dangling trade
        close_trade(len(bars) - 1, c[-1])
    return trades


def assert_matches_backtest(sp, mirrored, target: str):
    real = compile_to_backtest(sp)(BARS)["trades"]
    assert len(real) > 20, f"only {len(real)} trades -- the fixture proves nothing"
    assert len(mirrored) == len(real), (
        f"{target}: emitted algorithm takes {len(mirrored)} trades, the backtest "
        f"takes {len(real)}"
    )
    for got, want in zip(mirrored, real):
        for field in ("entry_i", "entry", "risk"):
            assert got[field] == pytest.approx(want[field], abs=1e-6), (
                f"{target}: trade filled at bar {want['entry_i']} disagrees on "
                f"{field}: emitted {got[field]!r} vs backtest {want[field]!r}"
            )


# --- the parity evidence ------------------------------------------------------

STOP_ANCHORS = ["prior_swing_wick_lo", "prior_swing_body_lo", "prior_fractal_lo",
                "session_ny_lo", "bb_line"]


@pytest.mark.parametrize("anchor", STOP_ANCHORS)
@pytest.mark.parametrize("kind", ["limit", "stop"])
def test_pine_prices_a_level_stop_from_the_placement_bar(anchor, kind):
    sp = spec(entry={"type": kind, "offset": 0.5, "expiry_bars": 5},
              stop={"type": "level", "anchor": anchor, "offset": -3.0})
    source = generate_pine(sp)
    pine_latch_is_at_placement(source, "signal_stop_lvl")
    name, back, off = pine_latch(source, "signal_stop_lvl")
    assert name == anchor
    assert_matches_backtest(sp, mirror_trades(BARS, sp, back, off), "pine")


@pytest.mark.parametrize("anchor", STOP_ANCHORS)
@pytest.mark.parametrize("kind", ["limit", "stop"])
def test_mql5_prices_a_level_stop_from_the_placement_bar(anchor, kind):
    sp = spec(entry={"type": kind, "offset": 0.5, "expiry_bars": 5},
              stop={"type": "level", "anchor": anchor, "offset": -3.0})
    shift, off = mql5_placement_shift(generate_mql5(sp), "stop")
    assert_matches_backtest(sp, mirror_trades(BARS, sp, shift - 1, off), "mql5")


@pytest.mark.parametrize("anchor", ["prior_swing_wick_hi", "prior_fractal_hi"])
def test_a_level_target_is_also_pinned_at_placement(anchor):
    sp = spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 5},
              stop={"type": "level", "anchor": "prior_swing_wick_lo", "offset": -3.0},
              target={"type": "level", "anchor": anchor, "offset": 0.0})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    pine_latch_is_at_placement(pine, "signal_target_lvl")
    _, s_back, s_off = pine_latch(pine, "signal_stop_lvl")
    _, t_back, t_off = pine_latch(pine, "signal_target_lvl")
    assert_matches_backtest(sp, mirror_trades(BARS, sp, s_back, s_off, t_back, t_off),
                            "pine")
    s_shift, s_off = mql5_placement_shift(mql, "stop")
    t_shift, t_off = mql5_placement_shift(mql, "target")
    assert_matches_backtest(
        sp, mirror_trades(BARS, sp, s_shift - 1, s_off, t_shift - 1, t_off), "mql5")


def test_an_atr_stop_with_a_level_target_still_measures_r_from_the_fill():
    """1R is the ATR distance off the SIGNAL bar, but the target is a placement-bar
    level -- the two timings coexist in one trade, which is what d4d5f97 fixed."""
    sp = spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 5},
              stop={"type": "atr", "mult": 1.5},
              target={"type": "level", "anchor": "prior_swing_wick_hi", "offset": 0.0})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert "signal_stop_lvl" not in pine
    _, t_back, t_off = pine_latch(pine, "signal_target_lvl")
    assert_matches_backtest(sp, mirror_trades(BARS, sp, 0, 0.0, t_back, t_off), "pine")
    t_shift, t_off = mql5_placement_shift(mql, "target")
    assert_matches_backtest(
        sp, mirror_trades(BARS, sp, 0, 0.0, t_shift - 1, t_off), "mql5")


# --- the interaction with INTRA_BAR_LEVELS -----------------------------------

@pytest.mark.parametrize("anchor", sorted(INTRA_BAR_LEVELS))
def test_an_intra_bar_level_is_shifted_as_an_exit_and_not_as_an_entry(anchor):
    """The same level, anchoring both the resting order and the stop, must be read
    on TWO different bars: the entry price is the signal bar's value (it existed
    then), the exit is the bar before (compute_level reads bar i's own OHLC).
    Collapsing them to one bar is a silent mispricing of every trade.
    """
    sp = spec(entry={"type": "limit", "anchor": anchor, "expiry_bars": 5},
              stop={"type": "level", "anchor": anchor, "offset": -3.0})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert f'strategy.entry("Long", strategy.long, limit={anchor})' in pine
    assert pine_latch(pine, "signal_stop_lvl")[1] == 1
    assert f"double anchor = {_mql5_level(anchor, 1)}" in mql
    assert mql5_placement_shift(mql, "stop")[0] == 2


@pytest.mark.parametrize("anchor", sorted(LEVEL_FEATURES - INTRA_BAR_LEVELS))
def test_a_settled_level_is_read_on_the_placement_bar_itself(anchor):
    """The mirror image: over-shifting a settled level would price every exit from
    a bar older than the one the backtest used."""
    sp = spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 5},
              stop={"type": "level", "anchor": anchor, "offset": -3.0})
    assert pine_latch(generate_pine(sp), "signal_stop_lvl")[1] == 0
    assert mql5_placement_shift(generate_mql5(sp), "stop")[0] == 1


def _mql5_level(name, shift):
    from popper.forge.mql5 import _level_call
    return _level_call(name, str(shift))


# --- the combination is a deployable bundle now ------------------------------

def test_the_forge_bundle_carries_a_resting_level_strategy():
    sp = spec(entry={"type": "stop", "offset": 0.4, "expiry_bars": 6},
              stop={"type": "level", "anchor": "prior_swing_wick_lo"},
              target={"type": "level", "anchor": "prior_swing_wick_hi"},
              trail={"type": "atr", "mult": 2.0},
              break_even={"type": "after_r", "trigger_r": 1.0},
              time_stop={"type": "bars", "max_bars": 24})
    bundle = forge_spec(sp)
    assert any(name.endswith(".pine") for name in bundle.files)
    assert any(name.endswith(".mq5") for name in bundle.files)
    pine = next(v for k, v in bundle.files.items() if k.endswith(".pine"))
    # the trail must tighten the LATCHED stop, not re-latch it
    assert "float new_stop = signal_stop_lvl" in pine
    assert "lvl_stop := in_long ? math.max(lvl_stop, trailed)" in pine


def test_the_placement_level_is_held_across_the_bars_the_order_rests():
    """Nothing may overwrite the latch between placement and fill: a new order is
    only armed while `rest_dir == 0`, which a live order rules out."""
    sp = spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 5},
              stop={"type": "level", "anchor": "prior_swing_wick_lo"})
    pine = generate_pine(sp)
    assert "var float signal_stop_lvl = na" in pine
    assert "can_enter = strategy.position_size == 0 and rest_dir == 0" in pine
    mql = generate_mql5(sp)
    assert "double pending_stop = 0.0;" in mql
    assert "double stop = pending_stop;" in mql          # replayed, never re-read
    assert "else return;   // still resting" in mql


def test_a_market_entry_keeps_reading_the_fill_bar():
    """The guard against fixing this in the wrong place. A market entry's anchor
    IS the fill bar (try_open passes no anchor_i), so nothing may latch."""
    sp = spec(stop={"type": "level", "anchor": "prior_swing_wick_lo", "offset": -3.0})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert "signal_stop_lvl" not in pine
    assert "float new_stop = prior_swing_wick_lo + (-3)" in pine
    assert "pending_stop" not in mql
    assert f"double stop = {_mql5_level('prior_swing_wick_lo', 0)} + (-3)" in mql
