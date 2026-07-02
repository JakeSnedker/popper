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
}

_EMA_RE = re.compile(r"^ema_(\d+)$")


def resolve_feature(name: str):
    """Feature name -> callable(bars) -> Series, or None if unknown."""
    if name in _EXACT:
        return _EXACT[name]
    m = _EMA_RE.match(name)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 1000:
            return lambda bars: ema(bars, n)
    return None


def compute_feature(name: str, bars: pd.DataFrame) -> pd.Series:
    fn = resolve_feature(name)
    if fn is None:
        raise KeyError(f"unknown feature: {name}")
    return fn(bars)
