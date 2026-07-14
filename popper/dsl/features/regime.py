"""Regime labels — causal, point-in-time-correct per-bar market-state features.

Two INDEPENDENT labels so later phases can score edges per-regime:

  vol_regime   -1 = low_vol, 0 = normal, +1 = high_vol
               ATR(14) percentile-ranked against a TRAILING window of its own
               history (window includes the current bar, never a future one).
  trend_regime  1 = trending, 0 = ranging
               |EMA48 - EMA240| / ATR(14) — the same EMA pair htf_trend already
               uses for direction; this is its normalized MAGNITUDE. Combine
               with htf_trend in a signal when the trend's sign matters.

DSL note: the expression grammar (dsl/expr.py) only allows numeric constants,
so the registered features are numeric codes (`vol_regime == 1`). The string
labels live in VOL_LABELS / TREND_LABELS and the *_label helpers for reporting.

Same non-negotiable rule as every feature: the value at bar i uses only bars
0..i. Rolling windows are trailing; NaN until the window has fully formed.
"""

import numpy as np
import pandas as pd

from popper.dsl.features import atr_14, ema

# CALIBRATION KNOB: trailing bars for the ATR percentile rank (~2 trading days
# on M5). Shorter = faster regime flips, longer = stabler. Needs market tuning.
VOL_RANK_WINDOW = 500
# CALIBRATION KNOB: percentile cuts for high/low vol. Needs market tuning.
VOL_HIGH_PCT = 0.70
VOL_LOW_PCT = 0.30
# CALIBRATION KNOB: EMA pair for trend separation (matches htf_trend's proxy).
TREND_EMA_FAST = 48
TREND_EMA_SLOW = 240
# CALIBRATION KNOB: min |ema_fast - ema_slow| in ATRs to call it trending.
TREND_SEP_ATR = 0.5

LOW_VOL, NORMAL, HIGH_VOL = -1.0, 0.0, 1.0
RANGING, TRENDING = 0.0, 1.0
VOL_LABELS = {LOW_VOL: "low_vol", NORMAL: "normal", HIGH_VOL: "high_vol"}
TREND_LABELS = {RANGING: "ranging", TRENDING: "trending"}


def vol_regime(bars: pd.DataFrame, window: int = VOL_RANK_WINDOW,
               high_pct: float = VOL_HIGH_PCT, low_pct: float = VOL_LOW_PCT) -> pd.Series:
    """-1/0/+1 volatility regime from ATR(14) rank vs its trailing window.

    rolling(...).rank(pct=True) ranks the CURRENT bar's ATR within the trailing
    `window` values ending at this bar — strictly causal. NaN until formed.
    """
    rank = atr_14(bars).rolling(window, min_periods=window).rank(pct=True)
    codes = np.where(rank >= high_pct, HIGH_VOL, np.where(rank <= low_pct, LOW_VOL, NORMAL))
    return pd.Series(codes, index=bars.index).where(rank.notna())


def trend_regime(bars: pd.DataFrame, sep_atr: float = TREND_SEP_ATR) -> pd.Series:
    """1 = trending, 0 = ranging: normalized EMA separation vs threshold.

    Every input (EMAs, ATR) is causal and pointwise, so this is prefix-invariant.
    NaN until the slow EMA and ATR have formed.
    """
    sep = (ema(bars, TREND_EMA_FAST) - ema(bars, TREND_EMA_SLOW)).abs() / atr_14(bars)
    return (sep >= sep_atr).astype(float).where(sep.notna())


def vol_regime_label(bars: pd.DataFrame) -> pd.Series:
    """String labels {low_vol, normal, high_vol} (NaN while unformed)."""
    return vol_regime(bars).map(VOL_LABELS)


def trend_regime_label(bars: pd.DataFrame) -> pd.Series:
    """String labels {trending, ranging} (NaN while unformed)."""
    return trend_regime(bars).map(TREND_LABELS)
