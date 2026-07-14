"""SMC feature pack: prefix-invariance (no look-ahead) + behavioural sanity.

The CORRECTNESS BAR: every SMC feature (default names AND tuned name-family
variants) must give the SAME value at bar i whether or not bars after i exist.
Mirrors tests/test_dsl.py::test_all_features_point_in_time_correct.
"""

import numpy as np
import pandas as pd
import pytest

from popper.dsl.features import compute_feature, resolve_feature

SMC = ["fvg", "bos", "mss", "cisd", "equilibrium", "ob_bull_mid", "ob_bear_mid"]
# tuned variants: swing_lookback / run encoded in the name (like ema_<n>)
SMC_VARIANTS = ["bos_3", "mss_3", "equilibrium_4", "ob_bull_mid_5", "ob_bear_mid_5", "cisd_2"]


def synth_bars(n: int = 2500, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 40000 + np.linspace(0, 120, n) + rng.normal(0, 8, n).cumsum()
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    wick = np.abs(rng.normal(0, 5, n)) + 1
    return pd.DataFrame({
        "time": pd.date_range("2025-01-06", periods=n, freq="5min", tz="UTC"),
        "open": open_,
        "high": np.maximum(open_, close) + wick,
        "low": np.minimum(open_, close) - wick,
        "close": close,
    })


@pytest.mark.parametrize("name", SMC + SMC_VARIANTS)
def test_smc_point_in_time_correct(name):
    """Value at bar i identical with or without future bars — the one thing we refuse."""
    assert resolve_feature(name) is not None, name
    bars = synth_bars()
    full = compute_feature(name, bars)
    prefix = compute_feature(name, bars.iloc[:1500].copy())
    pd.testing.assert_series_equal(prefix, full.iloc[:1500], check_names=False, obj=name)


def test_smc_variants_differ_from_defaults():
    """A tuned knob actually changes the series (not silently ignored)."""
    bars = synth_bars()
    assert not compute_feature("bos", bars).equals(compute_feature("bos_3", bars))


# --- behavioural sanity on hand-made bars (lookback=2 fractals) ---

def _bars(rows):
    o, h, l, c = zip(*rows)
    n = len(rows)
    return pd.DataFrame({
        "time": pd.date_range("2025-01-06", periods=n, freq="5min", tz="UTC"),
        "open": o, "high": h, "low": l, "close": c,
    })


def test_fvg_presence_and_mirror():
    # bullish 3-bar gap: high[0]=101 < low[2]=103
    rows = [(100, 101, 99, 100), (100, 105, 100, 104), (103, 106, 103, 105)]
    assert compute_feature("fvg", _bars(rows)).iloc[2] == 1.0
    # bearish mirror: low[0]=99 > high[2]=97
    rows = [(100, 100, 99, 99.5), (98, 99, 95, 96), (96, 97, 94, 95)]
    assert compute_feature("fvg", _bars(rows)).iloc[2] == -1.0


def test_bos_fires_on_break_of_confirmed_swing_high():
    # a clean fractal high at index 2 (99->100->[102]->100->99), confirmed at
    # index 4, then a close above 102 at index 6 => BOS +1.
    rows = [
        (99, 99.5, 98, 99),      # 0
        (99, 100.5, 98.5, 100),  # 1
        (100, 102, 99.5, 101),   # 2  <- fractal high (102)
        (101, 100.5, 99, 100),   # 3
        (100, 100, 98.5, 99),    # 4  swing high confirmed here
        (99, 101, 98, 100),      # 5
        (100, 103.5, 99.5, 103), # 6  <- close 103 > 102 => BOS up
    ]
    b = compute_feature("bos", _bars(rows))
    assert b.iloc[6] == 1.0
    assert (b.iloc[:6] == 1.0).sum() == 0  # no earlier phantom break


if __name__ == "__main__":
    for nm in SMC + SMC_VARIANTS:
        test_smc_point_in_time_correct(nm)
    test_smc_variants_differ_from_defaults()
    test_fvg_presence_and_mirror()
    test_bos_fires_on_break_of_confirmed_swing_high()
    print(f"OK: {len(SMC + SMC_VARIANTS)} SMC features prefix-invariant + behaviour sane")
