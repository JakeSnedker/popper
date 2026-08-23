"""compile_to_backtest — spec -> runnable pure-Python backtest (spec/01).

Popper never generates MQL5. Features are vectorised; fills are simulated
event-driven over the bars. Timing model (no look-ahead):
  - signals evaluate on a bar's CLOSE
  - market entries fill at the NEXT bar's open
  - stop distance uses ATR as of the signal bar
  - within a bar, stop is checked before target (conservative); gaps fill
    at the open; trail/break-even update AFTER exit checks, from that
    bar's data, so they only tighten the following bars.

v0.3 (docs/DSL_v0.3_EXIT_SPEC.md §4): stops/targets may anchor to a level-
feature series. stop = level[i] + offset; target = level[i] + offset, resolved at
the FILL bar i.

KNOWN GAP (found 2026-08-23 while building the Forge emitters, unfixed here
because changing it moves every recorded v0.3 backtest): that resolution assumed
"level[i] uses bars <= i-1, all complete at bar i's open". True for the swing and
session levels; FALSE for prior_fractal_hi/lo (confirmation reads bar i's own
high/low) and bb_line (the SMA includes bar i's close). Anchoring an exit to
those three reads a value that does not exist at the fill, so a backtest using
them is optimistic by up to one bar. popper/forge/{pine,mql5}.py refuse to render
them as exit anchors for exactly this reason; tests/test_forge_levels.py has the
demonstration.
GUARD: an entry whose anchor is NaN, or whose level sits on the wrong side of
entry (stop >= entry for a long, target <= entry, mirrored for shorts), is
SKIPPED — an incoherent trade is never opened.

compile_to_sim / compile_to_live are later phases.
"""

import re

import numpy as np
import pandas as pd

from popper.artifacts.strategy_spec import StrategySpec
from popper.data.timeframes import higher_feature_alias, normalize_timeframe, timeframe_minutes
from popper.dsl.expr import BAR_COLS, eval_signal
from popper.dsl.features import atr_14, compute_feature
from popper.dsl.features.levels import compute_level
from popper.dsl.validator import validate_spec

_PARAM_REF = re.compile(r"^p\[(\w+)\]$")


def _resolve(value, params: dict) -> float:
    if isinstance(value, str):
        return float(params[_PARAM_REF.match(value).group(1)])
    return float(value)


def _resolve_name(value: str, params: dict) -> str:
    """An anchor is a literal level-feature name or a p[...] categorical ref."""
    m = _PARAM_REF.match(value)
    return str(params[m.group(1)]) if m else value


def align_completed_feature(
    base_bars: pd.DataFrame,
    higher_bars: pd.DataFrame,
    values: pd.Series,
    base_timeframe: str,
    higher_timeframe: str,
) -> pd.Series:
    """Align a higher feature only when its source bar has completed."""
    if "time" not in base_bars or "time" not in higher_bars:
        raise ValueError("multi-timeframe alignment requires UTC time columns")
    if len(values) != len(higher_bars):
        raise ValueError("higher feature length does not match its source bars")
    base_close = (
        pd.to_datetime(base_bars["time"], utc=True)
        + pd.Timedelta(minutes=timeframe_minutes(base_timeframe))
    )
    target = pd.DataFrame({"available": base_close, "position": np.arange(len(base_bars))})
    source = pd.DataFrame({
        "available": (
            pd.to_datetime(higher_bars["time"], utc=True)
            + pd.Timedelta(minutes=timeframe_minutes(higher_timeframe))
        ),
        "value": values.to_numpy(),
    })
    if source.empty:
        return pd.Series(np.nan, index=base_bars.index, name=values.name, dtype="float64")
    source = source.sort_values("available", kind="stable").drop_duplicates(
        "available", keep="last",
    )
    aligned = pd.merge_asof(
        target.sort_values("available", kind="stable"),
        source,
        on="available",
        direction="backward",
        allow_exact_matches=True,
    ).sort_values("position")
    return pd.Series(aligned["value"].to_numpy(), index=base_bars.index, name=values.name)


