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
from popper.dsl.features import (
    CISD_RUN,
    OB_DISP_MULT,
    OPENING_RANGE_BARS,
    SESSIONS,
    SWING_LOOKBACK,
    compute_feature,
)
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


# --- the SMC pack: mirrors of the emitted Pine -------------------------------

def pine_pivots(bars, n):
    """`if bar_index >= 2n and (high[n-k] < high[n] and high[n+k] < high[n] ...)`
    -> `pop_swing_hi_n := high[n]`. Pine offset n-k is index i-n+k (the NEWER
    side) and n+k is i-n-k (the OLDER side); the centre is i-n, which is why the
    pivot only appears n bars after it forms."""
    high, low = list(bars["high"]), list(bars["low"])
    carried_hi = carried_lo = NAN
    hi_out, lo_out = [], []
    for i in range(len(bars)):
        if i >= 2 * n:
            cand_hi, cand_lo = high[i - n], low[i - n]
            if all(high[i - n + k] < cand_hi and high[i - n - k] < cand_hi
                   for k in range(1, n + 1)):
                carried_hi = cand_hi
            if all(low[i - n + k] > cand_lo and low[i - n - k] > cand_lo
                   for k in range(1, n + 1)):
                carried_lo = cand_lo
        hi_out.append(carried_hi)
        lo_out.append(carried_lo)
    return hi_out, lo_out


