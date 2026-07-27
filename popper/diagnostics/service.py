"""Run one diagnostic against an existing hypothesis without rerunning the gauntlet."""

import math

from popper.artifacts import StrategySpec
from popper.dsl.validator import DSLValidationError, validate_local_data_context
from popper.experiments.runner import run
from popper.falsify.stats import bootstrap_cone, monte_carlo_ruin
from popper.memory import get_memory


class DiagnosticUnavailable(RuntimeError):
    pass


def _usable_curve(curve) -> bool:
    return bool(curve and len(curve) >= 3 and all(
        isinstance(value, (int, float)) and math.isfinite(value) and value > 0
        for value in curve
    ))


def _selected_spec(record: dict) -> StrategySpec:
    specs = {row["id"]: row for row in record.get("specs", [])}
    experiments = {row["id"]: row for row in record.get("experiments", [])}
    verdicts = sorted(record.get("verdicts", []), key=lambda row: row["created_at"], reverse=True)
    for verdict in verdicts:
        experiment = experiments.get(verdict["experiment_id"])
        if experiment and experiment["spec_id"] in specs:
            return StrategySpec(**specs[experiment["spec_id"]])
    if specs:
        return StrategySpec(**next(iter(specs.values())))
    raise DiagnosticUnavailable("hypothesis has no StrategySpec to test")


def diagnostic_status(hypothesis_id: str, mem=None) -> dict:
    mem = mem or get_memory()
    record = mem.record(hypothesis_id)
    if not record:
        raise DiagnosticUnavailable("hypothesis not found")
    existing = next((row for row in record.get("experiments", [])
                     if _usable_curve(row.get("equity_curve"))), None)
    try:
        spec = _selected_spec(record)
        validate_local_data_context(spec)
        runnable, reason = True, None
    except (DiagnosticUnavailable, DSLValidationError) as exc:
        runnable, reason = False, str(exc)
    return {
        "hypothesis_id": hypothesis_id,
        "diagnostics": [{
            "name": "monte_carlo",
            "completed": existing is not None,
            "runnable": runnable,
            "reason": reason,
            "experiment_id": existing.get("id") if existing else None,
        }],
    }


def run_monte_carlo(hypothesis_id: str, mem=None, persist: bool = True) -> dict:
    """Use an existing curve or backfill one raw experiment, then run MC diagnostics."""
    mem = mem or get_memory()
    record = mem.record(hypothesis_id)
    if not record:
        raise DiagnosticUnavailable("hypothesis not found")
    spec = _selected_spec(record)

    experiment = max(
        (row for row in record.get("experiments", []) if _usable_curve(row.get("equity_curve"))),
        key=lambda row: len(row["equity_curve"]),
        default=None,
    )
    source = "existing"
    if experiment is None:
        try:
            validate_local_data_context(spec)
        except DSLValidationError as exc:
            raise DiagnosticUnavailable(
                f"cannot backfill this strategy on this machine: {exc}"
            ) from exc
        generated = run(spec)
        if not _usable_curve(generated.equity_curve):
            raise DiagnosticUnavailable(
                "backtest completed but produced fewer than two usable trade returns"
            )
        if persist:
            generated = mem.add_experiment(generated)
        experiment = generated.model_dump(mode="json")
        source = "backfill"

    cone = bootstrap_cone(experiment["equity_curve"])
    if cone is None:
        raise DiagnosticUnavailable("experiment equity curve is not usable for Monte Carlo")
    trades = (experiment.get("raw_metrics") or {}).get("trades") or []
    pnl_r = [float(row["pnl_r"]) for row in trades if row.get("pnl_r") is not None]
    ruin = monte_carlo_ruin(
        pnl_r,
        risk_frac=spec.wrapper.sizing.risk_pct / 100.0,
    ) if pnl_r else None
    cone.update({
        "experiment_id": experiment["id"],
        "trades_n": experiment["trades_n"],
    })
    return {
        "hypothesis_id": hypothesis_id,
        "name": "monte_carlo",
        "source": source,
        "persisted": source == "backfill" and persist,
        "experiment_id": experiment["id"],
        "gate": ruin,
        "cone": cone,
    }
