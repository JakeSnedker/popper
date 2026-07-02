"""compile_to_backtest — spec -> runnable pure-Python backtest (spec/01).

Popper never generates MQL5. Features are vectorised; fills are simulated
event-driven over the bars. Timing model (no look-ahead):
  - signals evaluate on a bar's CLOSE
  - market entries fill at the NEXT bar's open
  - stop distance uses ATR as of the signal bar
  - within a bar, stop is checked before target (conservative); gaps fill
    at the open; trail/break-even update AFTER exit checks, from that
    bar's data, so they only tighten the following bars.

compile_to_sim / compile_to_live are later phases.
"""

import re

import numpy as np
import pandas as pd

from popper.artifacts.strategy_spec import StrategySpec
from popper.dsl.expr import BAR_COLS, eval_signal
from popper.dsl.features import atr_14, compute_feature
from popper.dsl.validator import validate_spec

_PARAM_REF = re.compile(r"^p\[(\w+)\]$")


def _resolve(value, params: dict) -> float:
    if isinstance(value, str):
        return float(params[_PARAM_REF.match(value).group(1)])
    return float(value)


class CompiledStrategy:
    """Callable: (bars, params=None) -> result dict. Validated at compile time."""

    def __init__(self, spec: StrategySpec):
        self.spec = validate_spec(spec)

    def default_params(self) -> dict:
        # DECISION: default = middle of each grid (a representative point,
        # not an edge). The falsifier probes the full grid in Phase 3.
        return {k: v[len(v) // 2] for k, v in self.spec.param_space.items()}

    def _env(self, bars: pd.DataFrame) -> dict[str, pd.Series]:
        env = {c: bars[c] for c in BAR_COLS if c in bars.columns}
        if "volume" not in env and "tick_volume" in bars.columns:
            env["volume"] = bars["tick_volume"]
        for f in self.spec.alpha.features:
            env[f] = compute_feature(f, bars)
        return env

    def __call__(self, bars: pd.DataFrame, params: dict | None = None) -> dict:
        spec = self.spec
        params = {**self.default_params(), **(params or {})}

        env = self._env(bars)
        long_sig = eval_signal(spec.alpha.signal, env, params).to_numpy()
        short_sig = (
            eval_signal(spec.alpha.signal_short, env, params).to_numpy()
            if spec.alpha.signal_short else np.zeros(len(bars), dtype=bool)
        )
        atr = atr_14(bars).to_numpy()

        o = bars["open"].to_numpy()
        h = bars["high"].to_numpy()
        low = bars["low"].to_numpy()
        c = bars["close"].to_numpy()
        days = pd.DatetimeIndex(bars["time"]).date if "time" in bars.columns else np.zeros(len(bars))

        w = spec.wrapper
        sl_mult = _resolve(w.stop.mult, params)
        rr = _resolve(w.target.ratio, params)
        trail_mult = _resolve(w.trail.mult, params) if w.trail.type == "atr" else None
        be_trigger = _resolve(w.break_even.trigger_r, params) if w.break_even.type == "after_r" else None
        risk_frac = w.sizing.risk_pct / 100.0
        max_per_day = w.constraints.max_trades_per_day

        equity, equity_curve, trades = 1.0, [1.0], []
        pos = 0  # +1 long, -1 short, 0 flat
        pending = 0  # signal fired at prev close, enter at this open
        pending_atr = np.nan
        entry = stop = target = risk = np.nan
        entry_i = 0
        day_trades: dict = {}

        def close_trade(i: int, price: float, reason: str):
            nonlocal equity, pos
            pnl_r = (price - entry) / risk * pos
            equity *= 1.0 + risk_frac * pnl_r
            trades.append({
                "dir": "long" if pos == 1 else "short",
                "entry_i": entry_i, "exit_i": i,
                "entry": round(entry, 2), "exit": round(price, 2),
                "pnl_r": round(pnl_r, 4), "reason": reason,
            })
            equity_curve.append(round(equity, 6))
            pos = 0

        for i in range(len(bars)):
            # 1) open a pending entry at this bar's open
            if pos == 0 and pending != 0 and not np.isnan(pending_atr) and pending_atr > 0:
                d = days[i]
                if max_per_day is None or day_trades.get(d, 0) < max_per_day:
                    pos, entry, entry_i = pending, o[i], i
                    risk = sl_mult * pending_atr
                    stop = entry - pos * risk
                    target = entry + pos * rr * risk
                    day_trades[d] = day_trades.get(d, 0) + 1
            pending = 0

            # 2) manage an open position: stop first (conservative), then target
            if pos == 1:
                if low[i] <= stop:
                    close_trade(i, min(o[i], stop), "stop")
                elif h[i] >= target:
                    close_trade(i, max(o[i], target), "target")
            elif pos == -1:
                if h[i] >= stop:
                    close_trade(i, max(o[i], stop), "stop")
                elif low[i] <= target:
                    close_trade(i, min(o[i], target), "target")

            # 3) tighten stop for FUTURE bars from this bar's data
            if pos != 0:
                if be_trigger is not None:
                    fav = (h[i] - entry) if pos == 1 else (entry - low[i])
                    if fav >= be_trigger * risk:
                        stop = max(stop, entry) if pos == 1 else min(stop, entry)
                if trail_mult is not None and not np.isnan(atr[i]):
                    trailed = c[i] - pos * trail_mult * atr[i]
                    stop = max(stop, trailed) if pos == 1 else min(stop, trailed)

            # 4) signal on this bar's close -> entry next bar
            if pos == 0:
                if long_sig[i]:
                    pending, pending_atr = 1, atr[i]
                elif short_sig[i]:
                    pending, pending_atr = -1, atr[i]

        if pos != 0:  # close a dangling position at the last close
            close_trade(len(bars) - 1, c[-1], "eod")

        wins = sum(1 for t in trades if t["pnl_r"] > 0)
        return {
            "params": params,
            "n_trades": len(trades),
            "trades": trades,
            "equity_curve": equity_curve,
            "total_return": round(equity - 1.0, 6),
            "win_rate": round(wins / len(trades), 4) if trades else 0.0,
        }


def compile_to_backtest(spec: StrategySpec | dict) -> CompiledStrategy:
    """Validate + compile a spec into a runnable backtest callable."""
    if isinstance(spec, dict):
        spec = validate_spec(spec)
    return CompiledStrategy(spec)
