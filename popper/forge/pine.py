"""Deterministic StrategySpec to TradingView Pine v5 generator."""

import re

from popper.artifacts import StrategySpec
from popper.forge.expr import compile_expression, input_name

_SUPPORTED = {
    "atr_14", "session_ny", "rel_volume", "delta_proxy", "vol_regime",
    "trend_regime", "regime_score", "cusum_shift",
}
_EMA = re.compile(r"^ema_(\d+)$")
_PARAM = re.compile(r"^p\[(\w+)\]$")


class PineUnsupported(ValueError):
    pass


def _number(value) -> str:
    return f"{float(value):.10g}"


def _middle(grid: list):
    return grid[len(grid) // 2]


def _wrapper_value(value, target: str = "pine") -> str:
    match = _PARAM.match(value) if isinstance(value, str) else None
    return input_name(match.group(1), target) if match else _number(value)


def _validate(spec: StrategySpec) -> None:
    if spec.alpha.higher_timeframe_features:
        raise PineUnsupported("Pine target does not yet implement causal higher-timeframe features")
    unsupported = sorted(feature for feature in spec.alpha.features
                         if feature not in _SUPPORTED and not _EMA.match(feature))
    if unsupported:
        raise PineUnsupported(f"Pine target does not implement features: {', '.join(unsupported)}")
    if spec.wrapper.stop.type != "atr" or spec.wrapper.target.type != "ratio":
        raise PineUnsupported("Pine target currently requires ATR stop and ratio target")
    if spec.wrapper.trail.type != "none" or spec.wrapper.break_even.type != "none":
        raise PineUnsupported("Pine target does not yet support trail or break-even wrappers")
    if spec.wrapper.constraints.no_trade:
        raise PineUnsupported("Pine target does not yet support no_trade constraints")


def _reject_unsupported(spec) -> None:
    """Refuse to emit code that would trade differently from the backtest.

    Offset-priced resting entries and time exits are rendered below. An ANCHORED
    resting order is not: its price is a level-feature series, and Pine
    implements no level features at all -- the same reason `_validate` rejects a
    level stop or target. Falling back to an offset price would deploy a
    strategy nobody tested.
    """
    entry = spec.wrapper.entry
    if entry.type != "market" and entry.anchor:
        raise PineUnsupported(
            f"Pine target cannot price a {entry.type} entry off level-feature "
            f"{entry.anchor!r}: level features are not implemented for this target"
        )


def _resting_entry_lines(entry) -> list[str]:
    """A limit/stop order priced off the SIGNAL bar and live from the next one.

    Mirrors the compiler: a limit rests at a BETTER price than the signal close
    and a stop at a WORSE one; the order is cancelled once it has rested
    `expiry_bars` bars; no new signal is taken while one is resting.
    """
    offset = _wrapper_value(entry.offset)
    expiry = int(entry.expiry_bars)
    keyword = "limit" if entry.type == "limit" else "stop"
    long_sign, short_sign = ("-", "+") if entry.type == "limit" else ("+", "-")
    return [
        "var float signal_atr = na",
        "var int rest_dir = 0",
        "var int rest_bars = 0",
        "var int filled_trades = 0",
        "// a fill (or a trade opened and closed inside one bar) frees the slot; any",
        "// other bar ages the resting order one step toward its expiry",
        "total_trades = strategy.opentrades + strategy.closedtrades",
        "if total_trades != filled_trades",
        "    filled_trades := total_trades",
        "    if rest_dir != 0",
        "        entries_today += 1",
        "    rest_dir := 0",
        "    rest_bars := 0",
        "else if rest_dir != 0",
        "    rest_bars += 1",
        f"    if rest_bars >= {expiry}",
        '        strategy.cancel("Long")',
        '        strategy.cancel("Short")',
        "        rest_dir := 0",
        "        rest_bars := 0",
        "can_enter = strategy.position_size == 0 and rest_dir == 0 and (max_trades_per_day == 0 or entries_today < max_trades_per_day)",
        "if can_enter and long_signal and not na(atr_14)",
        "    signal_atr := atr_14",
        "    rest_dir := 1",
        "    rest_bars := 0",
        f'    strategy.entry("Long", strategy.long, {keyword}=close {long_sign} {offset} * atr_14)',
        "else if can_enter and short_signal and not na(atr_14)",
        "    signal_atr := atr_14",
        "    rest_dir := -1",
        "    rest_bars := 0",
        f'    strategy.entry("Short", strategy.short, {keyword}=close {short_sign} {offset} * atr_14)',
    ]


def generate_pine(spec: StrategySpec) -> str:
    _reject_unsupported(spec)
    _validate(spec)
    title = re.sub(r"[^A-Za-z0-9 _.-]", "", spec.name)[:80]
    lines = [
        "//@version=5",
        f'strategy("Popper | {title}", overlay=true, pyramiding=0,',
        "  process_orders_on_close=false, calc_on_order_fills=true,",
        f"  default_qty_type=strategy.percent_of_equity, default_qty_value={_number(spec.wrapper.sizing.risk_pct)})",
        "",
        "// Generated from a falsified Popper StrategySpec. Validate against Python before live use.",
        f"// hypothesis_id={spec.hypothesis_id} spec_id={spec.id or 'unstored'} dsl={spec.dsl_version}",
        "// Session timestamps assume UTC. Tick-volume and CUSUM parity depend on the broker feed.",
        "",
    ]
    for name, grid in sorted(spec.param_space.items()):
        default = _middle(grid)
        kind = "int" if isinstance(default, int) and not isinstance(default, bool) else "float"
        lines.append(
            f'{input_name(name, "pine")} = input.{kind}({_number(default)}, "{name}")'
        )
    max_trades = spec.wrapper.constraints.max_trades_per_day or 0
    lines += [f"max_trades_per_day = input.int({max_trades}, \"max trades/day (0=unlimited)\", minval=0)", ""]

    features = set(spec.alpha.features)
    if "atr_14" in features or spec.wrapper.stop.type == "atr":
        lines.append("atr_14 = ta.atr(14)")
    for feature in sorted(features):
        match = _EMA.match(feature)
        if match:
            lines.append(f"{feature} = ta.ema(close, {int(match.group(1))})")
    if "session_ny" in features:
        lines.append("session_ny = hour(time, \"UTC\") >= 13 and hour(time, \"UTC\") < 21 ? 1.0 : 0.0")
    if {"rel_volume", "delta_proxy"} & features:
        lines.append("rel_volume = nz(volume, 1.0) / ta.sma(nz(volume, 1.0), 20)[1]")
    if "delta_proxy" in features:
        lines.append("delta_proxy = math.sign(close - open) * rel_volume")
    if "vol_regime" in features:
        lines += [
            "atr_rank = ta.percentrank(atr_14, 500) / 100.0",
            "vol_regime = atr_rank >= 0.70 ? 1.0 : atr_rank <= 0.30 ? -1.0 : 0.0",
        ]
    if {"trend_regime", "regime_score"} & features:
        lines += ["ema_48_forge = ta.ema(close, 48)", "ema_240_forge = ta.ema(close, 240)"]
    if "trend_regime" in features:
        lines.append("trend_regime = math.abs(ema_48_forge - ema_240_forge) / atr_14 >= 0.5 ? 1.0 : 0.0")
    if "regime_score" in features:
        lines += [
            "ema_20_forge = ta.ema(close, 20)",
            "ema_50_forge = ta.ema(close, 50)",
            "rsi_7_forge = ta.rsi(close, 7)",
            "psar_forge = ta.sar(0.02, 0.02, 0.2)",
            "regime_score = (math.sign(ema_48_forge - ema_240_forge) + math.sign(close - ema_50_forge) + math.sign(ema_20_forge - ema_50_forge) + math.sign(rsi_7_forge - 50.0) + math.sign(close - psar_forge)) / 5.0",
        ]
    if "cusum_shift" in features:
        lines += [
            "cusum_ret = ta.change(close)",
            "cusum_vol = ta.stdev(cusum_ret, 50, false)",
            "cusum_z = cusum_vol > 0 ? cusum_ret / cusum_vol : 0.0",
            "var float cusum_pos = 0.0",
            "var float cusum_neg = 0.0",
            "cusum_pos := math.max(0.0, nz(cusum_pos[1]) + cusum_z - 0.5)",
            "cusum_neg := math.max(0.0, nz(cusum_neg[1]) - cusum_z - 0.5)",
            "cusum_shift = cusum_pos - cusum_neg",
        ]

    long_signal = compile_expression(spec.alpha.signal, "pine")
    short_signal = compile_expression(spec.alpha.signal_short, "pine") if spec.alpha.signal_short else "false"
    stop_mult = _wrapper_value(spec.wrapper.stop.mult)
    rr = _wrapper_value(spec.wrapper.target.ratio)
    entry = spec.wrapper.entry
    lines += [
        "",
        f"long_signal = {long_signal}",
        f"short_signal = {short_signal}",
        "new_day = ta.change(time(\"D\")) != 0",
        "var int entries_today = 0",
        "if new_day",
        "    entries_today := 0",
    ]
    if entry.type == "market":
        lines += [
            "can_enter = strategy.position_size == 0 and (max_trades_per_day == 0 or entries_today < max_trades_per_day)",
            "var float signal_atr = na",
            "if can_enter and long_signal",
            "    signal_atr := atr_14",
            "    entries_today += 1",
            '    strategy.entry("Long", strategy.long)',
            "else if can_enter and short_signal",
            "    signal_atr := atr_14",
            "    entries_today += 1",
            '    strategy.entry("Short", strategy.short)',
        ]
    else:
        lines += _resting_entry_lines(entry)
    lines += [
        "",
        # signal_atr is the SIGNAL bar's ATR, so a resting order that fills bars
        # later still risks the distance the hypothesis was written with
        f"stop_distance = nz(signal_atr, atr_14) * {stop_mult}",
        f"reward_distance = stop_distance * {rr}",
        "if strategy.position_size > 0",
        '    strategy.exit("Long exit", "Long", stop=strategy.position_avg_price - stop_distance, limit=strategy.position_avg_price + reward_distance)',
        "if strategy.position_size < 0",
        '    strategy.exit("Short exit", "Short", stop=strategy.position_avg_price + stop_distance, limit=strategy.position_avg_price - reward_distance)',
        "",
    ]
    if spec.wrapper.time_stop.type == "bars":
        lines += [
            # floor to whole bars: the compiler takes int(max_bars)
            f"max_bars_in_trade = math.floor({_wrapper_value(spec.wrapper.time_stop.max_bars)})",
            "// after the stop/target above, which run intrabar, so a bar that breaches",
            "// the stop AND expires the clock is recorded as the stop",
            "if strategy.position_size != 0 and (bar_index - strategy.opentrades.entry_bar_index(0)) >= max_bars_in_trade",
            '    strategy.close_all(comment="time", immediately=true)',
            "",
        ]
    return "\n".join(lines)
