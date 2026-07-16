"""Step-3 alpha features: Fibonacci position + volume/order-flow proxies.
Semantics only (prefix-invariance / no-look-ahead covered by test_dsl)."""
import numpy as np
import pandas as pd

from popper.dsl.features import compute_feature


def _bars(o, h, l, c, v=None):
    n = len(c)
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC"),
        "open": np.asarray(o, float), "high": np.asarray(h, float),
        "low": np.asarray(l, float), "close": np.asarray(c, float),
        **({"tick_volume": v} if v is not None else {})})


def test_body_pressure_bounds_and_sign():
    # bar0 opens at low & closes at high -> +1; bar1 closes below open -> negative
    b = _bars([0.5, 2.0], [2.0, 2.5], [0.5, 1.0], [2.0, 1.0])
    bp = compute_feature("body_pressure", b)
    assert bp.iloc[0] == 1.0
    assert bp.iloc[1] < 0
    assert bp.dropna().between(-1.0, 1.0).all()


def test_delta_proxy_follows_direction():
    n = 25  # need >VOL_BASELINE(20) for rel_volume to warm up
    o = np.full(n, 10.0); c = o.copy()
    c[-2] = 11.0  # green bar
    c[-1] = 9.0   # red bar
    b = _bars(o, np.maximum(o, c) + 1, np.minimum(o, c) - 1, c, v=[100] * n)
    d = compute_feature("delta_proxy", b)
    assert d.iloc[-2] > 0 and d.iloc[-1] < 0


def test_rel_volume_expands_on_surge_and_neutral_without_volume():
    n = 30
    vol = [100] * (n - 1) + [300]           # last bar 3x participation
    c = np.linspace(100, 110, n)
    rv = compute_feature("rel_volume", _bars(c, c + 1, c - 1, c, v=vol))
    assert rv.iloc[-1] > 2.5                 # surge detected
    rv0 = compute_feature("rel_volume", _bars(c, c + 1, c - 1, c))  # no tick_volume
    assert abs(rv0.dropna().iloc[-1] - 1.0) < 1e-9                  # flat 1.0 fallback


def test_fib_pos_sane_domain():
    # sawtooth: isolated single-bar peaks (14) & troughs (10) so fractal pivots confirm
    c = np.tile([10.0, 12.0, 14.0, 12.0], 8)  # 32 bars
    f = compute_feature("fib_pos", _bars(c, c + 0.5, c - 0.5, c)).dropna()
    assert not f.empty and f.between(-5, 5).all()
