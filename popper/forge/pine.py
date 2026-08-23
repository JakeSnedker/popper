"""Deterministic StrategySpec to TradingView Pine v5 generator."""

import re

from popper.artifacts import StrategySpec
from popper.dsl.features import OPENING_RANGE_BARS, SESSIONS
from popper.dsl.features.levels import DEFAULT_SWING_N, INTRA_BAR_LEVELS, LEVEL_FEATURES
from popper.forge.expr import anchor_level, compile_expression, input_name

_SUPPORTED = {
    "atr_14", "session_ny", "rel_volume", "delta_proxy", "vol_regime",
    "trend_regime", "regime_score", "cusum_shift",
    # step-4: the plain price / session / momentum features
    "bb_line", "body_pressure", "candle_size", "opening_range", "psar",
    "rsi_fast", "session_asia", "session_london",
}
# Every level-feature renders as a series (tests/test_forge_levels.py proves each
# one numerically against popper.dsl.features.levels.compute_level on real bars).
_SUPPORTED_LEVELS = set(LEVEL_FEATURES)
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
    """The level a stop/target anchors to."""
    name = anchor_level(spec, rule.anchor)
    if name not in _SUPPORTED_LEVELS:
        raise PineUnsupported(f"Pine target does not implement level-feature {name!r} ({path})")
    return name


def _exit_series(name: str) -> str:
    """The Pine series an EXIT reads its anchor from, at the fill bar.

    Most levels at bar i are functions of bars <= i-1, so the fill bar's own
    value is already final and `level` is read as-is. bb_line and the two
    fractals are not: they read bar i's own OHLC. popper/dsl/compiler.py's
    `exit_anchor` shifts exactly those three (INTRA_BAR_LEVELS) one bar back
    before resolving an exit, so the emitted code reads `level[1]` -- the last
    fully-closed bar's value, which is what the backtest priced.

    ENTRY anchors are NOT shifted, here or in the compiler: a resting order is
    priced from the signal bar's close, when the value does exist.
    """
    return f"{name}[1]" if name in INTRA_BAR_LEVELS else name


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


