"""Level-features (DSL v0.3) must be point-in-time correct: no level may change a
PAST value when future bars arrive (prefix-invariance). Plus a few value checks."""

import numpy as np
import pandas as pd

from popper.dsl.features.levels import (
    LEVEL_FEATURES,
    compute_level,
    prior_fractal_hi,
    prior_swing_wick_hi,
    resolve_level,
)


def _bars(n=900, seed=0) -> pd.DataFrame:
    """~3 days of synthetic M5 US30 bars with valid OHLC."""
    rng = np.random.default_rng(seed)
    times = pd.date_range("2025-01-06 00:00", periods=n, freq="5min", tz="UTC")
    price = 40000 + np.cumsum(rng.normal(0, 10, n))
    open_ = price + rng.uniform(-3, 3, n)
    close = price + rng.uniform(-3, 3, n)
    high = np.maximum.reduce([open_, close, price]) + rng.uniform(1, 6, n)
    low = np.minimum.reduce([open_, close, price]) - rng.uniform(1, 6, n)
    return pd.DataFrame({"time": times, "open": open_, "high": high, "low": low, "close": close})


def test_all_level_features_are_prefix_invariant():
    """The core guard: computing a level on bars[:k] must equal computing it on the
    full series and slicing to :k. A future-leaking level fails this."""
    bars = _bars()
    for name in LEVEL_FEATURES:
        full = compute_level(name, bars).to_numpy()
        for k in (200, 500, 750):
            pref = compute_level(name, bars.iloc[:k]).to_numpy()
            assert np.allclose(full[:k], pref, equal_nan=True), \
                f"{name} is not point-in-time correct (changed a past value at k={k})"


def test_swing_wick_hi_is_prior_window_max():
    bars = _bars()
    s = prior_swing_wick_hi(bars, n=20)
    t = 300
    expected = bars["high"].iloc[t - 20:t].max()  # prior 20 bars, excludes bar t
    assert np.isclose(s.iloc[t], expected)
    assert np.isnan(s.iloc[0])  # not formed yet


def test_fractal_hi_confirms_with_lag_and_no_lookahead():
    """A clean isolated peak must be published exactly 2 bars after it prints,
    never on or before the peak bar."""
    n = 40
    high = np.full(n, 100.0)
    low = np.full(n, 90.0)
    high[20] = 130.0  # a lone peak with 2 lower bars each side
    bars = pd.DataFrame({
        "time": pd.date_range("2025-01-06", periods=n, freq="5min", tz="UTC"),
        "open": (high + low) / 2, "close": (high + low) / 2, "high": high, "low": low,
    })
    f = prior_fractal_hi(bars)
    assert np.isnan(f.iloc[20]) or f.iloc[20] != 130.0  # not known AT the peak
    assert f.iloc[21] != 130.0                          # nor 1 bar later
    assert np.isclose(f.iloc[22], 130.0)                # confirmed at i+2
    assert np.isclose(f.iloc[30], 130.0)                # persists (ffill)


def test_resolve_unknown_level_is_none():
    assert resolve_level("not_a_level") is None
    assert "bb_line" in LEVEL_FEATURES and "prior_fractal_lo" in LEVEL_FEATURES
