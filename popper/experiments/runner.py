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
from popper.config import settings
from popper.data import load_bars, load_higher_timeframe_bars
from popper.dsl import compile_to_backtest


def _max_drawdown(equity: list[float]) -> float:
    """Largest peak-to-trough fractional drop. 0.0 if the curve only rises."""
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak else 0.0)
    return mdd


def _net_metrics(trades: list[dict], spread) -> dict:
    """Net-of-cost drag from the settings default cost model — INFORMATIONAL
    ONLY, derived analytically from the FRICTIONLESS trades (no second
    backtest): cost_px = 0.5*(spread[entry]+spread[exit])*pt + 2*slip*pt +
    comm*pt; cost_r = cost_px / trade.risk; net R = pnl_r - cost_r.
    NON-GATING by design: no gauntlet gate reads *_net — near-miss edges get
    rescued by the exploit engine, never auto-killed on cost."""
    pt = settings.point_value
    per_trade = []  # (gross_r, cost_r)
    for t in trades:
        risk = t.get("risk")
        if not risk or t.get("cost_r"):
            return {}  # pre-risk-field trades, or an already-costed (non-frictionless) run
        cost_px = (0.5 * (spread[t["entry_i"]] + spread[t["exit_i"]]) * pt
                   + 2 * settings.slippage_pts * pt + settings.commission_pts * pt)
        per_trade.append((t["pnl_r"], cost_px / risk))
    if not per_trade:
        return {}
    net = [g - c for g, c in per_trade]
    n = len(net)
    wins = [r for r in net if r > 0]
    losses = [r for r in net if r < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    expectancy_net = (len(wins) / n) * avg_win - (len(losses) / n) * avg_loss
    gp, gl = sum(wins), abs(sum(losses))
    pf_net = gp / gl if gl else (math.inf if gp else 0.0)
    gross_winner_net = [nr for (g, _), nr in zip(per_trade, net) if g > 0]
    flips = sum(1 for nr in gross_winner_net if nr <= 0)
    return {
        "expectancy_net": round(expectancy_net, 4),
        "profit_factor_net": round(pf_net, 4) if math.isfinite(pf_net) else pf_net,
        "avg_cost_r": round(sum(c for _, c in per_trade) / n, 4),
        "pct_trades_cost_flip": round(flips / len(gross_winner_net), 4) if gross_winner_net else 0.0,
    }


def compute_metrics(result: dict, spread=None) -> dict:
    """Backtest result dict -> raw_metrics (METHODOLOGY definitions, R-multiples).
    `spread` (per-bar spread series in points, or None) adds informational
    *_net fields; the gross metrics are byte-identical with or without it."""
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

    m = {
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
    if spread is not None and trades:
        m.update(_net_metrics(trades, spread))
    return m


def run(
    spec: StrategySpec,
    period: tuple[str | None, str | None] | None = None,
    params: dict | None = None,
    mem=None,
    costs: dict | None = None,
) -> ExperimentResult:
    """Backtest `spec` over US30 bars for `period` (start, end) and return a
    populated ExperimentResult. If `mem` is given, persist the spec (if it has
    no id) and the experiment, returning the stored (id-assigned) experiment.
    `costs` (None = frictionless) applies spread/slippage/commission per trade —
    see CompiledStrategy.__call__."""
    start, end = period or (None, None)
    tf = spec.context.timeframe
    symbol = spec.context.universe[0]
    bars = load_bars(symbol, _tf_to_mt5(tf), start, end)
    higher_bars = load_higher_timeframe_bars(
        symbol, spec.alpha.higher_timeframe_features, end=end,
    )

    bt = compile_to_backtest(spec)
    result = bt(bars, params, costs, higher_timeframe_bars=higher_bars)

    period_start = str(bars["time"].iloc[0].date()) if len(bars) else (start or "")
    period_end = str(bars["time"].iloc[-1].date()) if len(bars) else (end or "")

    exp = ExperimentResult(
        spec_id=spec.id or "",
        dataset=(
            f"{symbol} {tf}"
            f"{''.join('+' + key for key in higher_bars)} {period_start}..{period_end}"
        ),
        period_start=period_start,
        period_end=period_end,
        trades_n=result["n_trades"],
        raw_metrics=compute_metrics(
            result,
            spread=bars["spread"].to_numpy() if "spread" in bars.columns else None,
        ),
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


def bars_for_spec(spec: StrategySpec, period: tuple[str | None, str | None] | None = None):
    """The exact bar frame run() loads for this spec+period — lru-cached, so calling
    it after a judge/backtest is a cache hit, not a second read. Used to thread real
    bars into complete_scorecard so C3 regime conditioning fires on live runs
    (explore AND exploit paths). Callers gate on `run_fn is run` (synthetic runners
    must NOT pull real parquet)."""
    start, end = period or (None, None)
    return load_bars(spec.context.universe[0], _tf_to_mt5(spec.context.timeframe), start, end)


def _tf_to_mt5(tf: str) -> str:
    """'5m' -> 'M5'. Passes through if already MT5-style."""
    tf = tf.strip()
    if tf[:-1].isdigit() and tf[-1].lower() == "m":
        return f"M{tf[:-1]}"
    return tf.upper()
