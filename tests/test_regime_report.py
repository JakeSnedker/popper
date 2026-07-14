"""Phase C2: per-regime trade bucketing (popper/falsify/regime_report.py).

Synthetic bars: oscillating/ranging phase A, high-vol block phase B, trend leg
phase C. Trades entered at known bars must land in the matching regime bucket,
with metrics matching runner.compute_metrics definitions.
"""

import math

import numpy as np
import pandas as pd
import pytest

from popper.dsl.features.regime import trend_regime, vol_regime
from popper.falsify.regime_report import _concentration, regime_report

N = 1400
HV_START, HV_END = 900, 1100      # high-vol block (wide bars)
TREND_START = 1100                # trend leg (0.5/bar drift)


@pytest.fixture(scope="module")
def bars():
    i = np.arange(N)
    close = 100 + 0.5 * np.sin(i / 3.0)                     # ranging oscillation
    close[TREND_START:] = 100 + 0.5 * (i[TREND_START:] - TREND_START + 1)
    half = np.where((i >= HV_START) & (i < HV_END), 2.5, 0.5)  # wide bars = high vol
    return pd.DataFrame({
        "open": close, "high": close + half, "low": close - half, "close": close,
    })


def test_engineered_regimes(bars):
    # sanity: the entry bars used below really carry the labels we claim
    vol = vol_regime(bars).to_numpy()
    trend = trend_regime(bars).to_numpy()
    assert np.isnan(vol[5]) and np.isnan(trend[5])           # unformed -> unknown
    assert all(vol[i] == 1.0 for i in (960, 1000, 1040))     # high-vol block
    assert all(trend[i] == 0.0 for i in (700, 750, 960, 1000, 1040))  # ranging
    assert all(trend[i] == 1.0 for i in (1300, 1380))        # trend leg


def test_bucketing_by_entry_regime(bars):
    trades = [
        # 3 entries in the high-vol block: R = +2, +1, -1
        {"entry_i": 960, "pnl_r": 2.0},
        {"entry_i": 1000, "pnl_r": 1.0},
        {"entry_i": 1040, "pnl_r": -1.0},
        # 2 entries in the trend leg: R = +1, +1
        {"entry_i": 1300, "pnl_r": 1.0},
        {"entry_i": 1380, "pnl_r": 1.0},
        # 2 entries in the ranging phase A: R = -1, -1
        {"entry_i": 700, "pnl_r": -1.0},
        {"entry_i": 750, "pnl_r": -1.0},
        # 1 entry before regimes have formed -> unknown, must not crash
        {"entry_i": 5, "pnl_r": 5.0},
    ]
    rep = regime_report(trades, bars)
    assert rep["n_trades"] == 8
    assert sum(m["n_trades"] for m in rep["vol"].values()) == 8
    assert sum(m["n_trades"] for m in rep["trend"].values()) == 8

    # high-vol bucket: rs = [2, 1, -1] -> wr 2/3, avg_win 1.5, avg_loss 1
    hv = rep["vol"]["high_vol"]
    assert hv["n_trades"] == 3
    assert hv["expectancy"] == pytest.approx(2 / 3 * 1.5 - 1 / 3 * 1.0, abs=1e-4)
    assert hv["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert hv["profit_factor"] == pytest.approx(3.0, abs=1e-4)

    # trending bucket: rs = [1, 1] -> expectancy 1, no losses -> pf inf
    tr = rep["trend"]["trending"]
    assert tr["n_trades"] == 2 and tr["expectancy"] == 1.0 and tr["win_rate"] == 1.0
    assert tr["profit_factor"] == math.inf

    # ranging bucket = phase-A trades + high-vol-block trades (block is ranging)
    rg = rep["trend"]["ranging"]
    assert rg["n_trades"] == 5
    assert rg["expectancy"] == pytest.approx(2 / 5 * 1.5 - 3 / 5 * 1.0, abs=1e-4)

    # unknown bucket exists, holds exactly the pre-formation trade, no crash
    assert rep["vol"]["unknown"]["n_trades"] == 1
    assert rep["trend"]["unknown"]["n_trades"] == 1
    assert rep["vol_x_trend"]["unknown"]["n_trades"] == 1

    # summary
    assert rep["summary"]["trend"]["strongest"] == "trending"
    assert rep["summary"]["trend"]["weakest"] == "ranging"
    # vol strongest/weakest: don't pin the label (depends on where the
    # phase-A/trend-leg trades' ATR rank falls), just check ordering holds
    s, w = rep["summary"]["vol"]["strongest"], rep["summary"]["vol"]["weakest"]
    assert rep["vol"][s]["expectancy"] >= rep["vol"][w]["expectancy"]
    assert 0.0 <= rep["summary"]["regime_concentration"] <= 1.0


def test_concentration_formula():
    def b(exp, n):
        return {"expectancy": exp, "n_trades": n, "win_rate": 0, "profit_factor": 0}

    # all positive expectancy in one bucket -> 1.0 (fragile)
    assert _concentration({"a": b(1.0, 4), "b": b(-0.5, 4)}) == 1.0
    # split evenly across two positive buckets -> 0.5 (robust)
    assert _concentration({"a": b(1.0, 2), "b": b(0.5, 4), "c": b(-1.0, 3)}) == 0.5
    # no positive bucket -> 0.0
    assert _concentration({"a": b(-1.0, 5)}) == 0.0
    # unknown bucket excluded from the calc
    assert _concentration({"a": b(1.0, 2), "unknown": b(9.0, 9)}) == 1.0
