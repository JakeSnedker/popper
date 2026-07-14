"""Regime labels (features/regime.py): fire on obvious synthetic regimes AND
are point-in-time correct (same prefix-invariance style as test_dsl/test_levels)."""

import numpy as np
import pandas as pd

from popper.dsl.features import compute_feature
from popper.dsl.features.regime import (
    HIGH_VOL, LOW_VOL, RANGING, TRENDING,
    trend_regime, trend_regime_label, vol_regime, vol_regime_label,
)


def _bars(close: np.ndarray, wick: np.ndarray) -> pd.DataFrame:
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame({
        "time": pd.date_range("2025-01-06", periods=len(close), freq="5min", tz="UTC"),
        "open": open_,
        "high": np.maximum(open_, close) + wick,
        "low": np.minimum(open_, close) - wick,
        "close": close,
    })


def vol_burst_bars() -> pd.DataFrame:
    """900 normal bars, a 100-bar high-vol burst, then 500 extra-quiet bars."""
    rng = np.random.default_rng(3)
    n = 1500
    noise = np.full(n, 2.0)
    noise[900:1000] = 25.0  # the burst
    noise[1000:] = 0.4      # the lull
    close = 40000 + (rng.normal(0, 1, n) * noise).cumsum()
    wick = np.abs(rng.normal(0, 1, n)) * noise + 0.5
    return _bars(close, wick)


def trend_leg_bars() -> pd.DataFrame:
    """500 flat noisy bars, then a 500-bar steady up-leg."""
    rng = np.random.default_rng(5)
    n = 1000
    drift = np.zeros(n)
    drift[500:] = 2.0  # the trend leg
    close = 40000 + drift.cumsum() + rng.normal(0, 0.5, n)
    wick = np.abs(rng.normal(0, 0.5, n)) + 0.3
    return _bars(close, wick)


def test_vol_regime_fires_on_burst_and_calm():
    bars = vol_burst_bars()
    v = vol_regime(bars)
    # inside the burst (after ATR14 catches up) -> high_vol
    assert (v.iloc[950:1000] == HIGH_VOL).all()
    # in the post-burst lull, current ATR ranks at the bottom of the trailing
    # window (which still holds normal + burst bars) -> low_vol
    assert (v.iloc[1150:1250] == LOW_VOL).all()
    # unformed prefix stays NaN (no fabricated labels)
    assert v.iloc[:400].isna().all()
    assert set(vol_regime_label(bars).dropna().unique()) <= {"low_vol", "normal", "high_vol"}


def test_trend_regime_fires_on_leg_and_chop():
    bars = trend_leg_bars()
    t = trend_regime(bars)
    # flat chop (once EMAs formed) -> ranging
    assert (t.iloc[300:500] == RANGING).all()
    # deep into the drift leg -> trending
    assert (t.iloc[800:1000] == TRENDING).all()
    labels = trend_regime_label(bars)
    assert labels.iloc[900] == "trending" and labels.iloc[400] == "ranging"


def test_regime_point_in_time_correct():
    # truncating future bars must not change earlier labels (prefix invariance)
    for bars in (vol_burst_bars(), trend_leg_bars()):
        for name in ("vol_regime", "trend_regime"):
            full = compute_feature(name, bars)
            prefix = compute_feature(name, bars.iloc[:1100].copy())
            pd.testing.assert_series_equal(prefix, full.iloc[:1100], check_names=False, obj=name)


def test_regime_registered_in_feature_registry():
    from popper.dsl.features import FEATURES
    assert "vol_regime" in FEATURES and "trend_regime" in FEATURES