def _plain_feature_lines(features: set[str]) -> list[str]:
    """The price / session / momentum features, transliterated from features/.

    tests/test_forge_features.py mirrors every line below back into Python and
    asserts bar-for-bar equality with compute_feature on real US30 M5 bars, so
    the warm-up rules and the fill-value quirks are checked numerically.
    """
    lines: list[str] = []
    if "candle_size" in features:
        lines.append("candle_size = high - low")
    if "body_pressure" in features:
        lines += [
            "// (close-open)/(high-low); a zero-range bar is undefined, not zero",
            "body_pressure = (high - low) == 0 ? na : (close - open) / (high - low)",
        ]
    if "bb_line" in features:
        lines.append("bb_line = ta.sma(close, 20)")
    if "rsi_fast" in features:
        lines += [
            "// Wilder RSI(7) as features/__init__.py::rsi_fast computes it. That seeds",
            "// ewm(alpha=1/7, adjust=False) on the FIRST close-to-close delta, where",
            "// ta.rsi seeds on an SMA -- different values, so this is not ta.rsi(close, 7).",
            "// Warm-up and a zero average loss both fall through to 50.0: the .fillna(50).",
            "rsi_delta = close - close[1]",
            "var float rsi_gain = na",
            "var float rsi_loss = na",
            "rsi_gain := na(rsi_delta) ? na : na(rsi_gain[1]) ? math.max(rsi_delta, 0.0)"
            " : rsi_gain[1] + (math.max(rsi_delta, 0.0) - rsi_gain[1]) / 7.0",
            "rsi_loss := na(rsi_delta) ? na : na(rsi_loss[1]) ? math.max(-rsi_delta, 0.0)"
            " : rsi_loss[1] + (math.max(-rsi_delta, 0.0) - rsi_loss[1]) / 7.0",
            "rsi_fast = bar_index >= 7 and rsi_loss > 0"
            " ? 100.0 - 100.0 / (1.0 + rsi_gain / rsi_loss) : 50.0",
        ]
    if "psar" in features:
        lines += [
            "// Parabolic SAR (af 0.02, step 0.02, max 0.2) as features/__init__.py::psar",
            "// runs it. ta.sar's flip and clamp rules differ, so the forward loop is",
            "// replayed here. Bar 0 has no SAR (the Python leaves out[0] NaN).",
            "var bool psar_up = true",
            "var float psar_af = 0.02",
            "var float psar_ep = na",
            "var float psar_sar = na",
            "var float psar_value = na",
            "if bar_index == 0",
            "    psar_sar := low",
            "    psar_ep := high",
            "else",
            "    psar_sar := psar_sar + psar_af * (psar_ep - psar_sar)",
            "    if psar_up",
            "        psar_sar := math.min(psar_sar,"
            " math.min(low[1], bar_index >= 2 ? low[2] : low[1]))",
            "        if low < psar_sar",
            "            psar_up := false",
            "            psar_sar := psar_ep",
            "            psar_ep := low",
            "            psar_af := 0.02",
            "        else if high > psar_ep",
            "            psar_ep := high",
            "            psar_af := math.min(psar_af + 0.02, 0.2)",
            "    else",
            "        psar_sar := math.max(psar_sar,"
            " math.max(high[1], bar_index >= 2 ? high[2] : high[1]))",
            "        if high > psar_sar",
            "            psar_up := true",
            "            psar_sar := psar_ep",
            "            psar_ep := high",
            "            psar_af := 0.02",
            "        else if low < psar_ep",
            "            psar_ep := low",
            "            psar_af := math.min(psar_af + 0.02, 0.2)",
            "    psar_value := psar_sar",
            "psar = psar_value",
        ]
    if "opening_range" in features:
        start, end = SESSIONS["ny"]
        n = OPENING_RANGE_BARS
        lines += [
            f"// range of the first {n} NY-session bars of the bar's own UTC day, published",
            "// only once all of them have printed and then held for the rest of that day",
            "or_utc_day = math.floor(time / 86400000)",
            f'or_in_ny = hour(time, "UTC") >= {start} and hour(time, "UTC") < {end}',
            "var int or_pos = -1",
            "var float or_hi = na",
            "var float or_lo = na",
            "var float or_range = na",
            "if or_utc_day != or_utc_day[1]",
            "    or_pos := -1",
            "    or_hi := na",
            "    or_lo := na",
            "    or_range := na",
            "if or_in_ny",
            "    or_pos += 1",
            f"    if or_pos < {n}",
            "        or_hi := na(or_hi) ? high : math.max(or_hi, high)",
            "        or_lo := na(or_lo) ? low : math.min(or_lo, low)",
            "        or_range := or_hi - or_lo",
            f"opening_range = or_pos >= {n - 1} ? or_range : na",
        ]
    return lines


def _level_lines(names: set[str], defined: set[str] = frozenset()) -> list[str]:
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
    if "bb_line" in names and "bb_line" not in defined:
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
    stop_lvl[i]/tgt_lvl[i] at the fill bar and never moves them afterwards).
    `_exit_series` supplies the same series the compiler's `exit_anchor` builds,
    so the value is final even during Pine's on-fill intrabar recalculation.
    """
    w = spec.wrapper
    if w.stop.type == "level":
        anchor = _exit_series(_exit_anchor(spec, w.stop, "wrapper.stop"))
        stop_expr = f"{anchor} + ({_wrapper_value(w.stop.offset)})"
    else:
        stop_expr = (
            "entry_price - dir_sign * nz(signal_atr, atr_14) * "
            f"{_wrapper_value(w.stop.mult)}"
        )
    if w.target.type == "level":
        anchor = _exit_series(_exit_anchor(spec, w.target, "wrapper.target"))
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
    # ny first so a session_ny-only spec keeps emitting exactly what it always did
    for session in ("ny", "asia", "london"):
        if f"session_{session}" in features:
            start, end = SESSIONS[session]
            lines.append(f"session_{session} = hour(time, \"UTC\") >= {start} and "
                         f"hour(time, \"UTC\") < {end} ? 1.0 : 0.0")
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

    lines += _plain_feature_lines(features)
    lines += _level_lines(_spec_levels(spec), features)

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
