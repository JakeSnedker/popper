"""Forge parity for DSL v0.3 level features.

Structural string checks cannot prove an emitter right: Pine and MQL5 do not run
here. So every level the emitters render is transliterated BACK into Python --
line by line, in the same iterative shape the target executes -- and compared
element by element against popper.dsl.features.levels.compute_level on real
US30 M5 bars. A one-bar disagreement is a look-ahead bug, which is the whole
reason these levels lag by construction.

The mirrors below are deliberately dumb loops. They are not a second
implementation of the feature; they are what the emitted source says, rewritten
in Python. If an emitter line changes, its mirror must change with it.
"""

import numpy as np
import pandas as pd
import pytest

from popper.artifacts import StrategySpec
from popper.data.bars import load_bars
from popper.dsl.features import SESSIONS
from popper.dsl.features.levels import INTRA_BAR_LEVELS, LEVEL_FEATURES, compute_level
from popper.forge import forge_spec
from popper.forge.mql5 import _level_call, generate_mql5
from popper.forge.pine import generate_pine

BARS = load_bars("US30", "M5", "2025-01-01", "2025-03-31")
# the MQL5 session mirror rescans the previous day for every bar, so it runs on a
# month rather than a quarter -- still real bars, weekends and all
MONTH = load_bars("US30", "M5", "2025-01-01", "2025-01-31")

NAN = float("nan")


