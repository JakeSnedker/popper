import pytest

from popper.artifacts import ExperimentResult, StrategySpec
from popper.diagnostics import DiagnosticUnavailable, diagnostic_status, run_monte_carlo


def _spec(symbol="US30"):
    return StrategySpec(
        id="S-1", hypothesis_id="H-1", name="test",
        alpha={"features": ["ema_50"], "signal": "close > ema_50"},
        context={"universe": [symbol], "timeframe": "5m"},
    )


class Memory:
    def __init__(self, record):
        self.value = record
        self.added = []

    def record(self, _hypothesis_id):
        return self.value

    def add_experiment(self, experiment):
        stored = experiment.model_copy(update={"id": "E-BACKFILL"})
        self.added.append(stored)
        return stored


def _record(spec, experiment=None):
    return {
        "hypothesis": {"id": "H-1", "status": "promoted"},
        "specs": [spec.model_dump(mode="json")],
        "experiments": [experiment] if experiment else [],
        "scorecards": [],
        "verdicts": [],
    }


def test_existing_equity_curve_runs_only_monte_carlo():
    experiment = {
        "id": "E-1", "spec_id": "S-1", "dataset": "x", "period_start": "a",
        "period_end": "b", "trades_n": 3,
        "raw_metrics": {"trades": [{"pnl_r": 1.0}, {"pnl_r": -0.5}, {"pnl_r": 0.8}]},
        "equity_curve": [1.0, 1.01, 1.005, 1.02],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    memory = Memory(_record(_spec(), experiment))
    result = run_monte_carlo("H-1", mem=memory)
    assert result["source"] == "existing"
    assert result["cone"]["experiment_id"] == "E-1"
    assert result["gate"]["p_ruin"] >= 0
    assert not memory.added


def test_missing_curve_backfills_one_raw_experiment(monkeypatch):
    generated = ExperimentResult(
        spec_id="S-1", dataset="x", period_start="a", period_end="b", trades_n=3,
        raw_metrics={"trades": [{"pnl_r": 1.0}, {"pnl_r": -0.4}, {"pnl_r": 0.6}]},
        equity_curve=[1.0, 1.01, 1.006, 1.018],
    )
    monkeypatch.setattr("popper.diagnostics.service.run", lambda _spec: generated)
    memory = Memory(_record(_spec()))
    result = run_monte_carlo("H-1", mem=memory)
    assert result["source"] == "backfill"
    assert result["persisted"] is True
    assert result["experiment_id"] == "E-BACKFILL"
    assert len(memory.added) == 1


def test_unavailable_local_market_reports_actionable_error():
    memory = Memory(_record(_spec("NAS100")))
    status = diagnostic_status("H-1", mem=memory)
    assert status["diagnostics"][0]["runnable"] is False
    with pytest.raises(DiagnosticUnavailable, match="cannot backfill"):
        run_monte_carlo("H-1", mem=memory)
