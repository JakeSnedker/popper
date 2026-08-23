"""Forge parity for DSL signal features.

Same method as tests/test_forge_levels.py, and for the same reason: Pine and
MQL5 do not run here, so a structural string check cannot prove an emitter
right. Every feature line the emitters render is transliterated BACK into
Python -- line by line, in the shape the target executes -- and compared
element by element against popper.dsl.features.compute_feature on real US30 M5
bars. A one-bar disagreement is a lag bug.

Reading the mirrors:
  Pine   `x[k]` is the value k bars back, so Pine offset k reads index i - k.
  MQL5   a helper is a pure function of `shift` with Bars = i + 1, so the
         mirror evaluates it at shift 0 for every bar. (The emitted code calls
         it at shift 1 -- the last completed bar -- but `Bars - 1` moves with
         `shift`, so shift 1 at bar i is shift 0 at bar i - 1.)

The mirrors are deliberately dumb loops. They are not a second implementation
of the feature; they are what the emitted source says, rewritten in Python. If
an emitter line changes, its mirror must change with it.
"""

import numpy as np
import pandas as pd
import pytest

from popper.artifacts import StrategySpec
from popper.data.bars import load_bars
from popper.dsl.features import OPENING_RANGE_BARS, SESSIONS, compute_feature
from popper.forge.mql5 import generate_mql5
from popper.forge.pine import generate_pine

BARS = load_bars("US30", "M5", "2025-01-01", "2025-03-31")
# the MQL5 mirrors re-walk history for every bar, so they run on a window rather
# than the quarter. It is shorter than InpWarmupBars, so every walk reaches bar 0
# and the recursion is seeded exactly where the Python seeds it.
WARMUP = 2000
WALK = BARS.iloc[:1200].reset_index(drop=True)

NAN = float("nan")


def spec(features, signal, short=None):
    return StrategySpec.model_validate({
        "id": "S-1", "hypothesis_id": "H-1", "name": "features",
        "alpha": {"features": ["atr_14", *features], "signal": signal,
                  "signal_short": short or f"not ({signal})"},
        "wrapper": {}, "context": {"universe": ["US30"], "timeframe": "5m"},
    })


def assert_parity(name: str, mirror: list, bars: pd.DataFrame, target: str,
                  min_finite: float = 0.5):
    """Bar-for-bar equality with compute_feature; only NaN-vs-NaN may differ."""
    expected = compute_feature(name, bars).to_numpy(dtype=float)
    got = np.asarray(mirror, dtype=float)
    assert got.shape == expected.shape
    both_nan = np.isnan(expected) & np.isnan(got)
    assert np.isfinite(expected).mean() > min_finite, f"{name} barely forms on this data"
    equal = both_nan | np.isclose(expected, got, rtol=0.0, atol=1e-9)
    if not equal.all():
        i = int(np.flatnonzero(~equal)[0])
        raise AssertionError(
            f"{target} {name} diverges from compute_feature at bar {i} "
            f"({bars['time'].iloc[i]}): emitted {got[i]!r} vs backtest {expected[i]!r} "
            f"({int((~equal).sum())} of {len(got)} bars differ)"
        )


