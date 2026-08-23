"""Deterministic StrategySpec to TradingView Pine v5 generator."""

import re

from popper.artifacts import StrategySpec
from popper.dsl.features import SESSIONS
from popper.dsl.features.levels import DEFAULT_SWING_N, LEVEL_FEATURES
from popper.forge.expr import anchor_level, compile_expression, input_name

_SUPPORTED = {
    "atr_14", "session_ny", "rel_volume", "delta_proxy", "vol_regime",
    "trend_regime", "regime_score", "cusum_shift",
}
# Every level-feature renders as a series (tests/test_forge_levels.py proves each
# one numerically against popper.dsl.features.levels.compute_level on real bars).
_SUPPORTED_LEVELS = set(LEVEL_FEATURES)

# ...but only THESE may anchor a stop or a target. The compiler resolves an exit
# anchor at the FILL bar (`stop_lvl[i]` in popper/dsl/compiler.py, i = the bar the
# trade opens on) and states the invariant "level[i] uses bars <= i-1, all complete
# at bar i's open". That invariant holds for the swing and session levels; it does
# NOT hold for prior_fractal_hi/lo (confirmation reads bar i's own high/low) or
# bb_line (SMA includes bar i's close). Those three are only knowable at bar i's
# CLOSE, so no live target can price an exit off them at the fill -- they stay
# refused rather than silently rendered one bar stale.
_EXIT_LEVELS = frozenset(
    LEVEL_FEATURES - {"prior_fractal_hi", "prior_fractal_lo", "bb_line"}
)
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


def _exit_anchor(spec: StrategySpec, rule, path: str) -> str:
    """The level a stop/target anchors to, refusing what Pine cannot price."""
    name = anchor_level(spec, rule.anchor)
    if name not in _SUPPORTED_LEVELS:
        raise PineUnsupported(f"Pine target does not implement level-feature {name!r} ({path})")
    if name not in _EXIT_LEVELS:
        raise PineUnsupported(
            f"Pine target cannot anchor {path} to level-feature {name!r}: its value on "
            "bar i depends on bar i's own OHLC, but the backtest resolves an exit anchor "
            "at the fill bar, so the price is not knowable when the trade opens"
        )
    return name


def _entry_anchor(spec: StrategySpec, name: str) -> str:
    resolved = anchor_level(spec, name)
    if resolved not in _SUPPORTED_LEVELS:
        raise PineUnsupported(
            f"Pine target does not implement level-feature {resolved!r} "
            "(wrapper.entry.anchor)"
        )
    return resolved


def _spec_levels(spec: StrategySpec) -> set[str]:
    """Every level-feature this spec needs rendered as a series."""
    names = set()
    w = spec.wrapper
    if w.stop.type == "level":
        names.add(_exit_anchor(spec, w.stop, "wrapper.stop"))
    if w.target.type == "level":
        names.add(_exit_anchor(spec, w.target, "wrapper.target"))
    if w.entry.type != "market" and w.entry.anchor:
        names.add(_entry_anchor(spec, w.entry.anchor))
    return names


def _validate(spec: StrategySpec) -> None:
    if spec.alpha.higher_timeframe_features:
        raise PineUnsupported("Pine target does not yet implement causal higher-timeframe features")
    unsupported = sorted(feature for feature in spec.alpha.features
                         if feature not in _SUPPORTED and not _EMA.match(feature))
    if unsupported:
        raise PineUnsupported(f"Pine target does not implement features: {', '.join(unsupported)}")
    if spec.wrapper.stop.type not in ("atr", "level"):
        raise PineUnsupported(f"Pine target does not implement stop type {spec.wrapper.stop.type!r}")
    if spec.wrapper.target.type not in ("ratio", "level"):
        raise PineUnsupported(f"Pine target does not implement target type {spec.wrapper.target.type!r}")
    if spec.wrapper.trail.type != "none" or spec.wrapper.break_even.type != "none":
        raise PineUnsupported("Pine target does not yet support trail or break-even wrappers")
    if spec.wrapper.constraints.no_trade:
        raise PineUnsupported("Pine target does not yet support no_trade constraints")