def _utc_day(times: pd.DatetimeIndex):
    """Whole UTC days since the epoch -- what Pine's math.floor(time / 86400000)
    and MQL5's (int)(bar_utc / 86400) both compute."""
    return (times - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1D")


def spec(**wrapper):
    w = {"stop": {"type": "atr", "mult": 1.5}, "target": {"type": "ratio", "ratio": 2.0}}
    w.update(wrapper)
    return StrategySpec.model_validate({
        "id": "S-1", "hypothesis_id": "H-1", "name": "levels",
        "alpha": {"features": ["atr_14", "ema_20"], "signal": "close > ema_20",
                  "signal_short": "close < ema_20"},
        "wrapper": w, "context": {"universe": ["US30"], "timeframe": "5m"},
    })


def assert_parity(name: str, mirror: list, bars: pd.DataFrame, target: str, shift: int = 0):
    """Bar-for-bar equality with compute_level; only NaN-vs-NaN may differ.

    `shift=1` compares against the series popper/dsl/compiler.py's `exit_anchor`
    builds for an INTRA_BAR_LEVEL -- compute_level(...).shift(1).
    """
    expected = compute_level(name, bars).shift(shift).to_numpy(dtype=float)
    got = np.asarray(mirror, dtype=float)
    assert got.shape == expected.shape
    both_nan = np.isnan(expected) & np.isnan(got)
    # NaN-vs-NaN is only allowed during warm-up, so it must not be the whole series
    assert np.isfinite(expected).mean() > 0.5, f"{name} barely forms on this data"
    equal = both_nan | np.isclose(expected, got, rtol=0.0, atol=1e-9)
    if not equal.all():
        i = int(np.flatnonzero(~equal)[0])
        raise AssertionError(
            f"{target} {name} diverges from compute_level at bar {i} "
            f"({bars['time'].iloc[i]}): emitted {got[i]!r} vs backtest {expected[i]!r} "
            f"({int((~equal).sum())} of {len(got)} bars differ)"
        )


# --- mirrors of the emitted Pine ---------------------------------------------
# `x[k]` in Pine is the value k bars back, so Pine offset k reads index i - k.

def pine_swing(bars, name, n=20):
    """`ta.highest(high, 20)[1]` and friends. ta.highest is na until n bars exist,
    and the [1] is the shift(1) that keeps this bar out of its own window."""
    hi = name.endswith("_hi")
    if "_body_" in name:                # math.max(open, close) / math.min(open, close)
        src = [max(o, c) if hi else min(o, c)
               for o, c in zip(bars["open"], bars["close"])]
    else:
        src = list(bars["high"] if hi else bars["low"])
    out = []
    for i in range(len(bars)):
        j = i - 1                       # the [1]
        if j < n - 1:
            out.append(NAN)
            continue
        window = src[j - n + 1:j + 1]
        out.append(max(window) if hi else min(window))
    return out


def pine_fractal(bars, name):
    """`var float f = na` + `if high[2] > high[1] and high[2] > high[0] and
    high[2] > high[3] and high[2] > high[4]` -> `f := high[2]`. The var IS the
    forward fill; bar [2] as the candidate IS the two-bar confirmation lag."""
    hi = name.endswith("_hi")
    src = list(bars["high"] if hi else bars["low"])
    carried, out = NAN, []
    for i in range(len(bars)):
        if i >= 4:                      # high[4] is na before that, so no fractal
            v = src[i - 2]
            a, b, c, d = src[i - 1], src[i - 0], src[i - 3], src[i - 4]
            hit = (v > a and v > b and v > c and v > d) if hi else \
                  (v < a and v < b and v < c and v < d)
            if hit:
                carried = v
        out.append(carried)
    return out


def pine_session(bars, name):
    """The accumulator block: publish the previous day's extreme on the day's
    first bar, then reset. `lvl_utc_day = math.floor(time / 86400000)`."""
    session, side = name.split("_")[1], name.split("_")[2]
    start, end = SESSIONS[session]
    hi = side == "hi"
    times = pd.DatetimeIndex(bars["time"])
    utc_day = _utc_day(times)                         # math.floor(time / 86400000)
    hours = times.hour
    src = list(bars["high"] if hi else bars["low"])
    acc, published, out = NAN, NAN, []
    for i in range(len(bars)):
        if i > 0 and utc_day[i] != utc_day[i - 1]:    # utc_day[1] is na on bar 0
            published, acc = acc, NAN
        if start <= hours[i] < end:
            acc = src[i] if np.isnan(acc) else (max(acc, src[i]) if hi else min(acc, src[i]))
        out.append(published)
    return out


def pine_bb(bars):
    """`ta.sma(close, 20)`."""
    close = list(bars["close"])
    return [NAN if i < 19 else sum(close[i - 19:i + 1]) / 20.0 for i in range(len(bars))]


# --- mirrors of the emitted MQL5 ---------------------------------------------
# shift k at "now = bar i" reads index i - k, and Bars(_Symbol,_Period) is i + 1.
# Every mirror is evaluated at shift 0, the bar an entry opens on.

def mql5_swing(bars, name, n=20):
    """LevelSwing(shift, n, want_high, body)."""
    want_high, body = name.endswith("_hi"), "_body_" in name
    o, c = list(bars["open"]), list(bars["close"])
    h, low = list(bars["high"]), list(bars["low"])
    out = []
    for i in range(len(bars)):
        if i + 1 < n + 1:                     # Bars < shift + n + 1
            out.append(NAN)
            continue
        best = NAN
        for k in range(1, n + 1):             # i = shift+1 .. shift+n
            j = i - k
            if body:
                v = max(o[j], c[j]) if want_high else min(o[j], c[j])
            else:
                v = h[j] if want_high else low[j]
            best = v if np.isnan(best) else (max(best, v) if want_high else min(best, v))
        out.append(best)
    return out


def mql5_fractal(bars, name, scan=5000):
    """LevelFractal(shift, want_high): scan back for the newest CONFIRMED fractal,
    which is the forward fill written as a search."""
    want_high = name.endswith("_hi")
    src = list(bars["high"] if want_high else bars["low"])
    out = []
    for i in range(len(bars)):
        last = min((i + 1) - 3, 1 + scan)     # MathMin(Bars - 3, shift + 1 + scan)
        found = NAN
        for k in range(2, last + 1):
            j = i - k                         # shift k -> index i - k
            v = src[j]
            a, b = src[j + 1], src[j + 2]     # shifts k-1, k-2: the NEWER two bars
            c, d = src[j - 1], src[j - 2]     # shifts k+1, k+2: the OLDER two bars
            hit = (v > a and v > b and v > c and v > d) if want_high else \
                  (v < a and v < b and v < c and v < d)
            if hit:
                found = v
                break
        out.append(found)
    return out


def mql5_session(bars, name, scan=5000):
    """LevelSession(shift, start, end, want_high): walk back past today, take the
    first previous day PRESENT IN THE DATA, and extreme its session bars."""
    session, side = name.split("_")[1], name.split("_")[2]
    start, end = SESSIONS[session]
    want_high = side == "hi"
    times = pd.DatetimeIndex(bars["time"])
    day_of = _utc_day(times)                          # (int)(bar_utc / 86400)
    hours = times.hour
    src = list(bars["high"] if want_high else bars["low"])
    out = []
    for i in range(len(bars)):
        today, prev, best = day_of[i], None, NAN
        total = i + 1
        for k in range(0, min(total, scan + 1)):
            j = i - k
            day = day_of[j]
            if day >= today:
                continue
            if prev is None:
                prev = day
            if day != prev:
                break
            if not (start <= hours[j] < end):
                continue
            v = src[j]
            best = v if np.isnan(best) else (max(best, v) if want_high else min(best, v))
        out.append(best)
    return out


def mql5_bb(bars):
    """LevelBb(shift): SMA(20) of close INCLUDING bar `shift`."""
    close = list(bars["close"])
    return [NAN if i + 1 < 20 else sum(close[i - 19:i + 1]) / 20.0 for i in range(len(bars))]


# --- the parity evidence -----------------------------------------------------

SWINGS = ["prior_swing_wick_hi", "prior_swing_wick_lo",
          "prior_swing_body_hi", "prior_swing_body_lo"]
FRACTALS = ["prior_fractal_hi", "prior_fractal_lo"]
SESSION_LEVELS = [f"session_{s}_{side}" for s in SESSIONS for side in ("hi", "lo")]


@pytest.mark.parametrize("name", SWINGS)
def test_pine_swing_matches_compute_level(name):
    assert_parity(name, pine_swing(BARS, name), BARS, "pine")


@pytest.mark.parametrize("name", FRACTALS)
def test_pine_fractal_matches_compute_level(name):
    assert_parity(name, pine_fractal(BARS, name), BARS, "pine")


@pytest.mark.parametrize("name", SESSION_LEVELS)
def test_pine_session_matches_compute_level(name):
    assert_parity(name, pine_session(BARS, name), BARS, "pine")


def test_pine_bb_line_matches_compute_level():
    assert_parity("bb_line", pine_bb(BARS), BARS, "pine")


@pytest.mark.parametrize("name", SWINGS)
def test_mql5_swing_matches_compute_level(name):
    assert_parity(name, mql5_swing(BARS, name), BARS, "mql5")


@pytest.mark.parametrize("name", FRACTALS)
def test_mql5_fractal_matches_compute_level(name):
    assert_parity(name, mql5_fractal(BARS, name), BARS, "mql5")


@pytest.mark.parametrize("name", SESSION_LEVELS)
def test_mql5_session_matches_compute_level(name):
    assert_parity(name, mql5_session(MONTH, name), MONTH, "mql5")


def test_mql5_bb_line_matches_compute_level():
    assert_parity("bb_line", mql5_bb(BARS), BARS, "mql5")


def test_every_level_feature_has_a_mirror():
    """A level added to the DSL cannot slip past the parity check above."""
    mirrored = set(SWINGS) | set(FRACTALS) | set(SESSION_LEVELS) | {"bb_line"}
    assert mirrored == set(LEVEL_FEATURES)


# --- the lag is the feature: prove it is really there ------------------------

def test_a_swing_level_never_sees_its_own_bar():
    """shift(1). If the emitted window included bar i, this would fail."""
    window = BARS.iloc[:400]
    got = pine_swing(window, "prior_swing_wick_hi")
    highs = list(window["high"])
    peeking = 0
    for i in range(20, len(window)):
        assert got[i] == max(highs[i - 20:i])
        if max(highs[i - 19:i + 1]) != got[i]:
            peeking += 1                # bars where including bar i WOULD change it
    assert peeking > 5, "fixture never makes a new high -- the shift is untested here"


def test_a_fractal_is_published_two_bars_after_the_bar_that_made_it():
    """Move the fractal one bar earlier and the mirror stops matching -- the
    two-bar confirmation lag is load-bearing, not decoration."""
    window = BARS.iloc[:2000]
    correct = np.asarray(pine_fractal(window, "prior_fractal_hi"), dtype=float)
    expected = compute_level("prior_fractal_hi", window).to_numpy(dtype=float)
    early = np.r_[correct[1:], np.nan]          # published one bar sooner
    differing = ~(np.isnan(expected) & np.isnan(early)) & ~np.isclose(
        expected, early, rtol=0.0, atol=1e-9)
    assert differing.sum() > 20, "the fixture never confirms a fractal -- widen it"


# --- why the intra-bar levels have to be shifted at all ----------------------

@pytest.mark.parametrize("name", sorted(INTRA_BAR_LEVELS))
def test_the_shifted_exit_anchors_really_do_read_their_own_bar(name):
    """compiler.py's old claim was "level[i] uses bars <= i-1, all complete at
    bar i's open", and it resolves an exit anchor at the FILL bar. These three
    break that claim: perturbing bar i's own OHLC moves level[i]. That is why
    `exit_anchor` shifts them and both emitters read one bar back."""
    window = BARS.iloc[:600].copy().reset_index(drop=True)
    base = compute_level(name, window).to_numpy(dtype=float)
    bumped = window.copy()
    bumped.loc[:, "high"] = bumped["high"] + 500.0
    bumped.loc[:, "low"] = bumped["low"] - 500.0
    bumped.loc[:, "close"] = bumped["close"] + 500.0
    moved = compute_level(name, bumped).to_numpy(dtype=float)
    # every bar whose own OHLC we moved shows a changed level on that same bar
    changed = ~(np.isnan(base) & np.isnan(moved)) & ~np.isclose(
        base, moved, rtol=0.0, atol=1e-9)
    assert changed.any(), f"{name} does not depend on the current bar after all"


# --- the intra-bar levels: rendered, but one bar back ------------------------
# compiler.exit_anchor shifts exactly INTRA_BAR_LEVELS, and only for an exit.
# The mirrors below are the emitted exit expression -- Pine `level[1]` and MQL5
# shift 1 -- which is the same pure function of `shift` one bar earlier.


def _one_bar_back(mirror: list) -> list:
    """Pine `x[1]` / MQL5 shift+1: the value the bar before."""
    return [NAN] + list(mirror[:-1])


@pytest.mark.parametrize("name", FRACTALS)
def test_pine_exit_anchor_reads_the_previous_fractal_value(name):
    assert_parity(name, _one_bar_back(pine_fractal(BARS, name)), BARS, "pine", shift=1)


def test_pine_exit_anchor_reads_the_previous_bb_line_value():
    assert_parity("bb_line", _one_bar_back(pine_bb(BARS)), BARS, "pine", shift=1)


@pytest.mark.parametrize("name", FRACTALS)
def test_mql5_exit_anchor_reads_the_previous_fractal_value(name):
    assert_parity(name, _one_bar_back(mql5_fractal(BARS, name)), BARS, "mql5", shift=1)


def test_mql5_exit_anchor_reads_the_previous_bb_line_value():
    assert_parity("bb_line", _one_bar_back(mql5_bb(BARS)), BARS, "mql5", shift=1)


@pytest.mark.parametrize("name", sorted(INTRA_BAR_LEVELS))
def test_an_intra_bar_level_exit_renders_shifted_in_both_targets(name):
    sp = spec(stop={"type": "level", "anchor": name, "offset": -3.0})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert f"float new_stop = {name}[1] + (-3)" in pine
    assert f"double stop = {_level_call(name, '1')} + (-3)" in mql
    assert forge_spec(sp).files


@pytest.mark.parametrize("name", sorted(LEVEL_FEATURES - INTRA_BAR_LEVELS))
def test_a_settled_level_exit_is_never_shifted(name):
    """Getting this backwards would price an exit off a bar older than the one
    the backtest used -- a silent, permanent mispricing of every stop."""
    side = "stop" if name.endswith("_lo") else "target"
    sp = spec(**{side: {"type": "level", "anchor": name, "offset": 0.0}})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert f"new_{side} = {name} + (0)" in pine
    assert f"{name}[1]" not in pine
    assert _level_call(name, "0") in mql and _level_call(name, "1") not in mql


def test_a_level_exit_with_a_resting_entry_fails_closed():
    """1R is measured from the fill bar's level; a broker needs the volume when
    the order is SENT. Sizing off the placement bar would ship an untested size."""
    sp = spec(stop={"type": "level", "anchor": "prior_swing_wick_lo"},
              entry={"type": "limit", "offset": 0.5, "expiry_bars": 5})
    for generate in (generate_pine, generate_mql5):
        with pytest.raises(ValueError, match="only known at the fill"):
            generate(sp)


# --- what now renders --------------------------------------------------------

EXIT_ANCHORS = SWINGS + SESSION_LEVELS


@pytest.mark.parametrize("name", [n for n in EXIT_ANCHORS if n.endswith("_lo")])
def test_a_level_stop_renders_in_both_targets(name):
    sp = spec(stop={"type": "level", "anchor": name, "offset": -3.0})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert f"float new_stop = {name} + (-3)" in pine
    assert "double stop = Level" in mql and "+ (-3)" in mql
    # both must still fail closed on an incoherent anchor, as try_open does
    assert "strategy.close_all(comment=\"no level\"" in pine
    assert "if(is_long ? !(stop < price && target > price)" in mql


@pytest.mark.parametrize("name", [n for n in EXIT_ANCHORS if n.endswith("_hi")])
def test_a_level_target_renders_in_both_targets(name):
    sp = spec(target={"type": "level", "anchor": name, "offset": 0.0})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert f"float new_target = {name} + (0)" in pine
    assert "double target = Level" in mql


def test_a_level_stop_with_a_ratio_target_measures_r_from_the_level():
    sp = spec(stop={"type": "level", "anchor": "prior_swing_wick_lo"})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert "math.abs(entry_price - new_stop) * 2" in pine
    assert "double stop_distance = MathAbs(price - stop);" in mql
    assert "stop_distance * 2" in mql


@pytest.mark.parametrize("name", sorted(LEVEL_FEATURES))
@pytest.mark.parametrize("kind", ["limit", "stop"])
def test_every_level_can_anchor_a_resting_entry(name, kind):
    """The entry anchor is read on the SIGNAL bar, which is exactly when both
    targets evaluate -- so even the current-bar levels are renderable here."""
    sp = spec(entry={"type": kind, "anchor": name, "expiry_bars": 5})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    keyword = "limit" if kind == "limit" else "stop"
    assert f'strategy.entry("Long", strategy.long, {keyword}={name})' in pine
    assert f'strategy.entry("Short", strategy.short, {keyword}={name})' in pine
    assert f"and not na({name})" in pine
    assert "double anchor = Level" in mql
    assert "if(anchor == EMPTY_VALUE || !MathIsValidNumber(anchor)) return false;" in mql


def test_an_anchored_entry_applies_no_offset():
    """compiler.py rests at `entry_lvl[i]` flat -- offset belongs to the
    price-relative form only. Adding it here would move every fill."""
    sp = spec(entry={"type": "limit", "anchor": "prior_fractal_lo",
                     "offset": 0.75, "expiry_bars": 5})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert "0.75" not in pine and "0.75" not in mql
    assert "limit=prior_fractal_lo)" in pine
    assert "double price = NormalizeDouble(anchor, digits);" in mql


def test_a_swept_categorical_anchor_takes_the_middle_of_the_grid():
    sp = spec(stop={"type": "level", "anchor": "p[anc]"})
    sp.param_space = {"anc": ["prior_swing_wick_lo", "prior_swing_body_lo",
                             "session_ny_lo"]}
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert "prior_swing_body_lo" in pine          # the middle grid point
    assert "LevelSwing(0, 20, false, true)" in mql
    assert "input double InpAnc" not in mql       # a categorical is not an input


def test_the_forge_bundle_carries_a_level_strategy():
    sp = spec(stop={"type": "level", "anchor": "prior_swing_wick_lo"},
              target={"type": "level", "anchor": "prior_swing_wick_hi"})
    bundle = forge_spec(sp)
    assert any(name.endswith(".pine") for name in bundle.files)
    assert any(name.endswith(".mq5") for name in bundle.files)


# --- the guards that must NOT have been widened ------------------------------

def test_trail_break_even_and_no_trade_still_fail_closed():
    for wrapper in ({"trail": {"type": "atr", "mult": 2.0}},
                    {"break_even": {"type": "after_r", "trigger_r": 1.0}},
                    {"constraints": {"no_trade": ["news"]}}):
        sp = spec(stop={"type": "level", "anchor": "prior_swing_wick_lo"}, **wrapper)
        for generate in (generate_pine, generate_mql5):
            with pytest.raises(ValueError):
                generate(sp)


def test_an_unimplemented_alpha_feature_still_fails_closed():
    sp = spec(stop={"type": "level", "anchor": "prior_swing_wick_lo"})
    sp.alpha.features = ["atr_14", "psar"]
    sp.alpha.signal = "close > psar"
    sp.alpha.signal_short = "close < psar"
    for generate in (generate_pine, generate_mql5):
        with pytest.raises(ValueError, match="does not implement features"):
            generate(sp)
