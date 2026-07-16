"""Volume & order-flow-PROXY features (step-3 alpha expansion).

HONEST SCOPE: our data is OHLCV + tick_volume + spread — NOT true Level 2 / bid-ask
tick-delta. So "imbalance" here is a PROXY inferred from candle direction and where
price closes in its range, not real DOM order-flow. It's a legitimate confluence, but
a curve-fit to a fake orderflow signal is exactly what the gauntlet exists to catch —
so these are labelled proxies. If a broker DOM/tick-delta feed is wired later, the
feature NAMES stay and only the data source changes.

All PIT-correct: a completed bar's own OHLCV is known at its close (when signals fire),
and any baseline uses only PRIOR bars (rolling().shift(1)). tick_volume-free bars
degrade to neutral (1.0 / sign-only) so OHLC-only datasets still validate.
"""

import numpy as np
import pandas as pd

VOL_BASELINE = 20  # bars for the participation baseline


def _tick_volume(bars: pd.DataFrame) -> pd.Series:
    """tick_volume if present, else a flat 1.0 series (OHLC-only datasets)."""
    if "tick_volume" in bars.columns:
        return bars["tick_volume"].astype(float)
    return pd.Series(1.0, index=bars.index)


def rel_volume(bars: pd.DataFrame) -> pd.Series:
    """Participation: this bar's tick_volume / the mean of the PRIOR VOL_BASELINE bars.
    ~1 = average, >1 = a volume expansion (the useful continuous form; an alpha gates
    `rel_volume > p[vol_mult]`). Baseline shifted so a bar never inflates its own ratio."""
    v = _tick_volume(bars)
    base = v.rolling(VOL_BASELINE).mean().shift(1)
    return (v / base).replace([np.inf, -np.inf], np.nan)


def delta_proxy(bars: pd.DataFrame) -> pd.Series:
    """Signed-volume delta PROXY: sign(close-open) * rel_volume. A green bar on 2x
    volume ~ +2 (buy pressure), a red bar ~ negative. Approximates tick-delta from
    candle direction — the standard stand-in when true bid/ask volume is unavailable."""
    direction = np.sign(bars["close"] - bars["open"])
    return direction * rel_volume(bars)


def body_pressure(bars: pd.DataFrame) -> pd.Series:
    """Where the bar CLOSED within its range, signed: (close-open)/(high-low), in
    [-1, +1]. +1 = closed at the high (buyers won the bar), -1 = at the low. A pure
    intrabar imbalance proxy (own-bar only). Zero-range bars -> NaN (undefined)."""
    rng = (bars["high"] - bars["low"]).replace(0, np.nan)
    return (bars["close"] - bars["open"]) / rng
