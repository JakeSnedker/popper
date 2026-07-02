"""run(spec, period=...) -> ExperimentResult.

Wraps the Phase-1 compile_to_backtest, executes it over loaded US30 bars, and
records what happened as a populated ExperimentResult. Metric definitions are
ported from the JJC validation-framework METHODOLOGY.md:

  Expectancy   E  = Win% * Avg_Win - Loss% * Avg_Loss
  Profit Factor   = gross_profit / gross_loss
  Recovery Factor = net_profit / max_drawdown          ("Profit / Max DD")
  Win Rate        = winners / trades
  Max Drawdown    = largest peak-to-trough drop on the equity curve

Everything is in R-multiples (the wrapper's native risk unit): each trade's
pnl is `pnl_r`, so Avg_Win / Avg_Loss / Expectancy are in R and are broker/
account-size independent. `pnl` is the equity curve's total fractional return
(risk_pct compounding).
"""

import math

from popper.artifacts import ExperimentResult
from popper.artifacts.strategy_spec import StrategySpec
from popper.data import load_bars
from popper.dsl import compile_to_backtest


def _max_drawdown(equity: list[float]) -> float:
    """Largest peak-to-trough fractional drop. 0.0 if the curve only rises."""
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak else 0.0)
    return mdd


def compute_metrics(result: dict) -> dict:
    """Backtest result dict -> raw_metrics (METHODOLOGY definitions, R-multiples)."""
    trades = result["trades"]
    rs = [t["pnl_r"] for t in trades]
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]  # exact-0 (rare) counts as neither

    win_rate = len(wins) / n if n else 0.0
    loss_rate = len(losses) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0  # positive magnitude
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    expectancy = win_rate * avg_win - loss_rate * avg_loss
    profit_factor = gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0)

    # per-trade Sharpe: mean/std of trade R (unitless, no annualisation).
    # DECISION: annualising needs a trade-frequency assumption the runner
    # doesn't own yet; per-trade Sharpe is the honest, comparable number now.
    if n > 1:
        mean_r = sum(rs) / n
        var = sum((r - mean_r) ** 2 for r in rs) / (n - 1)
        std = math.sqrt(var)
        sharpe = mean_r / std if std else 0.0
    else:
        sharpe = 0.0

    pnl = result["total_return"]  # equity curve fractional return (risk_pct compounding)
    max_dd = _max_drawdown(result["equity_curve"])
    recovery = pnl / max_dd if max_dd else (math.inf if pnl > 0 else 0.0)

    return {
        "pnl": round(pnl, 6),
        "profit_factor": round(profit_factor, 4) if math.isfinite(profit_factor) else profit_factor,
        "sharpe": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
        "max_drawdown": round(max_dd, 6),
        "expectancy": round(expectancy, 4),
        "recovery_factor": round(recovery, 4) if math.isfinite(recovery) else recovery,
        "trades_n": n,
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
    }


def run(
    spec: StrategySpec,
    period: tuple[str | None, str | None] | None = None,
    params: dict | None = None,
    mem=None,
) -> ExperimentResult:
    """Backtest `spec` over US30 bars for `period` (start, end) and return a
    populated ExperimentResult. If `mem` is given, persist the spec (if it has
    no id) and the experiment, returning the stored (id-assigned) experiment."""
    start, end = period or (None, None)
    tf = spec.context.timeframe
    symbol = spec.context.universe[0]
    bars = load_bars(symbol, _tf_to_mt5(tf), start, end)

    bt = compile_to_backtest(spec)
    result = bt(bars, params)

    period_start = str(bars["time"].iloc[0].date()) if len(bars) else (start or "")
    period_end = str(bars["time"].iloc[-1].date()) if len(bars) else (end or "")

    exp = ExperimentResult(
        spec_id=spec.id or "",
        dataset=f"{symbol} {tf} {period_start}..{period_end}",
        period_start=period_start,
        period_end=period_end,
        trades_n=result["n_trades"],
        raw_metrics=compute_metrics(result),
        equity_curve=result["equity_curve"],
    )
    # trades[] live under raw_metrics so the immutable ExperimentResult contract
    # (Phase 0) stays unchanged. ponytail: promote to a first-class field if a
    # consumer needs to query trades without deserialising the whole metrics blob.
    exp.raw_metrics["trades"] = result["trades"]

    if mem is not None:
        if not spec.id:
            spec = mem.add_spec(spec)
            exp.spec_id = spec.id
        exp = mem.add_experiment(exp)
    return exp


def _tf_to_mt5(tf: str) -> str:
    """'5m' -> 'M5'. Passes through if already MT5-style."""
    tf = tf.strip()
    if tf[:-1].isdigit() and tf[-1].lower() == "m":
        return f"M{tf[:-1]}"
    return tf.upper()
