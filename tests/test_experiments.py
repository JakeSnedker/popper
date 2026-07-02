"""Phase 2 acceptance: run(spec) returns a populated ExperimentResult on real
US30 M5 for all 3 example alphas, metrics obey their definitions, and the
result persists + round-trips through alpha memory."""

import os
import tempfile
from pathlib import Path

import pytest

from popper.data import load_bars
from popper.dsl import load_spec
from popper.experiments import compute_metrics, run
from popper.memory.sqlite_repo import SqliteMemory

EXAMPLES = sorted((Path(__file__).parent.parent / "popper" / "dsl" / "examples").glob("*.yaml"))
US30 = Path(__file__).parent.parent / "data" / "us30" / "US30_M5.parquet"
pytestmark = pytest.mark.skipif(not US30.exists(), reason="US30 parquet not present")

_REQUIRED = {
    "pnl", "profit_factor", "sharpe", "win_rate", "max_drawdown",
    "expectancy", "recovery_factor", "trades_n", "avg_win", "avg_loss",
}


def test_load_bars_slices_period():
    full = load_bars("US30", "M5")
    assert len(full) > 1000 and list(full.columns[:5]) == ["time", "open", "high", "low", "close"]
    sliced = load_bars("US30", "M5", start="2025-06-01", end="2025-06-30")
    assert 0 < len(sliced) < len(full)
    assert sliced["time"].min() >= full["time"].min()
    assert str(sliced["time"].max().date()) <= "2025-06-30"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_run_populates_result_on_us30(path):
    exp = run(load_spec(path))
    assert exp.trades_n > 0
    assert exp.equity_curve and len(exp.equity_curve) == exp.trades_n + 1
    assert exp.period_start and exp.period_end and exp.dataset
    assert _REQUIRED <= set(exp.raw_metrics)
    m = exp.raw_metrics
    assert m["trades_n"] == exp.trades_n == len(m["trades"])
    # metrics in sane ranges
    assert 0.0 <= m["win_rate"] <= 1.0
    assert 0.0 <= m["max_drawdown"] <= 1.0
    assert m["profit_factor"] >= 0 and m["avg_win"] >= 0 and m["avg_loss"] >= 0


def test_metric_definitions_hold():
    """PF, expectancy, and PF<->expectancy sign agreement on a known trade set."""
    result = {
        "trades": [{"pnl_r": 2.0}, {"pnl_r": 2.0}, {"pnl_r": -1.0}, {"pnl_r": -1.0}, {"pnl_r": -1.0}],
        "equity_curve": [1.0, 1.02, 1.04, 1.03, 1.02, 1.01],
        "total_return": 0.01,
    }
    m = compute_metrics(result)
    assert m["trades_n"] == 5
    assert m["win_rate"] == 0.4
    assert m["avg_win"] == 2.0 and m["avg_loss"] == 1.0
    # PF = gross_profit/gross_loss = 4/3
    assert m["profit_factor"] == pytest.approx(4 / 3, abs=1e-3)
    # E = 0.4*2 - 0.6*1 = 0.2
    assert m["expectancy"] == pytest.approx(0.2, abs=1e-3)
    # PF > 1  <=>  E > 0
    assert (m["profit_factor"] > 1) == (m["expectancy"] > 0)
    # recovery = net_profit / max_dd; max_dd here = (1.04-1.01)/1.04
    assert m["max_drawdown"] == pytest.approx((1.04 - 1.01) / 1.04, abs=1e-4)


def test_result_persists_and_roundtrips():
    mem = SqliteMemory(os.path.join(tempfile.mkdtemp(), "test.db"))
    spec = load_spec(EXAMPLES[0])
    exp = run(spec, period=("2025-06-01", "2025-06-30"), mem=mem)
    assert exp.id and exp.spec_id  # ids assigned by the store (spec + experiment)

    got = _reload_experiment(mem, exp.id)
    assert got["trades_n"] == exp.trades_n
    assert got["raw_metrics"]["profit_factor"] == exp.raw_metrics["profit_factor"]
    assert got["equity_curve"][0] == exp.equity_curve[0]


def _reload_experiment(mem: SqliteMemory, exp_id: str) -> dict:
    import json
    with mem._connect() as con:
        row = dict(con.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,)).fetchone())
    for col in ("raw_metrics", "equity_curve"):
        row[col] = json.loads(row[col])
    return row