def pine_pop_atr(bars):
    """`pop_true_range` + `pop_atr_raw := na(pop_atr_raw[1]) ? tr : raw[1] +
    (tr - raw[1]) / 14`, published from bar_index 13."""
    high, low, close = list(bars["high"]), list(bars["low"]), list(bars["close"])
    atr = NAN
    out = []
    for i in range(len(bars)):
        rng = (high[i] - low[i]) if i == 0 else max(
            high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        atr = rng if np.isnan(atr) else atr + (rng - atr) / 14.0
        out.append(atr if i >= 13 else NAN)
    return out


def pine_fib_pos(bars, n=SWING_LOOKBACK):
    hi, lo = pine_pivots(bars, n)
    close = list(bars["close"])
    return [NAN if hi[i] - lo[i] == 0 else (close[i] - lo[i]) / (hi[i] - lo[i])
            for i in range(len(bars))]


def pine_equilibrium(bars, n=SWING_LOOKBACK):
    hi, lo = pine_pivots(bars, n)
    return [(hi[i] + lo[i]) / 2.0 for i in range(len(bars))]


def pine_bos(bars, n=SWING_LOOKBACK):
    """The `pop_bos_*` ternary. `pop_swing_hi_n[1]` is the pivot as of the
    previous bar, which is what makes this a fresh-break event."""
    hi, lo = pine_pivots(bars, n)
    close = list(bars["close"])
    out = []
    for i in range(len(bars)):
        if i < 1:
            out.append(0.0)
            continue
        up_now = False if np.isnan(hi[i]) else close[i] > hi[i]
        dn_now = False if np.isnan(lo[i]) else close[i] < lo[i]
        up_prev = False if np.isnan(hi[i - 1]) else close[i - 1] > hi[i - 1]
        dn_prev = False if np.isnan(lo[i - 1]) else close[i - 1] < lo[i - 1]
        out.append(1.0 if (up_now and not up_prev)
                   else (-1.0 if (dn_now and not dn_prev) else 0.0))
    return out


def pine_mss(bars, n=SWING_LOOKBACK):
    """`var float pop_mss_prev_n = 0.0` carried across bars."""
    bos = pine_bos(bars, n)
    prev = 0.0
    out = []
    for value in bos:
        result = 0.0
        if value != 0.0:
            result = value if (prev != 0.0 and value != prev) else 0.0
            prev = value
        out.append(result)
    return out


def pine_order_block(bars, want_bull, n=SWING_LOOKBACK, disp=OB_DISP_MULT):
    """The `pop_ob_*` var block, in emitted order: arm, mitigate, publish, then
    record this bar as the next candidate."""
    hi, lo = pine_pivots(bars, n)
    atr = pine_pop_atr(bars)
    o, h, l, c = (list(bars[x]) for x in ("open", "high", "low", "close"))
    b_lo = b_hi = s_lo = s_hi = d_lo = d_hi = u_lo = u_hi = NAN
    out = []
    for i in range(len(bars)):
        if c[i] > o[i] and not np.isnan(d_lo):
            broke = (not np.isnan(hi[i])) and c[i] > hi[i]
            big = (not np.isnan(atr[i])) and (c[i] - o[i]) >= disp * atr[i]
            if broke or big:
                b_lo, b_hi = d_lo, d_hi
        if c[i] < o[i] and not np.isnan(u_lo):
            broke = (not np.isnan(lo[i])) and c[i] < lo[i]
            big = (not np.isnan(atr[i])) and (o[i] - c[i]) >= disp * atr[i]
            if broke or big:
                s_lo, s_hi = u_lo, u_hi
        if not np.isnan(b_lo) and l[i] < b_lo:
            b_lo = b_hi = NAN
        if not np.isnan(s_hi) and h[i] > s_hi:
            s_lo = s_hi = NAN
        value = NAN
        if want_bull:
            if not np.isnan(b_lo):
                value = (b_hi + b_lo) / 2.0
        elif not np.isnan(s_hi):
            value = (s_hi + s_lo) / 2.0
        out.append(value)
        if c[i] < o[i]:
            d_lo, d_hi = l[i], h[i]
        elif c[i] > o[i]:
            u_lo, u_hi = l[i], h[i]
    return out


def mirror_cisd(bars, run=CISD_RUN):
    """The `pop_cisd_*` var block; the MQL5 Cisd walk is the same state machine
    replayed from the start of its window, so one mirror serves both."""
    o, c = list(bars["open"]), list(bars["close"])
    run_dir = run_len = 0
    run_open = arm_dn = arm_up = NAN
    out = []
    for i in range(len(bars)):
        value = 0.0
        if not np.isnan(arm_dn) and c[i] > arm_dn:
            value, arm_dn = 1.0, NAN
        elif not np.isnan(arm_up) and c[i] < arm_up:
            value, arm_up = -1.0, NAN
        d = 1 if c[i] > o[i] else (-1 if c[i] < o[i] else 0)
        if d != 0 and d == run_dir:
            run_len += 1
        elif d != 0:
            run_dir, run_len, run_open = d, 1, o[i]
        if run_dir == -1 and run_len >= run:
            arm_dn = run_open
        elif run_dir == 1 and run_len >= run:
            arm_up = run_open
        out.append(value)
    return out


# --- the SMC pack: mirrors of the emitted MQL5 -------------------------------
# SwingPivot/Bos/Cisd/OrderBlock take a `shift`, and `Bars` moves with it, so
# "the helper at shift s when now = bar i" is "the helper at shift 0 when now =
# bar i - s". Every mirror below is therefore built at shift 0, bar by bar.

STRUCTURE = 1000     # InpStructureBars
# the MQL5 structural mirrors re-walk history for every bar, so they run on a
# window shorter than InpStructureBars: every walk reaches bar 0 and the state
# machines are seeded exactly where the Python seeds them.
SMC = BARS.iloc[:600].reset_index(drop=True)


def mql5_pivots(bars, n, structure=STRUCTURE):
    """`SwingPivot(shift, lookback, want_high)` for every bar."""
    high, low = list(bars["high"]), list(bars["low"])

    def scan(i, src, want_high):
        total = i + 1
        last = min(total - 1 - n, n + structure)
        for m in range(n, last + 1):
            v = src[i - m]
            ok = True
            for k in range(1, n + 1):
                newer, older = src[i - m + k], src[i - m - k]
                if (newer >= v or older >= v) if want_high else (newer <= v or older <= v):
                    ok = False
                    break
            if ok:
                return v
        return NAN

    return ([scan(i, high, True) for i in range(len(bars))],
            [scan(i, low, False) for i in range(len(bars))])


def mql5_bos(bars, n=SWING_LOOKBACK, structure=STRUCTURE):
    """`Bos(shift, lookback)`: SwingPivot at `shift` and at `shift + 1`, which
    is the pivot array one bar back."""
    hi, lo = mql5_pivots(bars, n, structure)
    close = list(bars["close"])
    out = []
    for i in range(len(bars)):
        if i + 1 < 2:                       # Bars < shift + 2
            out.append(0.0)
            continue
        up_now = hi[i] == hi[i] and close[i] > hi[i]
        dn_now = lo[i] == lo[i] and close[i] < lo[i]
        up_prev = hi[i - 1] == hi[i - 1] and close[i - 1] > hi[i - 1]
        dn_prev = lo[i - 1] == lo[i - 1] and close[i - 1] < lo[i - 1]
        out.append(1.0 if (up_now and not up_prev)
                   else (-1.0 if (dn_now and not dn_prev) else 0.0))
    return out


def mql5_mss(bars, n=SWING_LOOKBACK, structure=STRUCTURE):
    """`Mss(shift, lookback)`: walk back to the nearest EARLIER non-zero Bos."""
    bos = mql5_bos(bars, n, structure)
    out = []
    for i in range(len(bars)):
        if bos[i] == 0.0:
            out.append(0.0)
            continue
        value = 0.0
        for k in range(1, min(i + 1, structure + 1)):
            previous = bos[i - k]           # Bos at shift k
            if previous != 0.0:
                value = bos[i] if previous != bos[i] else 0.0
                break
        out.append(value)
    return out


def mql5_cisd(bars, run=CISD_RUN, structure=STRUCTURE):
    """`Cisd(shift, run)`: replay the state machine from `last` down to `shift`."""
    o, c = list(bars["open"]), list(bars["close"])
    out = []
    for i in range(len(bars)):
        last = min(i, structure)
        run_dir = run_len = 0
        run_open = arm_dn = arm_up = NAN
        value = 0.0
        for k in range(last, -1, -1):
            oo, cc = o[i - k], c[i - k]
            value = 0.0
            if not np.isnan(arm_dn) and cc > arm_dn:
                value, arm_dn = 1.0, NAN
            elif not np.isnan(arm_up) and cc < arm_up:
                value, arm_up = -1.0, NAN
            d = 1 if cc > oo else (-1 if cc < oo else 0)
            if d != 0 and d == run_dir:
                run_len += 1
            elif d != 0:
                run_dir, run_len, run_open = d, 1, oo
            if run_dir == -1 and run_len >= run:
                arm_dn = run_open
            elif run_dir == 1 and run_len >= run:
                arm_up = run_open
        out.append(value)
    return out


def mql5_equilibrium(bars, n=SWING_LOOKBACK, structure=STRUCTURE):
    hi, lo = mql5_pivots(bars, n, structure)
    return [NAN if np.isnan(hi[i]) or np.isnan(lo[i]) else (hi[i] + lo[i]) / 2.0
            for i in range(len(bars))]


def mql5_fib_pos(bars, n=SWING_LOOKBACK, structure=STRUCTURE):
    hi, lo = mql5_pivots(bars, n, structure)
    close = list(bars["close"])
    return [NAN if np.isnan(hi[i]) or np.isnan(lo[i]) or hi[i] == lo[i]
            else (close[i] - lo[i]) / (hi[i] - lo[i]) for i in range(len(bars))]


def mql5_order_block(bars, want_bull, n=SWING_LOOKBACK, disp=OB_DISP_MULT,
                     structure=STRUCTURE):
    """`OrderBlock(shift, lookback, disp, want_bull)`, including the atr_14
    recursion the helper runs inside its own walk."""
    hi, lo = mql5_pivots(bars, n, structure)
    o, h, l, c = (list(bars[x]) for x in ("open", "high", "low", "close"))
    out = []
    for i in range(len(bars)):
        last = min(i, structure)
        b_lo = b_hi = s_lo = s_hi = d_lo = d_hi = u_lo = u_hi = NAN
        atr, seen, prev_close, value = 0.0, 0, NAN, NAN
        for k in range(last, -1, -1):
            p = i - k
            rng = (h[p] - l[p]) if np.isnan(prev_close) else max(
                h[p] - l[p], abs(h[p] - prev_close), abs(l[p] - prev_close))
            atr = rng if seen == 0 else atr + (rng - atr) / 14.0
            seen += 1
            prev_close = c[p]
            atr_value = atr if seen >= 14 else NAN
            if c[p] > o[p] and not np.isnan(d_lo):
                broke = (not np.isnan(hi[p])) and c[p] > hi[p]
                big = (not np.isnan(atr_value)) and (c[p] - o[p]) >= disp * atr_value
                if broke or big:
                    b_lo, b_hi = d_lo, d_hi
            if c[p] < o[p] and not np.isnan(u_lo):
                broke = (not np.isnan(lo[p])) and c[p] < lo[p]
                big = (not np.isnan(atr_value)) and (o[p] - c[p]) >= disp * atr_value
                if broke or big:
                    s_lo, s_hi = u_lo, u_hi
            if not np.isnan(b_lo) and l[p] < b_lo:
                b_lo = b_hi = NAN
            if not np.isnan(s_hi) and h[p] > s_hi:
                s_lo = s_hi = NAN
            value = NAN
            if want_bull:
                if not np.isnan(b_lo):
                    value = (b_hi + b_lo) / 2.0
            elif not np.isnan(s_hi):
                value = (s_hi + s_lo) / 2.0
            if c[p] < o[p]:
                d_lo, d_hi = l[p], h[p]
            elif c[p] > o[p]:
                u_lo, u_hi = l[p], h[p]
        out.append(value)
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


# --- the SMC pack, at the default knob and at a family member ----------------

@pytest.mark.parametrize("n", [SWING_LOOKBACK, 5])
def test_pine_bos_matches_compute_feature(n):
    name = "bos" if n == SWING_LOOKBACK else f"bos_{n}"
    assert_parity(name, pine_bos(BARS, n), BARS, "pine", min_finite=0.99)


@pytest.mark.parametrize("n", [SWING_LOOKBACK, 5])
def test_mql5_bos_matches_compute_feature(n):
    name = "bos" if n == SWING_LOOKBACK else f"bos_{n}"
    assert_parity(name, mql5_bos(SMC, n), SMC, "mql5", min_finite=0.99)


@pytest.mark.parametrize("n", [SWING_LOOKBACK, 5])
def test_pine_mss_matches_compute_feature(n):
    name = "mss" if n == SWING_LOOKBACK else f"mss_{n}"
    assert_parity(name, pine_mss(BARS, n), BARS, "pine", min_finite=0.99)


@pytest.mark.parametrize("n", [SWING_LOOKBACK, 5])
def test_mql5_mss_matches_compute_feature(n):
    name = "mss" if n == SWING_LOOKBACK else f"mss_{n}"
    assert_parity(name, mql5_mss(SMC, n), SMC, "mql5", min_finite=0.99)


@pytest.mark.parametrize("run", [CISD_RUN, 3])
@pytest.mark.parametrize("target", ["pine", "mql5"])
def test_pine_cisd_matches_compute_feature(run, target):
    name = "cisd" if run == CISD_RUN else f"cisd_{run}"
    assert_parity(name, mirror_cisd(BARS, run), BARS, target, min_finite=0.99)


@pytest.mark.parametrize("run", [CISD_RUN, 3])
def test_mql5_cisd_walk_matches_compute_feature(run):
    name = "cisd" if run == CISD_RUN else f"cisd_{run}"
    assert_parity(name, mql5_cisd(SMC, run), SMC, "mql5", min_finite=0.99)


@pytest.mark.parametrize("n", [SWING_LOOKBACK, 5])
def test_pine_equilibrium_matches_compute_feature(n):
    name = "equilibrium" if n == SWING_LOOKBACK else f"equilibrium_{n}"
    assert_parity(name, pine_equilibrium(BARS, n), BARS, "pine")


@pytest.mark.parametrize("n", [SWING_LOOKBACK, 5])
def test_mql5_equilibrium_matches_compute_feature(n):
    name = "equilibrium" if n == SWING_LOOKBACK else f"equilibrium_{n}"
    assert_parity(name, mql5_equilibrium(SMC, n), SMC, "mql5")


def test_pine_fib_pos_matches_compute_feature():
    assert_parity("fib_pos", pine_fib_pos(BARS), BARS, "pine")


def test_mql5_fib_pos_matches_compute_feature():
    assert_parity("fib_pos", mql5_fib_pos(SMC), SMC, "mql5")


@pytest.mark.parametrize("bull", [True, False])
def test_pine_order_block_matches_compute_feature(bull):
    name = "ob_bull_mid" if bull else "ob_bear_mid"
    assert_parity(name, pine_order_block(BARS, bull), BARS, "pine", min_finite=0.1)


@pytest.mark.parametrize("bull", [True, False])
def test_mql5_order_block_matches_compute_feature(bull):
    name = "ob_bull_mid" if bull else "ob_bear_mid"
    assert_parity(name, mql5_order_block(SMC, bull), SMC, "mql5", min_finite=0.1)


def test_the_pine_atr_used_by_the_order_block_is_the_python_atr():
    """The displacement test is a threshold on atr_14, so the SMA-seeded ta.atr
    would arm the order block on different candles."""
    assert_parity("atr_14", pine_pop_atr(BARS), BARS, "pine")


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


def test_a_late_started_structural_walk_still_converges():
    """InpStructureBars is the EA's stand-in for the Python's dataset origin, and
    the order block is the longest-lived state in the pack (a zone survives until
    price sweeps it). Starting the walk 200 bars back already lands on the same
    number, so the 1000-bar default has real margin."""
    late = mql5_order_block(SMC, True, structure=200)
    full = compute_feature("ob_bull_mid", SMC).to_numpy(dtype=float)
    late = np.asarray(late, dtype=float)
    both_nan = np.isnan(late) & np.isnan(full)
    assert (both_nan | np.isclose(late, full, rtol=0.0, atol=1e-9))[300:].all()


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
    "bos": "bos > 0",
    "mss": "mss > 0",
    "cisd": "cisd > 0",
    "equilibrium": "close > equilibrium",
    "fib_pos": "fib_pos > 0.5",
    "ob_bull_mid": "close > ob_bull_mid",
    "ob_bear_mid": "close < ob_bear_mid",
    # the name FAMILIES: the knob is a suffix, exactly like ema_<n>
    "bos_20": "bos_20 > 0",
    "mss_7": "mss_7 > 0",
    "cisd_3": "cisd_3 > 0",
    "equilibrium_9": "close > equilibrium_9",
    "ob_bull_mid_4": "close > ob_bull_mid_4",
    "ob_bear_mid_4": "close < ob_bear_mid_4",
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