def _reject_unsupported(spec) -> None:
    """Refuse to emit code that would trade differently from the backtest.

    A level exit combined with a RESTING entry is the one combination left out:
    the compiler measures 1R from the fill bar's level, and a broker needs the
    order volume when the order is placed, bars before that level is known.
    Sizing off the placement-bar level instead would deploy a position size
    nobody backtested.
    """
    w = spec.wrapper
    level_exit = w.stop.type == "level" or w.target.type == "level"
    if level_exit and w.entry.type != "market":
        raise PineUnsupported(
            f"Pine target cannot combine a level exit with a {w.entry.type} entry: "
            "the risk distance is only known at the fill, after the order is placed"
        )
    _spec_levels(spec)  # raises on any anchor this target cannot render


def _level_lines(names: set[str]) -> list[str]:
    """Render each level-feature exactly as popper/dsl/features/levels.py computes it.

    tests/test_forge_levels.py mirrors these lines back into Python and asserts
    bar-for-bar equality with compute_level on real US30 M5 bars -- the lag is
    the whole point, so it is checked numerically rather than by eye.
    """
    if not names:
        return []
    n = DEFAULT_SWING_N
    swing = {
        "prior_swing_wick_hi": f"ta.highest(high, {n})[1]",
        "prior_swing_wick_lo": f"ta.lowest(low, {n})[1]",
        "prior_swing_body_hi": f"ta.highest(math.max(open, close), {n})[1]",
        "prior_swing_body_lo": f"ta.lowest(math.min(open, close), {n})[1]",
    }
    lines = ["", "// --- level features (popper/dsl/features/levels.py) ---"]
    if names & set(swing):
        lines.append(f"// [1] is the shift(1): the extreme of the PRIOR {n} bars, never this one")
    for name in sorted(names & set(swing)):
        lines.append(f"{name} = {swing[name]}")
    for name in sorted(names & {"prior_fractal_hi", "prior_fractal_lo"}):
        src, cmp_ = ("high", ">") if name.endswith("_hi") else ("low", "<")
        lines += [
            "// a 5-bar Bill Williams fractal is only CONFIRMED two bars later, so the",
            "// candidate is bar [2] and `var` carries the last one forward (the ffill)",
            f"var float {name} = na",
            f"if {src}[2] {cmp_} {src}[1] and {src}[2] {cmp_} {src}[0]"
            f" and {src}[2] {cmp_} {src}[3] and {src}[2] {cmp_} {src}[4]",
            f"    {name} := {src}[2]",
        ]
    sessions = sorted({name.split("_")[1] for name in names if name.startswith("session_")})
    if sessions:
        lines += [
            "// levels.py groups by UTC calendar date; `time` is the bar's UTC epoch ms",
            "lvl_utc_day = math.floor(time / 86400000)",
            "lvl_new_day = lvl_utc_day != lvl_utc_day[1]",
        ]
    for session in sessions:
        start, end = SESSIONS[session]
        lines.append(
            f'lvl_in_{session} = hour(time, "UTC") >= {start} and hour(time, "UTC") < {end}'
        )
        for side in ("hi", "lo"):
            name = f"session_{session}_{side}"
            if name not in names:
                continue
            acc = f"lvl_{session}_{side}_acc"
            src = "high" if side == "hi" else "low"
            pick = "math.max" if side == "hi" else "math.min"
            lines += [
                f"var float {acc} = na",
                f"var float {name} = na",
                "// publish the PREVIOUS day's extreme on the day's first bar, then start a",
                "// fresh accumulator -- the current day's session is never readable",
                "if lvl_new_day",
                f"    {name} := {acc}",
                f"    {acc} := na",
                f"if lvl_in_{session}",
                f"    {acc} := na({acc}) ? {src} : {pick}({acc}, {src})",
            ]
    if "bb_line" in names:
        lines.append("bb_line = ta.sma(close, 20)")
    return lines