def _utc_day(times: pd.DatetimeIndex):
    """Whole UTC days since the epoch -- Pine's math.floor(time / 86400000) and
    MQL5's (int)(bar_utc / 86400)."""
    return (times - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1D")


# --- shared by both targets ---------------------------------------------------

def mirror_session(bars, name):
    """`hour(time, "UTC") >= s and hour(time, "UTC") < e ? 1.0 : 0.0`, and the
    identical MQL5 `(utc.hour >= s && utc.hour < e) ? 1.0 : 0.0`."""
    start, end = SESSIONS[name.split("_")[1]]
    hours = pd.DatetimeIndex(bars["time"]).hour
    return [1.0 if start <= h < end else 0.0 for h in hours]


def mirror_candle_size(bars):
    return [h - l for h, l in zip(bars["high"], bars["low"])]


def mirror_fvg(bars):
    """Pine `(not na(high[2]) and high[2] < low ? 1 : 0) - (not na(low[2]) and
    low[2] > high ? 1 : 0)`, and the identical MQL5 `Fvg(shift)` under its
    `Bars < shift + 3` guard. Warm-up is 0.0, not na: the Python compares
    against NaN, which is False."""
    high, low = list(bars["high"]), list(bars["low"])
    out = []
    for i in range(len(bars)):
        if i < 2:
            out.append(0.0)
            continue
        value = 0.0
        if high[i - 2] < low[i]:
            value += 1.0
        if low[i - 2] > high[i]:
            value -= 1.0
        out.append(value)
    return out


def mirror_liquidity_sweep(bars):
    """Pine `ta.lowest(low, 20)[1]` / `ta.highest(high, 20)[1]` and the MQL5 loop
    over shift+1..shift+20 -- the same shifted window in both."""
    high, low, close = list(bars["high"]), list(bars["low"]), list(bars["close"])
    out = []
    for i in range(len(bars)):
        if i < 20:
            out.append(0.0)
            continue
        prior_lo, prior_hi = min(low[i - 20:i]), max(high[i - 20:i])
        value = 0.0
        if low[i] < prior_lo and close[i] > prior_lo:
            value += 1.0
        if high[i] > prior_hi and close[i] < prior_hi:
            value -= 1.0
        out.append(value)
    return out


def mirror_body_pressure(bars):
    """`(high - low) == 0 ? na : (close - open) / (high - low)`."""
    return [NAN if h - l == 0 else (c - o) / (h - l)
            for o, h, l, c in zip(bars["open"], bars["high"], bars["low"], bars["close"])]


# --- mirrors of the emitted Pine ---------------------------------------------

def pine_bb_line(bars):
    """`ta.sma(close, 20)`."""
    close = list(bars["close"])
    return [NAN if i < 19 else sum(close[i - 19:i + 1]) / 20.0 for i in range(len(bars))]


def pine_rsi_fast(bars):
    """`rsi_gain := na(rsi_delta) ? na : na(rsi_gain[1]) ? math.max(rsi_delta, 0)
    : rsi_gain[1] + (math.max(rsi_delta, 0) - rsi_gain[1]) / 7` and the loss twin,
    published as `bar_index >= 7 and rsi_loss > 0 ? 100 - 100/(1 + g/l) : 50`."""
    close = list(bars["close"])
    gain = loss = NAN
    out = []
    for i in range(len(bars)):
        delta = NAN if i == 0 else close[i] - close[i - 1]
        if np.isnan(delta):
            gain = loss = NAN
        else:
            up, down = max(delta, 0.0), max(-delta, 0.0)
            gain = up if np.isnan(gain) else gain + (up - gain) / 7.0
            loss = down if np.isnan(loss) else loss + (down - loss) / 7.0
        publish = i >= 7 and not np.isnan(loss) and loss > 0
        out.append(100.0 - 100.0 / (1.0 + gain / loss) if publish else 50.0)
    return out


def pine_psar(bars):
    """The `var`-carried forward loop: seed on bar 0, then advance, clamp against
    the two prior extremes, flip on a breach. `psar_value` stays na on bar 0."""
    high, low = list(bars["high"]), list(bars["low"])
    up, af, ep, sar, value = True, 0.02, NAN, NAN, NAN
    out = []
    for i in range(len(bars)):
        if i == 0:
            sar, ep = low[0], high[0]
        else:
            sar = sar + af * (ep - sar)
            if up:
                sar = min(sar, min(low[i - 1], low[i - 2] if i >= 2 else low[i - 1]))
                if low[i] < sar:
                    up, sar, ep, af = False, ep, low[i], 0.02
                elif high[i] > ep:
                    ep, af = high[i], min(af + 0.02, 0.2)
            else:
                sar = max(sar, max(high[i - 1], high[i - 2] if i >= 2 else high[i - 1]))
                if high[i] > sar:
                    up, sar, ep, af = True, ep, high[i], 0.02
                elif low[i] < ep:
                    ep, af = low[i], min(af + 0.02, 0.2)
            value = sar
        out.append(value)
    return out


def pine_opening_range(bars):
    """The `or_*` accumulator block: reset on a new UTC day, count NY bars, take
    the extreme of the first OPENING_RANGE_BARS of them, publish from the last
    of those onward."""
    times = pd.DatetimeIndex(bars["time"])
    utc_day, hours = _utc_day(times), times.hour
    start, end = SESSIONS["ny"]
    n = OPENING_RANGE_BARS
    pos, hi, lo, rng = -1, NAN, NAN, NAN
    out = []
    for i in range(len(bars)):
        if i > 0 and utc_day[i] != utc_day[i - 1]:   # or_utc_day[1] is na on bar 0
            pos, hi, lo, rng = -1, NAN, NAN, NAN
        if start <= hours[i] < end:
            pos += 1
            if pos < n:
                h, l = bars["high"].iloc[i], bars["low"].iloc[i]
                hi = h if np.isnan(hi) else max(hi, h)
                lo = l if np.isnan(lo) else min(lo, l)
                rng = hi - lo
        out.append(rng if pos >= n - 1 else NAN)
    return out


def pine_htf_trend(bars):
    """`htf_ema_48 := na(htf_ema_48[1]) ? close : htf_ema_48[1] + (close -
    htf_ema_48[1]) * (2/49)`, the span-240 twin, and
    `bar_index >= 239 ? math.sign(fast - slow) : na`."""
    close = list(bars["close"])
    fast = slow = NAN
    out = []
    for i in range(len(bars)):
        fast = close[i] if np.isnan(fast) else fast + (close[i] - fast) * (2.0 / 49.0)
        slow = close[i] if np.isnan(slow) else slow + (close[i] - slow) * (2.0 / 241.0)
        out.append(np.sign(fast - slow) if i >= 239 else NAN)
    return out


# --- mirrors of the emitted MQL5 ---------------------------------------------

def mql5_bb_line(bars):
    """`LevelBb(shift)`: SMA(20) of close INCLUDING bar `shift`."""
    close = list(bars["close"])
    return [NAN if i + 1 < 20 else sum(close[i - 19:i + 1]) / 20.0 for i in range(len(bars))]


def mql5_rsi_fast(bars, warmup=WARMUP):
    """`RsiFast(shift)`: re-walk the Wilder recursion from `last` down to `shift`."""
    close = list(bars["close"])
    out = []
    for i in range(len(bars)):
        last = min(i, warmup)                     # MathMin(Bars - 1, shift + warmup)
        if last < 7:                              # last - shift < 7
            out.append(50.0)
            continue
        gain = loss = 0.0
        for k in range(last - 1, -1, -1):         # loop var i, from last-1 down to shift
            delta = close[i - k] - close[i - k - 1]
            up, down = max(delta, 0.0), max(-delta, 0.0)
            if k == last - 1:
                gain, loss = up, down
            else:
                gain += (up - gain) / 7.0
                loss += (down - loss) / 7.0
        out.append(50.0 if loss <= 0.0 else 100.0 - 100.0 / (1.0 + gain / loss))
    return out


def mql5_psar(bars, warmup=WARMUP):
    """`Psar(shift)`: re-walk the SAR loop from `last` down to `shift`."""
    high, low = list(bars["high"]), list(bars["low"])
    out = []
    for i in range(len(bars)):
        last = min(i, warmup)
        if last <= 0:
            out.append(NAN)
            continue
        up, af = True, 0.02
        sar, ep = low[i - last], high[i - last]
        value = NAN
        for k in range(last - 1, -1, -1):
            step = last - k
            h, l = high[i - k], low[i - k]
            sar = sar + af * (ep - sar)
            if up:
                prev = low[i - k - 1]
                sar = min(sar, min(prev, low[i - k - 2] if step >= 2 else prev))
                if l < sar:
                    up, sar, ep, af = False, ep, l, 0.02
                elif h > ep:
                    ep, af = h, min(af + 0.02, 0.2)
            else:
                prev = high[i - k - 1]
                sar = max(sar, max(prev, high[i - k - 2] if step >= 2 else prev))
                if h > sar:
                    up, sar, ep, af = True, ep, h, 0.02
                elif l < ep:
                    ep, af = l, min(af + 0.02, 0.2)
            value = sar
        out.append(value)
    return out


def mql5_htf_trend(bars, warmup=WARMUP):
    """`PopEma(shift, 48)` and `PopEma(shift, 240)`, then the EMPTY_VALUE guard
    and `Sign(fast - slow)` from the emitted feature lines."""
    close = list(bars["close"])

    def pop_ema(i, period):
        last = min(i, warmup)                    # MathMin(Bars - 1, shift + warmup)
        if last + 1 < period:                    # last - shift + 1 < period
            return NAN
        alpha = 2.0 / (period + 1.0)
        value = close[i - last]
        for k in range(last - 1, -1, -1):
            value += (close[i - k] - value) * alpha
        return value

    out = []
    for i in range(len(bars)):
        fast, slow = pop_ema(i, 48), pop_ema(i, 240)
        out.append(NAN if np.isnan(fast) or np.isnan(slow) else np.sign(fast - slow))
    return out


def mql5_opening_range(bars, scan=5000):
    """`OpeningRange(shift)`: walk back to the oldest bar of the same UTC day,
    then forward, counting NY bars."""
    times = pd.DatetimeIndex(bars["time"])
    day_of, hours = _utc_day(times), times.hour
    high, low = list(bars["high"]), list(bars["low"])
    start, end = SESSIONS["ny"]
    n = OPENING_RANGE_BARS
    out = []
    for i in range(len(bars)):
        today, total, oldest = day_of[i], i + 1, 0
        for k in range(0, min(total, scan + 1)):
            if day_of[i - k] != today:
                break
            oldest = k
        pos, hi, lo, rng = -1, NAN, NAN, NAN
        for k in range(oldest, -1, -1):
            if not (start <= hours[i - k] < end):
                continue
            pos += 1
            if pos >= n:
                continue
            h, l = high[i - k], low[i - k]
            hi = h if np.isnan(hi) else max(hi, h)
            lo = l if np.isnan(lo) else min(lo, l)
            rng = hi - lo
        out.append(rng if pos >= n - 1 else NAN)
    return out


# --- the parity evidence -----------------------------------------------------

SESSION_FEATURES = ["session_ny", "session_asia", "session_london"]


@pytest.mark.parametrize("name", SESSION_FEATURES)
@pytest.mark.parametrize("target", ["pine", "mql5"])
def test_session_matches_compute_feature(name, target):
    assert_parity(name, mirror_session(BARS, name), BARS, target, min_finite=0.99)


@pytest.mark.parametrize("target", ["pine", "mql5"])
def test_candle_size_matches_compute_feature(target):
    assert_parity("candle_size", mirror_candle_size(BARS), BARS, target)


@pytest.mark.parametrize("target", ["pine", "mql5"])
def test_body_pressure_matches_compute_feature(target):
    assert_parity("body_pressure", mirror_body_pressure(BARS), BARS, target)


@pytest.mark.parametrize("target", ["pine", "mql5"])
def test_fvg_matches_compute_feature(target):
    assert_parity("fvg", mirror_fvg(BARS), BARS, target, min_finite=0.99)


@pytest.mark.parametrize("target", ["pine", "mql5"])
def test_liquidity_sweep_matches_compute_feature(target):
    assert_parity("liquidity_sweep", mirror_liquidity_sweep(BARS), BARS, target,
                  min_finite=0.99)


def test_pine_htf_trend_matches_compute_feature():
    assert_parity("htf_trend", pine_htf_trend(BARS), BARS, "pine")


def test_mql5_htf_trend_matches_compute_feature():
    assert_parity("htf_trend", mql5_htf_trend(WALK), WALK, "mql5", min_finite=0.2)


def test_pine_bb_line_matches_compute_feature():
    assert_parity("bb_line", pine_bb_line(BARS), BARS, "pine")


def test_mql5_bb_line_matches_compute_feature():
    assert_parity("bb_line", mql5_bb_line(BARS), BARS, "mql5")


def test_pine_rsi_fast_matches_compute_feature():
    assert_parity("rsi_fast", pine_rsi_fast(BARS), BARS, "pine")


def test_mql5_rsi_fast_matches_compute_feature():
    assert_parity("rsi_fast", mql5_rsi_fast(WALK), WALK, "mql5")


def test_pine_psar_matches_compute_feature():
    assert_parity("psar", pine_psar(BARS), BARS, "pine")


def test_mql5_psar_matches_compute_feature():
    assert_parity("psar", mql5_psar(WALK), WALK, "mql5")


def test_pine_opening_range_matches_compute_feature():
    assert_parity("opening_range", pine_opening_range(BARS), BARS, "pine", min_finite=0.25)


def test_mql5_opening_range_matches_compute_feature():
    assert_parity("opening_range", mql5_opening_range(WALK), WALK, "mql5", min_finite=0.25)


# --- the lag and the fill-values are load-bearing, so prove they are there ----

def test_rsi_fast_is_not_the_platform_rsi():
    """ta.rsi / iRSI seed Wilder smoothing on an SMA of the first 7 deltas; the
    Python seeds on the FIRST delta. Emitting the native call would be wrong for
    over a hundred bars, so neither emitter may name it."""
    close = BARS["close"].to_numpy()
    delta = np.diff(close, prepend=np.nan)
    up = np.where(delta > 0, delta, 0.0)
    down = np.where(delta < 0, -delta, 0.0)
    gain = np.full(len(close), np.nan)
    loss = np.full(len(close), np.nan)
    gain[7], loss[7] = up[1:8].mean(), down[1:8].mean()   # the SMA seed
    for i in range(8, len(close)):
        gain[i] = gain[i - 1] + (up[i] - gain[i - 1]) / 7.0
        loss[i] = loss[i - 1] + (down[i] - loss[i - 1]) / 7.0
    with np.errstate(divide="ignore", invalid="ignore"):
        native = 100.0 - 100.0 / (1.0 + gain / np.where(loss == 0, np.nan, loss))
    native = np.where(np.isnan(native), 50.0, native)
    ours = compute_feature("rsi_fast", BARS).to_numpy()
    assert (np.abs(native - ours) > 1e-6).sum() > 50, "the two seeds agree -- reconsider"

    sp = spec(["rsi_fast"], "rsi_fast > 60")
    assert "rsi_fast = ta.rsi" not in generate_pine(sp)
    assert "RsiFast(1)" in generate_mql5(sp)


def test_psar_is_not_the_platform_sar():
    sp = spec(["psar"], "close > psar")
    assert "psar = ta.sar" not in generate_pine(sp)
    assert "double psar = Psar(1);" in generate_mql5(sp)


def test_opening_range_is_na_until_the_range_has_fully_formed():
    """Publishing on the 5th NY bar instead of the 6th would leak an unfinished
    range into the signal."""
    got = np.asarray(pine_opening_range(BARS), dtype=float)
    early = np.r_[got[1:], np.nan]
    expected = compute_feature("opening_range", BARS).to_numpy(dtype=float)
    differing = ~(np.isnan(expected) & np.isnan(early)) & ~np.isclose(
        expected, early, rtol=0.0, atol=1e-9)
    assert differing.sum() > 20, "the fixture never forms a range -- widen it"


def test_a_late_started_recursion_still_converges():
    """InpWarmupBars is the EA's stand-in for the Python's dataset origin. This
    is the claim that makes it safe: start RSI(7) 500 bars late and it lands on
    the same number. If this ever fails, the input needs raising, not ignoring."""
    late = mql5_rsi_fast(BARS.iloc[:1500].reset_index(drop=True), warmup=500)
    full = compute_feature("rsi_fast", BARS.iloc[:1500]).to_numpy(dtype=float)
    assert np.abs(np.asarray(late)[600:] - full[600:]).max() < 1e-9


# --- what now renders --------------------------------------------------------

NEW_FEATURES = {
    "bb_line": "close > bb_line",
    "body_pressure": "body_pressure > 0.5",
    "candle_size": "candle_size > 10",
    "opening_range": "opening_range > 20",
    "psar": "close > psar",
    "rsi_fast": "rsi_fast > 60",
    "session_asia": "session_asia == 1",
    "session_london": "session_london == 1",
    "fvg": "fvg > 0",
    "htf_trend": "htf_trend > 0",
    "liquidity_sweep": "liquidity_sweep > 0",
}


@pytest.mark.parametrize("name,signal", sorted(NEW_FEATURES.items()))
def test_the_feature_renders_and_is_named_in_both_targets(name, signal):
    sp = spec([name], signal)
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert f"{name} = " in pine, "the signal references the bare name, so it must be bound"
    assert f"double {name} = " in mql
    assert name in mql.split("bool long_signal =")[-1]


def test_bb_line_is_declared_once_when_it_is_also_the_entry_anchor():
    sp = spec(["bb_line"], "close > bb_line")
    sp.wrapper.entry.type = "limit"
    sp.wrapper.entry.anchor = "bb_line"
    pine = generate_pine(sp)
    assert pine.count("bb_line = ta.sma(close, 20)") == 1
