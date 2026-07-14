"""Phase C3: real-regime conditioning of Scorecard.regime_stability.

complete_scorecard(..., bars=bars) must score an edge spread across real
vol/trend regimes HIGHER than one concentrated in a single regime, and with
bars=None must reproduce the crude temporal-chunk v0 exactly (non-gating,
backward compatible).
"""

import os
import tempfile

import numpy as np
import pandas as pd
import pytest

from popper.artifacts import ExperimentResult, Hypothesis, Scorecard
from popper.memory import LocalEmbedder, complete_scorecard
from popper.memory.map import _trades_r, regime_stability
from popper.memory.sqlite_repo import SqliteMemory

# same engineered market as test_regime_report: ranging A, high-vol block B, trend leg C
N = 1400
HV_START, HV_END = 900, 1100
TREND_START = 1100


@pytest.fixture(scope="module")
def bars():
    i = np.arange(N)
    close = 100 + 0.5 * np.sin(i / 3.0)
    close[TREND_START:] = 100 + 0.5 * (i[TREND_START:] - TREND_START + 1)
    half = np.where((i >= HV_START) & (i < HV_END), 2.5, 0.5)
    return pd.DataFrame({
        "open": close, "high": close + half, "low": close - half, "close": close,
    })


def _mem() -> SqliteMemory:
    return SqliteMemory(os.path.join(tempfile.mkdtemp(), "t.db"), embedder=LocalEmbedder())


def _score(trades, bars=None):
    mem = _mem()
    h = mem.add_hypothesis(Hypothesis(claim="momentum persists after a drive", mechanism="flow"))
    exp = ExperimentResult(
        spec_id="S", dataset="d", period_start="a", period_end="b",
        trades_n=len(trades), equity_curve=[1.0, 1.1],
        raw_metrics={"pnl": 0.1, "win_rate": 0.5, "expectancy": 0.1, "trades": trades})
    return complete_scorecard(Scorecard(experiment_id="E"), mem, h, exp, bars=bars)


def test_diversified_beats_concentrated(bars):
    # winners in BOTH the high-vol block and the trend leg -> spread across regimes
    diversified = [
        {"entry_i": 960, "pnl_r": 2.0}, {"entry_i": 1040, "pnl_r": 1.0},
        {"entry_i": 1300, "pnl_r": 2.0}, {"entry_i": 1380, "pnl_r": 1.0},
        {"entry_i": 700, "pnl_r": -1.0}, {"entry_i": 750, "pnl_r": -1.0},
    ]
    # ALL profit inside the high-vol block, losses everywhere else -> one regime
    concentrated = [
        {"entry_i": 960, "pnl_r": 2.0}, {"entry_i": 1000, "pnl_r": 2.0},
        {"entry_i": 1040, "pnl_r": 1.0},
        {"entry_i": 1300, "pnl_r": -1.0}, {"entry_i": 1380, "pnl_r": -1.0},
        {"entry_i": 700, "pnl_r": -1.0},
    ]
    div = _score(diversified, bars=bars).regime_stability
    con = _score(concentrated, bars=bars).regime_stability
    assert div > con
    assert con == 0.0          # 1 - concentration(1.0): all edge in one regime
    assert 0.0 < div <= 1.0


def test_bars_none_keeps_crude_path(bars):
    trades = [{"entry_i": i, "pnl_r": r} for i, r in
              zip((700, 750, 960, 1000, 1040, 1300, 1380, 800),
                  (2.0, -1.0, 2.0, -1.0, 2.0, -1.0, 2.0, -1.0))]
    sc = _score(trades)  # no bars
    exp = ExperimentResult(spec_id="S", dataset="d", period_start="a", period_end="b",
                           raw_metrics={"trades": trades})
    assert sc.regime_stability == round(regime_stability(_trades_r(exp)), 4)


def test_degenerate_report_falls_back_to_crude(bars):
    # all losers -> concentration 0.0 (no positive bucket) -> crude fallback
    losers = [{"entry_i": i, "pnl_r": -1.0} for i in (700, 750, 960, 1000, 1040, 1300, 1380, 800)]
    sc = _score(losers, bars=bars)
    exp = ExperimentResult(spec_id="S", dataset="d", period_start="a", period_end="b",
                           raw_metrics={"trades": losers})
    assert sc.regime_stability == round(regime_stability(_trades_r(exp)), 4)

    # no trades at all -> crude fallback (0.0), no crash
    assert _score([], bars=bars).regime_stability == 0.0