def _resting_entry_lines(entry, anchor: str | None = None) -> list[str]:
    """A limit/stop order priced off the SIGNAL bar and live from the next one.

    Mirrors the compiler: a limit rests at a BETTER price than the signal close
    and a stop at a WORSE one; the order is cancelled once it has rested
    `expiry_bars` bars; no new signal is taken while one is resting.

    An ANCHORED order rests at the level-feature's value on the signal bar
    instead. The compiler reads `entry_lvl[i]` there and applies NO offset, so
    neither does this -- and a signal whose anchor has not formed is dropped,
    exactly as `rest_dir` stays 0 for a NaN anchor.
    """
    offset = _wrapper_value(entry.offset)
    expiry = int(entry.expiry_bars)
    keyword = "limit" if entry.type == "limit" else "stop"
    long_sign, short_sign = ("-", "+") if entry.type == "limit" else ("+", "-")
    if anchor:
        armed = f"not na({anchor})"
        long_price = short_price = anchor
    else:
        armed = "not na(atr_14)"
        long_price = f"close {long_sign} {offset} * atr_14"
        short_price = f"close {short_sign} {offset} * atr_14"
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
        f"if can_enter and long_signal and {armed}",
        "    signal_atr := atr_14",
        "    rest_dir := 1",
        "    rest_bars := 0",
        f'    strategy.entry("Long", strategy.long, {keyword}={long_price})',
        f"else if can_enter and short_signal and {armed}",
        "    signal_atr := atr_14",
        "    rest_dir := -1",
        "    rest_bars := 0",
        f'    strategy.entry("Short", strategy.short, {keyword}={short_price})',
    ]


def _level_exit_lines(spec: StrategySpec) -> list[str]:
    """Freeze the stop/target on the bar the position OPENED, from that bar's levels.

    That is where popper/dsl/compiler.py resolves them (`try_open` reads
    stop_lvl[i]/tgt_lvl[i] at the fill bar and never moves them afterwards), and
    the levels allowed here are functions of bars 0..i-1, so the value is final
    even during Pine's on-fill intrabar recalculation.
    """
    w = spec.wrapper
    if w.stop.type == "level":
        anchor = _exit_anchor(spec, w.stop, "wrapper.stop")
        stop_expr = f"{anchor} + ({_wrapper_value(w.stop.offset)})"
    else:
        stop_expr = (
            "entry_price - dir_sign * nz(signal_atr, atr_14) * "
            f"{_wrapper_value(w.stop.mult)}"
        )
    if w.target.type == "level":
        anchor = _exit_anchor(spec, w.target, "wrapper.target")
        target_expr = f"{anchor} + ({_wrapper_value(w.target.offset)})"
    else:
        target_expr = (
            "entry_price + dir_sign * math.abs(entry_price - new_stop) * "
            f"{_wrapper_value(w.target.ratio)}"
        )
    return [
        "",
        "in_long = strategy.position_size > 0",
        "entry_price = strategy.position_avg_price",
        "dir_sign = in_long ? 1.0 : -1.0",
        "var float lvl_stop = na",
        "var float lvl_target = na",
        "if strategy.position_size == 0",
        "    lvl_stop := na",
        "    lvl_target := na",
        "else if na(lvl_stop)",
        f"    float new_stop = {stop_expr}",
        f"    float new_target = {target_expr}",
        "    // the backtest SKIPS an entry whose anchor has not formed or sits on the",
        "    // wrong side of it. Live, the fill already happened, so flatten at once",
        "    // rather than hold a position the hypothesis never took.",
        "    bool coherent = not na(new_stop) and not na(new_target) and"
        " (in_long ? new_stop < entry_price and new_target > entry_price"
        " : new_stop > entry_price and new_target < entry_price)",
        "    if coherent",
        "        lvl_stop := new_stop",
        "        lvl_target := new_target",
        "    else",
        '        strategy.close_all(comment="no level", immediately=true)',
        "if strategy.position_size > 0 and not na(lvl_stop)",
        '    strategy.exit("Long exit", "Long", stop=lvl_stop, limit=lvl_target)',
        "if strategy.position_size < 0 and not na(lvl_stop)",
        '    strategy.exit("Short exit", "Short", stop=lvl_stop, limit=lvl_target)',
        "",
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
        if isinstance(default, str):
            continue  # a categorical (an anchor sweep): resolved to a name, not an input
        kind = "int" if isinstance(default, int) and not isinstance(default, bool) else "float"
        lines.append(
            f'{input_name(name, "pine")} = input.{kind}({_number(default)}, "{name}")'
        )
    max_trades = spec.wrapper.constraints.max_trades_per_day or 0
    lines += [f"max_trades_per_day = input.int({max_trades}, \"max trades/day (0=unlimited)\", minval=0)", ""]

    features = set(spec.alpha.features)
    # unconditional: the entry blocks below latch signal_atr whatever the stop type
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

    lines += _level_lines(_spec_levels(spec))

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
        lines += _resting_entry_lines(
            entry, _entry_anchor(spec, entry.anchor) if entry.anchor else None
        )
    if spec.wrapper.stop.type == "level" or spec.wrapper.target.type == "level":
        lines += _level_exit_lines(spec)
    else:
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
