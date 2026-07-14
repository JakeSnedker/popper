"""Feature library — the ONLY names an alpha may reference (spec/01).

Every feature is a pure function `bars -> pd.Series` aligned to bars.index and
POINT-IN-TIME CORRECT: the value at bar i uses only bars 0..i (a completed
bar's own OHLC is known at its close, which is when signals are evaluated;
entries fill at the NEXT bar's open — see compiler). tests/test_dsl.py has a
prefix-invariance check that fails if any feature leaks the future.

`bars` needs columns: time (datetime), open, high, low, close.
"""

import re

import numpy as np
import pandas as pd

# DECISION: session hours defined on the bars' `time` column, which the MT5
# export tags UTC. Asia 00-07, London 07-13, NY 13-21 (covers the cash open).
SESSIONS: dict[str, tuple[int, int]] = {"asia": (0, 7), "london": (7, 13), "ny": (13, 21)}

OPENING_RANGE_BARS = 6  # first 30 minutes on M5


def _time(bars: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(bars["time"]) if "time" in bars.columns else pd.DatetimeIndex(bars.index)


def _true_range(bars: pd.DataFrame) -> pd.Series:
    prev_close = bars["close"].shift(1)
    return pd.concat(
        [bars["high"] - bars["low"], (bars["high"] - prev_close).abs(), (bars["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr_14(bars: pd.DataFrame) -> pd.Series:
    """Wilder ATR, period 14."""
    return _true_range(bars).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()


def ema(bars: pd.DataFrame, n: int) -> pd.Series:
    return bars["close"].ewm(span=n, min_periods=n, adjust=False).mean()


def _session(bars: pd.DataFrame, name: str) -> pd.Series:
    start, end = SESSIONS[name]
    hours = _time(bars).hour
    return pd.Series(((hours >= start) & (hours < end)).astype(float), index=bars.index)


def session_ny(bars: pd.DataFrame) -> pd.Series:
    return _session(bars, "ny")


def session_london(bars: pd.DataFrame) -> pd.Series:
    return _session(bars, "london")


def session_asia(bars: pd.DataFrame) -> pd.Series:
    return _session(bars, "asia")


def opening_range(bars: pd.DataFrame) -> pd.Series:
    """High-low range of the first OPENING_RANGE_BARS bars of the NY session.

    DECISION: anchored to the NY (cash) open — that's the "opening drive"
    the JJC vocabulary means, not the UTC day rollover. NaN until the range
    has fully formed (no peeking), then constant for the rest of the day.
    """
    day = _time(bars).date
    in_ny = _session(bars, "ny").astype(bool)
    pos_ny = in_ny.groupby(day).cumsum() - 1  # 0-based position within the NY session
    in_or = in_ny & (pos_ny < OPENING_RANGE_BARS)
    or_high = bars["high"].where(in_or).groupby(day).cummax()
    or_low = bars["low"].where(in_or).groupby(day).cummin()
    rng = (or_high - or_low).groupby(day).ffill()
    return rng.where(pos_ny >= OPENING_RANGE_BARS - 1)


def rsi_fast(bars: pd.DataFrame) -> pd.Series:
    """Wilder RSI, period 7."""
    delta = bars["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 7, min_periods=7, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 7, min_periods=7, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def htf_trend(bars: pd.DataFrame) -> pd.Series:
    # DECISION: higher-timeframe trend proxied on the native series as
    # sign(EMA48 - EMA240) (~4h vs ~20h on M5). Avoids resample edge cases;
    # upgrade to true HTF resampling when an alpha needs exact H1 bars.
    return np.sign(ema(bars, 48) - ema(bars, 240))


def bb_line(bars: pd.DataFrame) -> pd.Series:
    # DECISION: "bb_line" = the Bollinger middle line (SMA 20).
    return bars["close"].rolling(20, min_periods=20).mean()


def psar(bars: pd.DataFrame) -> pd.Series:
    """Parabolic SAR (af 0.02, step 0.02, max 0.2). Forward-only loop."""
    high, low = bars["high"].to_numpy(), bars["low"].to_numpy()
    n = len(bars)
    out = np.full(n, np.nan)
    if n < 2:
        return pd.Series(out, index=bars.index)
    uptrend, af, af_step, af_max = True, 0.02, 0.02, 0.2
    sar, ep = low[0], high[0]
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if uptrend:
            sar = min(sar, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < sar:  # flip to downtrend
                uptrend, sar, ep, af = False, ep, low[i], 0.02
            elif high[i] > ep:
                ep, af = high[i], min(af + af_step, af_max)
        else:
            sar = max(sar, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > sar:  # flip to uptrend
                uptrend, sar, ep, af = True, ep, high[i], 0.02
            elif low[i] < ep:
                ep, af = low[i], min(af + af_step, af_max)
        out[i] = sar
    return pd.Series(out, index=bars.index)


def liquidity_sweep(bars: pd.DataFrame) -> pd.Series:
    """+1 bullish sweep (low pierces prior 20-bar low, close reclaims it),
    -1 bearish sweep (mirror), else 0."""
    prior_low = bars["low"].rolling(20).min().shift(1)
    prior_high = bars["high"].rolling(20).max().shift(1)
    bull = (bars["low"] < prior_low) & (bars["close"] > prior_low)
    bear = (bars["high"] > prior_high) & (bars["close"] < prior_high)
    return bull.astype(float) - bear.astype(float)


def candle_size(bars: pd.DataFrame) -> pd.Series:
    return bars["high"] - bars["low"]


# ================================================================= SMC PACK
# Smart-Money-Concepts primitives. Admission rubric + admit/reject ledger:
# docs/spec/FEATURE_ADMISSION.md. Every feature here is forward-only (no
# look-ahead): structural pivots are CONFIRMED `swing_lookback` bars AFTER they
# form and are invisible before confirmation. The tunable knobs are named
# module constants (defaults) and are also exposable per-spec via name families
# (bos_<n>, mss_<n>, equilibrium_<n>, ob_bull_mid_<n>, ob_bear_mid_<n>, cisd_<n>)
# mirroring ema_<n> — this is also how a "supply/demand zone" is expressed:
# order_block at a larger structural lookback (a PARAMETERIZATION, not a new
# feature). True HTF resampling stays deferred, consistent with htf_trend.

SWING_LOOKBACK = 2   # fractal half-width (bars each side); ICT default
CISD_RUN = 1         # min consecutive same-direction closes before a delivery flip counts
OB_DISP_MULT = 1.0   # displacement threshold in ATRs for an order block


def _swing_pivots(bars: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> tuple[pd.Series, pd.Series]:
    """(confirmed_swing_high, confirmed_swing_low) price at each bar — SHARED by
    bos/mss/equilibrium/order_block (do NOT duplicate pivot logic).

    A strict fractal high at centre k becomes visible only at bar k+lookback
    (confirmation delay = no look-ahead), then is carried forward until the next
    pivot confirms. Forward-only, so prefix-invariant.
    """
    hi, lo = bars["high"].to_numpy(), bars["low"].to_numpy()
    n = len(hi)
    ch, cl = np.full(n, np.nan), np.full(n, np.nan)
    last_h = last_l = np.nan
    for i in range(n):
        j = i - lookback  # a centre becomes fully surrounded (and thus confirmable) now
        if lookback <= j <= n - 1 - lookback:
            wh, wl = hi[j - lookback:j + lookback + 1], lo[j - lookback:j + lookback + 1]
            if (wh[:lookback] < hi[j]).all() and (wh[lookback + 1:] < hi[j]).all():
                last_h = hi[j]
            if (wl[:lookback] > lo[j]).all() and (wl[lookback + 1:] > lo[j]).all():
                last_l = lo[j]
        ch[i], cl[i] = last_h, last_l
    return pd.Series(ch, index=bars.index), pd.Series(cl, index=bars.index)


def fvg(bars: pd.DataFrame) -> pd.Series:
    """Fair Value Gap PRESENCE: +1 bullish 3-bar gap (high[i-2] < low[i]),
    -1 bearish (low[i-2] > high[i]), else 0.

    DECISION: expose presence (the form most useful for signal gating). The
    rejected 'imbalance' feature folds in here — gap *magnitude* is the trivial
    derivation `low - high[2]` (bull) and is not a separate primitive.
    """
    h, l = bars["high"], bars["low"]
    return (h.shift(2) < l).astype(float) - (l.shift(2) > h).astype(float)


def bos(bars: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> pd.Series:
    """Break of Structure: +1 when close crosses above the most recent CONFIRMED
    swing high, -1 below the confirmed swing low, else 0 (a fresh-break event)."""
    ch, cl = _swing_pivots(bars, lookback)
    c = bars["close"].to_numpy()
    chn, cln = ch.to_numpy(), cl.to_numpy()
    out = np.zeros(len(c))
    for i in range(1, len(c)):
        up_now = not np.isnan(chn[i]) and c[i] > chn[i]
        up_prev = not np.isnan(chn[i - 1]) and c[i - 1] > chn[i - 1]
        dn_now = not np.isnan(cln[i]) and c[i] < cln[i]
        dn_prev = not np.isnan(cln[i - 1]) and c[i - 1] < cln[i - 1]
        if up_now and not up_prev:
            out[i] = 1.0
        elif dn_now and not dn_prev:
            out[i] = -1.0
    return pd.Series(out, index=bars.index)


def mss(bars: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> pd.Series:
    """Market Structure Shift: the first BOS whose direction OPPOSES the previous
    BOS (a structural flip). +1/-1 on the flip bar, else 0. Built on bos."""
    b = bos(bars, lookback).to_numpy()
    out = np.zeros(len(b))
    prev = 0.0
    for i in range(len(b)):
        if b[i] != 0:
            if prev != 0 and b[i] != prev:
                out[i] = b[i]
            prev = b[i]
    return pd.Series(out, index=bars.index)


def cisd(bars: pd.DataFrame, run: int = CISD_RUN) -> pd.Series:
    """Change in State of Delivery: after a run of >= `run` consecutive
    same-direction closes, +1 when a candle closes beyond the OPEN of the first
    candle of a down-run (delivery flips buyside), -1 mirror, else 0."""
    o, c = bars["open"].to_numpy(), bars["close"].to_numpy()
    out = np.zeros(len(c))
    run_dir = run_len = 0
    run_open = arm_dn = arm_up = np.nan
    for i in range(len(c)):
        if not np.isnan(arm_dn) and c[i] > arm_dn:
            out[i], arm_dn = 1.0, np.nan
        elif not np.isnan(arm_up) and c[i] < arm_up:
            out[i], arm_up = -1.0, np.nan
        d = 1 if c[i] > o[i] else (-1 if c[i] < o[i] else 0)
        if d != 0 and d == run_dir:
            run_len += 1
        elif d != 0:
            run_dir, run_len, run_open = d, 1, o[i]
        if run_dir == -1 and run_len >= run:
            arm_dn = run_open
        elif run_dir == 1 and run_len >= run:
            arm_up = run_open
    return pd.Series(out, index=bars.index)


def equilibrium(bars: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> pd.Series:
    """50% of the current dealing range = midpoint of the most recent confirmed
    swing high and swing low (premium above / discount below)."""
    ch, cl = _swing_pivots(bars, lookback)
    return (ch + cl) / 2.0


def _order_blocks(bars: pd.DataFrame, lookback: int = SWING_LOOKBACK,
                  disp_mult: float = OB_DISP_MULT) -> tuple[pd.Series, pd.Series]:
    """(bull_mid, bear_mid) = 50% level of the nearest UNMITIGATED order block.

    Bullish OB = the last down-close candle before an up-move that DISPLACES
    (breaks the prior confirmed swing high OR the candle body >= disp_mult*ATR);
    bearish mirror. NaN when none active; cleared on full mitigation (price fully
    sweeps back through the zone). Forward-only, so prefix-invariant.
    """
    o, h, l, c = (bars[x].to_numpy() for x in ("open", "high", "low", "close"))
    ch, cl = _swing_pivots(bars, lookback)
    chn, cln = ch.to_numpy(), cl.to_numpy()
    atr = atr_14(bars).to_numpy()
    n = len(c)
    bull_mid, bear_mid = np.full(n, np.nan), np.full(n, np.nan)
    b_lo = b_hi = s_lo = s_hi = np.nan          # active bull/bear OB extents
    ld_lo = ld_hi = lu_lo = lu_hi = np.nan      # last down-/up-close candle extents (before i)
    for i in range(n):
        a = atr[i]
        if c[i] > o[i] and not np.isnan(ld_lo):
            broke = not np.isnan(chn[i]) and c[i] > chn[i]
            big = not np.isnan(a) and (c[i] - o[i]) >= disp_mult * a
            if broke or big:
                b_lo, b_hi = ld_lo, ld_hi
        if c[i] < o[i] and not np.isnan(lu_lo):
            broke = not np.isnan(cln[i]) and c[i] < cln[i]
            big = not np.isnan(a) and (o[i] - c[i]) >= disp_mult * a
            if broke or big:
                s_lo, s_hi = lu_lo, lu_hi
        if not np.isnan(b_lo) and l[i] < b_lo:  # full mitigation of bullish demand
            b_lo = b_hi = np.nan
        if not np.isnan(s_hi) and h[i] > s_hi:  # full mitigation of bearish supply
            s_lo = s_hi = np.nan
        if not np.isnan(b_lo):
            bull_mid[i] = (b_hi + b_lo) / 2.0
        if not np.isnan(s_hi):
            bear_mid[i] = (s_hi + s_lo) / 2.0
        if c[i] < o[i]:
            ld_lo, ld_hi = l[i], h[i]
        elif c[i] > o[i]:
            lu_lo, lu_hi = l[i], h[i]
    return pd.Series(bull_mid, index=bars.index), pd.Series(bear_mid, index=bars.index)


def ob_bull_mid(bars: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> pd.Series:
    return _order_blocks(bars, lookback)[0]


def ob_bear_mid(bars: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> pd.Series:
    return _order_blocks(bars, lookback)[1]


# ================================================================ REGIME SCORE
def regime_score(bars: pd.DataFrame) -> pd.Series:
    """Signed regime belief in [-1, +1] — the generalized JJC trade score.

    Mean of directional STATE votes: +1 = every read bullish (strong up-trend),
    -1 = every read bearish, ~0 = mixed (consolidation). Votes: HTF trend, price
    vs EMA50, EMA20-vs-EMA50 stack, RSI-vs-50 momentum, price-vs-PSAR side. Each
    vote is causal and pointwise, so the average is prefix-invariant. Consumers
    threshold it in a signal, e.g. `regime_score > p[bull]`.

    ponytail: fixed 5-vote STATE ensemble (event features are ~0 most bars, so
    they don't belong in a persistent score). Weights/constituents are the v2
    knob; a CUSUM shift-detector layers on top when detection *speed* matters.
    """
    c = bars["close"]
    votes = [
        htf_trend(bars),                            # HTF trend (already signed)
        np.sign(c - ema(bars, 50)),                 # price vs medium trend
        np.sign(ema(bars, 20) - ema(bars, 50)),     # fast/medium EMA stack
        np.sign(rsi_fast(bars) - 50.0),             # momentum bias
        np.sign(c - psar(bars)),                    # PSAR trend side
    ]
    return pd.concat([v.fillna(0.0) for v in votes], axis=1).mean(axis=1)


# ========================================================= REGIME-SHIFT (CUSUM)
CUSUM_VOL_N = 50     # rolling window for return standardization
CUSUM_SLACK = 0.5    # k: drift allowance (sigmas) before evidence accrues


def cusum_shift(bars: pd.DataFrame, vol_n: int = CUSUM_VOL_N,
                slack: float = CUSUM_SLACK) -> pd.Series:
    """Signed sequential change-point (CUSUM) evidence of a regime shift.

    z = standardized bar return; two one-sided CUSUM accumulators (floored at 0)
    build directional evidence and self-reset in chop. Output S+ - S- rises fast
    when a real drift emerges (regime *shift*), ~0 in consolidation. A signal
    thresholds it, e.g. `cusum_shift > p[h]`: sweeping h explores the detection
    speed / false-alarm frontier (quickest-change detection theory). This is the
    FAST-detection complement to regime_score's persistent STATE read.

    Forward recursion over causal inputs -> prefix-invariant (no look-ahead).
    ponytail: CUSUM on returns = the universal drift-shift detector; point it at
    another series (e.g. regime_score) in a v3 if a specific regime needs it.
    """
    ret = bars["close"].diff()
    vol = ret.rolling(vol_n, min_periods=vol_n).std()
    z = (ret / vol).where(vol > 0).fillna(0.0).to_numpy()
    out = np.zeros(len(z))
    sp = sn = 0.0
    for i in range(len(z)):
        sp = max(0.0, sp + z[i] - slack)
        sn = max(0.0, sn - z[i] - slack)
        out[i] = sp - sn
    return pd.Series(out, index=bars.index)


# imported here (not at top) because regime.py needs atr_14/ema from this
# module — same partial-init pattern levels.py uses.
from popper.dsl.features.regime import trend_regime, vol_regime  # noqa: E402

_EXACT = {
    "atr_14": atr_14,
    "session_ny": session_ny,
    "session_london": session_london,
    "session_asia": session_asia,
    "opening_range": opening_range,
    "rsi_fast": rsi_fast,
    "htf_trend": htf_trend,
    "bb_line": bb_line,
    "psar": psar,
    "liquidity_sweep": liquidity_sweep,
    "candle_size": candle_size,
    # SMC pack (docs/spec/FEATURE_ADMISSION.md)
    "fvg": fvg,
    "bos": bos,
    "mss": mss,
    "cisd": cisd,
    "equilibrium": equilibrium,
    "ob_bull_mid": ob_bull_mid,
    "ob_bear_mid": ob_bear_mid,
    # regime — signed belief score (generalized JJC trade score)
    "regime_score": regime_score,
    # regime-shift — fast CUSUM change-point evidence (threshold via p[h])
    "cusum_shift": cusum_shift,
    # regime labels (features/regime.py): numeric codes, e.g. `vol_regime == 1`
    "vol_regime": vol_regime,       # -1 low_vol / 0 normal / +1 high_vol
    "trend_regime": trend_regime,   # 1 trending / 0 ranging
}

_EMA_RE = re.compile(r"^ema_(\d+)$")

# name families that carry one tunable knob as a suffix (like ema_<n>):
# swing_lookback for structural features, run length for cisd.
_SMC_BASE = {"bos": bos, "mss": mss, "equilibrium": equilibrium,
             "ob_bull_mid": ob_bull_mid, "ob_bear_mid": ob_bear_mid, "cisd": cisd}
_SMC_RE = re.compile(r"^(bos|mss|equilibrium|ob_bull_mid|ob_bear_mid|cisd)_(\d+)$")


# the exact feature names an alpha may reference (plus the ema_<n> family below)
FEATURES = tuple(_EXACT)


def resolve_feature(name: str):
    """Feature name -> callable(bars) -> Series, or None if unknown."""
    if name in _EXACT:
        return _EXACT[name]
    m = _EMA_RE.match(name)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 1000:
            return lambda bars: ema(bars, n)
    m = _SMC_RE.match(name)
    if m:
        base, n = m.group(1), int(m.group(2))
        if 1 <= n <= 100:
            return lambda bars: _SMC_BASE[base](bars, n)
    return None


def compute_feature(name: str, bars: pd.DataFrame) -> pd.Series:
    fn = resolve_feature(name)
    if fn is None:
        raise KeyError(f"unknown feature: {name}")
    return fn(bars)