class CompiledStrategy:
    """Callable: (bars, params=None) -> result dict. Validated at compile time."""

    def __init__(self, spec: StrategySpec):
        self.spec = validate_spec(spec)

    def default_params(self) -> dict:
        # DECISION: default = middle of each grid (a representative point,
        # not an edge). The falsifier probes the full grid in Phase 3.
        return {k: v[len(v) // 2] for k, v in self.spec.param_space.items()}

    def _env(
        self,
        bars: pd.DataFrame,
        higher_timeframe_bars: dict[str, pd.DataFrame] | None = None,
    ) -> dict[str, pd.Series]:
        env = {c: bars[c] for c in BAR_COLS if c in bars.columns}
        if "volume" not in env and "tick_volume" in bars.columns:
            env["volume"] = bars["tick_volume"]
        for feature in dict.fromkeys(self.spec.alpha.features):
            env[feature] = compute_feature(feature, bars)

        requested = self.spec.alpha.higher_timeframe_features
        if not requested:
            return env
        supplied = {}
        for timeframe, frame in (higher_timeframe_bars or {}).items():
            canonical = normalize_timeframe(timeframe)
            if canonical in supplied:
                raise ValueError(f"duplicate supplied higher timeframe: {canonical}")
            supplied[canonical] = frame
        for timeframe, features in requested.items():
            canonical = normalize_timeframe(timeframe)
            if canonical not in supplied:
                raise ValueError(f"missing bars for required higher timeframe {canonical}")
            source = supplied[canonical]
            for feature in dict.fromkeys(features):
                values = compute_feature(feature, source)
                env[higher_feature_alias(canonical, feature)] = align_completed_feature(
                    bars, source, values, self.spec.context.timeframe, canonical,
                )
        return env

    def __call__(self, bars: pd.DataFrame, params: dict | None = None,
                 costs: dict | None = None,
                 higher_timeframe_bars: dict[str, pd.DataFrame] | None = None) -> dict:
        spec = self.spec
        params = {**self.default_params(), **(params or {})}

        env = self._env(bars, higher_timeframe_bars)
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
        # exit resolution (v0.3): atr|level stops, ratio|level targets.
        # anchor level series precomputed ONCE — already point-in-time correct.
        sl_mult = stop_lvl = stop_off = None
        if w.stop.type == "level":
            stop_lvl = compute_level(_resolve_name(w.stop.anchor, params), bars).to_numpy()
            stop_off = _resolve(w.stop.offset, params)
        else:
            sl_mult = _resolve(w.stop.mult, params)
        rr = tgt_lvl = tgt_off = None
        if w.target.type == "level":
            tgt_lvl = compute_level(_resolve_name(w.target.anchor, params), bars).to_numpy()
            tgt_off = _resolve(w.target.offset, params)
        else:
            rr = _resolve(w.target.ratio, params)
        # entry: market fills at the next open; limit/stop rest until touched.
        entry_kind = w.entry.type
        entry_lvl = entry_off = None
        if entry_kind != "market":
            if w.entry.anchor:
                entry_lvl = compute_level(_resolve_name(w.entry.anchor, params), bars).to_numpy()
            else:
                entry_off = _resolve(w.entry.offset, params)
        expiry_bars = max(1, int(w.entry.expiry_bars))
        max_bars_in_trade = (
            int(_resolve(w.time_stop.max_bars, params)) if w.time_stop.type == "bars" else None
        )
        trail_mult = _resolve(w.trail.mult, params) if w.trail.type == "atr" else None
        be_trigger = _resolve(w.break_even.trigger_r, params) if w.break_even.type == "after_r" else None
        risk_frac = w.sizing.risk_pct / 100.0
        max_per_day = w.constraints.max_trades_per_day

        # cost model. costs=None -> frictionless (legacy behaviour, all tests).
        # Deducted per trade in R: cross half the bid/ask spread on each side,
        # plus per-side slippage, plus a round-trip commission.
        # ponytail: point_value is a CALIBRATION KNOB — MT5 'spread' is in points
        # and the point size differs by feed (US30 quoted 1- vs 2-decimal), so it
        # must be passed in, never guessed. Set spread_pts to override the data
        # column with a flat assumption.
        cost_slip = cost_comm = 0.0
        spread_px = None
        if costs is not None:
            pt = costs.get("point_value", 0.01)
            cost_slip = costs.get("slippage_pts", 0.0) * pt   # per side
            cost_comm = costs.get("commission_pts", 0.0) * pt  # per round trip
            fixed = costs.get("spread_pts")                    # None -> bar column
            if fixed is not None:
                spread_px = np.full(len(bars), fixed * pt)
            elif "spread" in bars.columns:
                spread_px = bars["spread"].to_numpy() * pt
            else:
                spread_px = np.zeros(len(bars))

        equity, equity_curve, trades = 1.0, [1.0], []
        pos = 0  # +1 long, -1 short, 0 flat
        pending = 0  # signal fired at prev close, enter at this open
        pending_atr = np.nan
        # a resting limit/stop order: direction, price, and bars left to live
        rest_dir, rest_px, rest_left = 0, np.nan, 0
        entry = stop = target = risk = np.nan
        entry_i = 0
        day_trades: dict = {}

        def close_trade(i: int, price: float, reason: str):
            nonlocal equity, pos
            pnl_r = (price - entry) / risk * pos
            cost_r = 0.0
            if costs is not None:
                cost_px = (0.5 * (spread_px[entry_i] + spread_px[i])
                           + 2 * cost_slip + cost_comm)
                cost_r = cost_px / risk
                pnl_r -= cost_r
            equity *= 1.0 + risk_frac * pnl_r
            trades.append({
                "dir": "long" if pos == 1 else "short",
                "entry_i": entry_i, "exit_i": i,
                "entry": round(entry, 2), "exit": round(price, 2),
                # risk = the price distance of 1R — lets net-of-cost metrics be
                # derived exactly from a frictionless run (no second backtest)
                "risk": round(risk, 6),
                "pnl_r": round(pnl_r, 4), "cost_r": round(cost_r, 4),
                "reason": reason,
            })
            equity_curve.append(round(equity, 6))
            pos = 0

        def try_open(i: int, direction: int, e: float, signal_atr: float) -> bool:
            """Open `direction` at price `e` on bar i, if the trade is coherent.

            Shared by every entry type: a market fill at the open and a touched
            limit/stop order differ only in the price handed in here.
            """
            nonlocal pos, entry, entry_i, risk, stop, target
            d = days[i]
            if max_per_day is not None and day_trades.get(d, 0) >= max_per_day:
                return False
            # stop price — atr distance or structural level ± offset
            if sl_mult is not None:
                s = (e - direction * sl_mult * signal_atr
                     if not np.isnan(signal_atr) and signal_atr > 0 else np.nan)
            else:
                s = stop_lvl[i] + stop_off
            # GUARD: skip incoherent entries — NaN anchor (level not formed yet)
            # or a stop on the wrong side of entry
            if np.isnan(s) or not (s < e if direction == 1 else s > e):
                return False
            r_ = abs(e - s)
            if rr is not None:
                tp = e + direction * rr * r_
            else:
                tp = tgt_lvl[i] + tgt_off
                # GUARD: target must sit on the profit side of entry
                if np.isnan(tp) or not (tp > e if direction == 1 else tp < e):
                    return False
            if r_ <= 0:
                return False
            pos, entry, entry_i, risk, stop, target = direction, e, i, r_, s, tp
            day_trades[d] = day_trades.get(d, 0) + 1
            return True

        for i in range(len(bars)):
            # 1) market entry: the signal fired at the previous close, fill here
            if pos == 0 and pending != 0:
                try_open(i, pending, o[i], pending_atr)
            pending = 0

            # 1b) a resting limit/stop order: fill if this bar trades through it.
            # A limit is touched from the favourable side and can only fill at
            # its price or better; a stop is touched from the adverse side and a
            # gap fills it worse. Both mirror the exit handling below.
            if pos == 0 and rest_dir != 0:
                touched = fill = None
                if entry_kind == "limit":
                    if rest_dir == 1 and low[i] <= rest_px:
                        touched, fill = True, min(o[i], rest_px)
                    elif rest_dir == -1 and h[i] >= rest_px:
                        touched, fill = True, max(o[i], rest_px)
                else:  # stop entry
                    if rest_dir == 1 and h[i] >= rest_px:
                        touched, fill = True, max(o[i], rest_px)
                    elif rest_dir == -1 and low[i] <= rest_px:
                        touched, fill = True, min(o[i], rest_px)
                if touched:
                    try_open(i, rest_dir, fill, pending_atr)
                    rest_dir, rest_px, rest_left = 0, np.nan, 0
                else:
                    rest_left -= 1
                    if rest_left <= 0:      # expired unfilled
                        rest_dir, rest_px, rest_left = 0, np.nan, 0

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

            # 2b) time stop: close on elapsed bars regardless of price. Checked
            # after the price exits so a bar that hits the stop AND expires is
            # recorded as the stop -- the worse of the two, conservatively.
            if pos != 0 and max_bars_in_trade is not None and i > entry_i:
                if (i - entry_i) >= max_bars_in_trade:
                    close_trade(i, c[i], "time")

            # 3) tighten stop for FUTURE bars from this bar's data
            if pos != 0:
                if be_trigger is not None:
                    fav = (h[i] - entry) if pos == 1 else (entry - low[i])
                    if fav >= be_trigger * risk:
                        stop = max(stop, entry) if pos == 1 else min(stop, entry)
                if trail_mult is not None and not np.isnan(atr[i]):
                    trailed = c[i] - pos * trail_mult * atr[i]
                    stop = max(stop, trailed) if pos == 1 else min(stop, trailed)

            # 4) signal on this bar's close -> order for the NEXT bar onwards.
            # Prices come from bar i only, so nothing here can see the future.
            if pos == 0 and rest_dir == 0:
                direction = 1 if long_sig[i] else (-1 if short_sig[i] else 0)
                if direction:
                    pending_atr = atr[i]
                    if entry_kind == "market":
                        pending = direction
                    elif entry_lvl is not None:
                        rest_px = entry_lvl[i]
                        rest_dir = direction if not np.isnan(rest_px) else 0
                        rest_left = expiry_bars
                    elif not np.isnan(atr[i]):
                        # limit rests at a BETTER price, stop at a WORSE one
                        away = entry_off * atr[i]
                        sign = -1 if entry_kind == "limit" else 1
                        rest_px = c[i] + direction * sign * away
                        rest_dir, rest_left = direction, expiry_bars

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
            # Per-side signal counts. A declared side that fires on zero bars is
            # one-directional in fact whatever the YAML says -- H-0010 passed the
            # whole gauntlet with an unreachable short branch.
            "signal_bars_long": int(long_sig.sum()),
            "signal_bars_short": int(short_sig.sum()),
        }


def compile_to_backtest(spec: StrategySpec | dict) -> CompiledStrategy:
    """Validate + compile a spec into a runnable backtest callable."""
    if isinstance(spec, dict):
        spec = validate_spec(spec)
    return CompiledStrategy(spec)


def direction_realized_errors(spec: StrategySpec, result: dict) -> list[str]:
    """Compare DECLARED direction against signals that actually fired.

    Static validation cannot see an unreachable branch: H-0010 declared a short
    signal on `trend_regime < 0`, but trend_regime is 0/1 by construction, so the
    branch could never be true. Only counting fired bars catches that.
    """
    errors: list[str] = []
    longs = int(result.get("signal_bars_long", 0))
    shorts = int(result.get("signal_bars_short", 0))
    direction = getattr(spec.alpha, "direction", "both")

    if direction in ("both", "long_only") and longs == 0:
        errors.append("alpha.signal never fires on this data (0 signal bars)")
    if direction == "both" and shorts == 0:
        errors.append(
            "alpha.direction is 'both' but alpha.signal_short never fires "
            "(0 signal bars) -- the short branch is unreachable, so this is "
            "long-only in fact. Fix the condition or declare direction: long_only."
        )
    if direction == "long_only" and shorts:
        errors.append(f"alpha.direction is 'long_only' but {shorts} short signal bars fired")
    return errors
