"""Deterministic StrategySpec to TradingView Pine v5 generator."""

import re

from popper.artifacts import StrategySpec
from popper.dsl.features import (
    CISD_RUN,
    OB_DISP_MULT,
    OPENING_RANGE_BARS,
    SESSIONS,
    SWING_LOOKBACK,
)
from popper.dsl.features.levels import DEFAULT_SWING_N, INTRA_BAR_LEVELS, LEVEL_FEATURES
from popper.forge.expr import anchor_level, compile_expression, input_name, smc_knob

_SUPPORTED = {
    "atr_14", "session_ny", "rel_volume", "delta_proxy", "vol_regime",
    "trend_regime", "regime_score", "cusum_shift",
    # step-4: the plain price / session / momentum features
    "bb_line", "body_pressure", "candle_size", "opening_range", "psar",
    "rsi_fast", "session_asia", "session_london",
    # step-5: window features (no structural pivots)
    "fvg", "htf_trend", "liquidity_sweep",
    # step-6: the SMC pack, built on the shared confirmed-swing pivots
    "bos", "cisd", "equilibrium", "fib_pos", "mss", "ob_bear_mid", "ob_bull_mid",
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
                         if feature not in _SUPPORTED and not _EMA.match(feature)
                         and smc_knob(feature) is None)
    if unsupported:
        raise PineUnsupported(f"Pine target does not implement features: {', '.join(unsupported)}")
    if spec.wrapper.stop.type not in ("atr", "level"):
        raise PineUnsupported(f"Pine target does not implement stop type {spec.wrapper.stop.type!r}")
    if spec.wrapper.target.type not in ("ratio", "level"):
        raise PineUnsupported(f"Pine target does not implement target type {spec.wrapper.target.type!r}")
    if spec.wrapper.trail.type not in ("none", "atr"):
        raise PineUnsupported(f"Pine target does not implement trail type {spec.wrapper.trail.type!r}")
    if spec.wrapper.break_even.type not in ("none", "after_r"):
        raise PineUnsupported(
            f"Pine target does not implement break-even type {spec.wrapper.break_even.type!r}")
    if spec.wrapper.constraints.no_trade:
        # NOT a Forge gap: popper/dsl/compiler.py never reads no_trade, so the
        # backtest traded straight through those windows. An EA that honoured the
        # tag would trade a different strategy from the one that was falsified.
        raise PineUnsupported(
            "Pine target refuses constraints.no_trade: the backtest does not enforce it "
            "(needs the news calendar), so emitting it would deploy a strategy that "
            "trades differently from the one that passed the gauntlet")


def _reject_unsupported(spec) -> None:
    """Refuse to emit code that would trade differently from the backtest."""
    _spec_levels(spec)  # raises on any anchor this target cannot render


def _placement_latch(spec: StrategySpec) -> list[tuple[str, str]]:
    """(var, expression) pairs a RESTING order latches on the bar it is PLACED.

    popper/dsl/compiler.py resolves a level stop/target from `rest_anchor_i` --
    the bar the order was placed on, not the bar it filled (`try_open`'s
    `anchor_i`). A broker fixes order volume when the order is sent, so 1R has to
    be settled there; a level re-read at the fill would be a different number and
    so a different position size from the one that was backtested.

    The expressions are the same ones an immediate entry uses, evaluated on the
    signal bar -- so an INTRA_BAR_LEVEL exit anchor is still read one bar back
    (`_exit_series`) while an ENTRY anchor on that same bar is not.
    """
    w = spec.wrapper
    if w.entry.type == "market":
        return []
    latch = []
    if w.stop.type == "level":
        anchor = _exit_series(_exit_anchor(spec, w.stop, "wrapper.stop"))
        latch.append(("signal_stop_lvl", f"{anchor} + ({_wrapper_value(w.stop.offset)})"))
    if w.target.type == "level":
        anchor = _exit_series(_exit_anchor(spec, w.target, "wrapper.target"))
        latch.append(("signal_target_lvl", f"{anchor} + ({_wrapper_value(w.target.offset)})"))
    return latch


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
    if "fvg" in features:
        lines += [
            "// Fair Value Gap presence. features/__init__.py compares against NaN during",
            "// warm-up, which is False, so the first two bars are 0.0 and not na.",
            "fvg = (not na(high[2]) and high[2] < low ? 1.0 : 0.0)"
            " - (not na(low[2]) and low[2] > high ? 1.0 : 0.0)",
        ]
    if "liquidity_sweep" in features:
        lines += [
            "// [1] is the .shift(1) on the rolling window: this bar is never part of the",
            "// baseline it has to pierce. Warm-up compares against NaN, so it is 0.0.",
            "ls_prior_lo = ta.lowest(low, 20)[1]",
            "ls_prior_hi = ta.highest(high, 20)[1]",
            "liquidity_sweep = (not na(ls_prior_lo) and low < ls_prior_lo"
            " and close > ls_prior_lo ? 1.0 : 0.0)"
            " - (not na(ls_prior_hi) and high > ls_prior_hi"
            " and close < ls_prior_hi ? 1.0 : 0.0)",
        ]
    if "htf_trend" in features:
        lines += [
            "// features/__init__.py::htf_trend is sign(ema(48) - ema(240)) where ema() is",
            "// ewm(span, adjust=False) seeded on the FIRST close. ta.ema seeds on an SMA,",
            "// and at span 240 the two disagree for thousands of M5 bars, so both legs are",
            "// re-derived here. np.sign(nan) is nan, hence the bar_index guard.",
            "var float htf_ema_48 = na",
            "htf_ema_48 := na(htf_ema_48[1]) ? close"
            " : htf_ema_48[1] + (close - htf_ema_48[1]) * (2.0 / 49.0)",
            "var float htf_ema_240 = na",
            "htf_ema_240 := na(htf_ema_240[1]) ? close"
            " : htf_ema_240[1] + (close - htf_ema_240[1]) * (2.0 / 241.0)",
            "htf_trend = bar_index >= 239 ? math.sign(htf_ema_48 - htf_ema_240) : na",
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


def _pivot_lines(lookback: int) -> list[str]:
    """features/__init__.py::_swing_pivots for one lookback, unrolled.

    The centre is bar [L] and is only confirmable once L bars have printed on
    each side of it, so it is read L bars back -- that delay is what keeps the
    pivot causal. `var` carries the last confirmed one forward (the ffill).
    The comparison chain is unrolled rather than looped because a Pine series
    offset must be a constant for the compiler to size its history buffer.
    """
    n = lookback
    def chain(src: str, cmp_: str) -> str:
        return " and ".join(
            f"{src}[{n - k}] {cmp_} {src}[{n}]" for k in range(1, n + 1)
        ) + " and " + " and ".join(
            f"{src}[{n + k}] {cmp_} {src}[{n}]" for k in range(1, n + 1)
        )
    return [
        f"// confirmed swing pivots, lookback {n} "
        "(features/__init__.py::_swing_pivots)",
        f"var float pop_swing_hi_{n} = na",
        f"var float pop_swing_lo_{n} = na",
        f"if bar_index >= {2 * n} and ({chain('high', '<')})",
        f"    pop_swing_hi_{n} := high[{n}]",
        f"if bar_index >= {2 * n} and ({chain('low', '>')})",
        f"    pop_swing_lo_{n} := low[{n}]",
    ]


def _pop_atr_lines() -> list[str]:
    """features/__init__.py::atr_14, re-derived rather than taken from ta.atr.

    ta.atr seeds Wilder smoothing on an SMA of the first 14 true ranges; the
    Python seeds on the first one. The order block's displacement test is a
    threshold on this value, so the two seeds would arm different candles.
    """
    return [
        "// exact atr_14 for the order-block displacement test (NOT ta.atr: different seed)",
        "pop_true_range = bar_index == 0 ? high - low : math.max(high - low,"
        " math.max(math.abs(high - close[1]), math.abs(low - close[1])))",
        "var float pop_atr_raw = na",
        "pop_atr_raw := na(pop_atr_raw[1]) ? pop_true_range"
        " : pop_atr_raw[1] + (pop_true_range - pop_atr_raw[1]) / 14.0",
        "pop_atr_14 = bar_index >= 13 ? pop_atr_raw : na",
    ]


def _bos_lines(n: int) -> list[str]:
    """features/__init__.py::bos -- a fresh-break EVENT, so the previous bar's
    state is recomputed from [1] rather than carried in a bool series."""
    return [
        f"pop_bos_up_{n} = na(pop_swing_hi_{n}) ? false : close > pop_swing_hi_{n}",
        f"pop_bos_dn_{n} = na(pop_swing_lo_{n}) ? false : close < pop_swing_lo_{n}",
        f"pop_bos_up_prev_{n} = na(pop_swing_hi_{n}[1]) ? false"
        f" : close[1] > pop_swing_hi_{n}[1]",
        f"pop_bos_dn_prev_{n} = na(pop_swing_lo_{n}[1]) ? false"
        f" : close[1] < pop_swing_lo_{n}[1]",
        f"pop_bos_{n} = bar_index < 1 ? 0.0"
        f" : pop_bos_up_{n} and not pop_bos_up_prev_{n} ? 1.0"
        f" : pop_bos_dn_{n} and not pop_bos_dn_prev_{n} ? -1.0 : 0.0",
    ]


def _mss_lines(name: str, n: int) -> list[str]:
    return [
        "// the first BOS whose direction OPPOSES the previous BOS (a structural flip)",
        f"var float pop_mss_prev_{n} = 0.0",
        f"float {name} = 0.0",
        f"if pop_bos_{n} != 0.0",
        f"    {name} := pop_mss_prev_{n} != 0.0 and pop_bos_{n} != pop_mss_prev_{n}"
        f" ? pop_bos_{n} : 0.0",
        f"    pop_mss_prev_{n} := pop_bos_{n}",
    ]


def _order_block_lines(n: int) -> list[str]:
    """features/__init__.py::_order_blocks, one state machine feeding both mids."""
    disp = _number(OB_DISP_MULT)
    return [
        "// nearest UNMITIGATED order block: the last opposite-close candle before a",
        f"// displacing move (breaks the confirmed swing, or a body >= {disp} * ATR),",
        "// cleared once price fully sweeps back through the zone",
        f"var float pop_ob_bull_lo_{n} = na",
        f"var float pop_ob_bull_hi_{n} = na",
        f"var float pop_ob_bear_lo_{n} = na",
        f"var float pop_ob_bear_hi_{n} = na",
        f"var float pop_ob_down_lo_{n} = na",
        f"var float pop_ob_down_hi_{n} = na",
        f"var float pop_ob_up_lo_{n} = na",
        f"var float pop_ob_up_hi_{n} = na",
        f"float pop_ob_bull_mid_{n} = na",
        f"float pop_ob_bear_mid_{n} = na",
        f"if close > open and not na(pop_ob_down_lo_{n})",
        f"    if (not na(pop_swing_hi_{n}) and close > pop_swing_hi_{n})"
        f" or (not na(pop_atr_14) and (close - open) >= {disp} * pop_atr_14)",
        f"        pop_ob_bull_lo_{n} := pop_ob_down_lo_{n}",
        f"        pop_ob_bull_hi_{n} := pop_ob_down_hi_{n}",
        f"if close < open and not na(pop_ob_up_lo_{n})",
        f"    if (not na(pop_swing_lo_{n}) and close < pop_swing_lo_{n})"
        f" or (not na(pop_atr_14) and (open - close) >= {disp} * pop_atr_14)",
        f"        pop_ob_bear_lo_{n} := pop_ob_up_lo_{n}",
        f"        pop_ob_bear_hi_{n} := pop_ob_up_hi_{n}",
        f"if not na(pop_ob_bull_lo_{n}) and low < pop_ob_bull_lo_{n}",
        f"    pop_ob_bull_lo_{n} := na",
        f"    pop_ob_bull_hi_{n} := na",
        f"if not na(pop_ob_bear_hi_{n}) and high > pop_ob_bear_hi_{n}",
        f"    pop_ob_bear_lo_{n} := na",
        f"    pop_ob_bear_hi_{n} := na",
        f"if not na(pop_ob_bull_lo_{n})",
        f"    pop_ob_bull_mid_{n} := (pop_ob_bull_hi_{n} + pop_ob_bull_lo_{n}) / 2.0",
        f"if not na(pop_ob_bear_hi_{n})",
        f"    pop_ob_bear_mid_{n} := (pop_ob_bear_hi_{n} + pop_ob_bear_lo_{n}) / 2.0",
        "// the candidate candles are tracked separately from the bar being judged, and",
        "// an up-close bar can only arm a BULL block from a previous DOWN-close candle,",
        "// so a candle is never its own order block",
        "if close < open",
        f"    pop_ob_down_lo_{n} := low",
        f"    pop_ob_down_hi_{n} := high",
        "else if close > open",
        f"    pop_ob_up_lo_{n} := low",
        f"    pop_ob_up_hi_{n} := high",
    ]


def _cisd_lines(name: str, run: int) -> list[str]:
    """features/__init__.py::cisd -- arm the run's OPEN, fire on the cross back."""
    return [
        f"// Change in State of Delivery, run {run}",
        f"var float pop_cisd_arm_dn_{run} = na",
        f"var float pop_cisd_arm_up_{run} = na",
        f"var int pop_cisd_dir_{run} = 0",
        f"var int pop_cisd_len_{run} = 0",
        f"var float pop_cisd_open_{run} = na",
        f"float {name} = 0.0",
        f"if not na(pop_cisd_arm_dn_{run}) and close > pop_cisd_arm_dn_{run}",
        f"    {name} := 1.0",
        f"    pop_cisd_arm_dn_{run} := na",
        f"else if not na(pop_cisd_arm_up_{run}) and close < pop_cisd_arm_up_{run}",
        f"    {name} := -1.0",
        f"    pop_cisd_arm_up_{run} := na",
        f"int pop_cisd_d_{run} = close > open ? 1 : close < open ? -1 : 0",
        f"if pop_cisd_d_{run} != 0 and pop_cisd_d_{run} == pop_cisd_dir_{run}",
        f"    pop_cisd_len_{run} += 1",
        f"else if pop_cisd_d_{run} != 0",
        f"    pop_cisd_dir_{run} := pop_cisd_d_{run}",
        f"    pop_cisd_len_{run} := 1",
        f"    pop_cisd_open_{run} := open",
        f"if pop_cisd_dir_{run} == -1 and pop_cisd_len_{run} >= {run}",
        f"    pop_cisd_arm_dn_{run} := pop_cisd_open_{run}",
        f"else if pop_cisd_dir_{run} == 1 and pop_cisd_len_{run} >= {run}",
        f"    pop_cisd_arm_up_{run} := pop_cisd_open_{run}",
    ]


def _structural_lines(features: set[str]) -> list[str]:
    """The SMC pack. `bos_20` and friends are name FAMILIES, so the knob is read
    off the suffix and one pivot block is shared by every feature using it."""
    knobs = {name: smc_knob(name) for name in features}
    knobs = {name: k for name, k in knobs.items() if k}
    pivots = {k for _, (base, k) in knobs.items() if base != "cisd"}
    if "fib_pos" in features:
        pivots.add(SWING_LOOKBACK)
    if not pivots and not knobs:
        return []
    lines: list[str] = []
    for n in sorted(pivots):
        lines += _pivot_lines(n)
    if any(base.startswith("ob_") for base, _ in knobs.values()):
        lines += _pop_atr_lines()
    if "fib_pos" in features:
        n = SWING_LOOKBACK
        lines += [
            "// where the close sits in the last confirmed swing range: 0 at the low,",
            "// 1 at the high, outside [0,1] on a break-out. A zero-width range is na.",
            f"pop_fib_span_{n} = pop_swing_hi_{n} - pop_swing_lo_{n}",
            f"fib_pos = pop_fib_span_{n} == 0 ? na"
            f" : (close - pop_swing_lo_{n}) / pop_fib_span_{n}",
        ]
    for name in sorted(n for n, (b, _) in knobs.items() if b == "equilibrium"):
        n = knobs[name][1]
        lines.append(f"{name} = (pop_swing_hi_{n} + pop_swing_lo_{n}) / 2.0")
    # mss is built ON bos, so every lookback either needs it
    for n in sorted({k for _, (base, k) in knobs.items() if base in ("bos", "mss")}):
        lines += _bos_lines(n)
    for name in sorted(n for n, (b, _) in knobs.items() if b == "bos"):
        lines.append(f"{name} = pop_bos_{knobs[name][1]}")
    for name in sorted(n for n, (b, _) in knobs.items() if b == "mss"):
        lines += _mss_lines(name, knobs[name][1])
    for n in sorted({k for _, (base, k) in knobs.items() if base.startswith("ob_")}):
        lines += _order_block_lines(n)
    for name in sorted(n for n, (b, _) in knobs.items() if b.startswith("ob_")):
        base, n = knobs[name]
        lines.append(f"{name} = pop_{base}_{n}")
    for name in sorted(n for n, (b, _) in knobs.items() if b == "cisd"):
        lines += _cisd_lines(name, knobs[name][1])
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


def _resting_entry_lines(entry, anchor: str | None = None,
                         placement: list[tuple[str, str]] = ()) -> list[str]:
    """A limit/stop order priced off the SIGNAL bar and live from the next one.

    Mirrors the compiler: a limit rests at a BETTER price than the signal close
    and a stop at a WORSE one; the order is cancelled once it has rested
    `expiry_bars` bars; no new signal is taken while one is resting.

    An ANCHORED order rests at the level-feature's value on the signal bar
    instead. The compiler reads `entry_lvl[i]` there and applies NO offset, so
    neither does this -- and a signal whose anchor has not formed is dropped,
    exactly as `rest_dir` stays 0 for a NaN anchor.

    `placement` latches a level exit's stop/target on this same bar, because that
    is where the compiler measures them from once an order rests (see
    `_placement_latch`). The var holds while the order rests: nothing can
    overwrite it, since no new order is armed while `rest_dir != 0`.
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
    latch = [f"    {var} := {expr}" for var, expr in placement]
    return [
        "var float signal_atr = na",
    ] + [f"var float {var} = na" for var, _ in placement] + [
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
    ] + latch + [
        "    rest_dir := 1",
        "    rest_bars := 0",
        f'    strategy.entry("Long", strategy.long, {keyword}={long_price})',
        f"else if can_enter and short_signal and {armed}",
        "    signal_atr := atr_14",
    ] + latch + [
        "    rest_dir := -1",
        "    rest_bars := 0",
        f'    strategy.entry("Short", strategy.short, {keyword}={short_price})',
    ]


def _tightens(spec: StrategySpec) -> bool:
    return spec.wrapper.trail.type == "atr" or spec.wrapper.break_even.type == "after_r"


def _tighten_lines(spec: StrategySpec, stop_var: str, risk_var: str) -> list[str]:
    """Move the stop for FUTURE bars from THIS bar's data -- compiler.py step 3.

    The compiler runs this AFTER its own price checks, so a tightened stop only
    binds from the next bar; a Pine exit order placed now becomes live at the
    next bar too, which is the same timing. Both moves only ever tighten (max
    for a long, min for a short) and the break-even measures the favourable
    excursion against the ORIGINAL 1R, never the trailed distance.

    `barstate.isconfirmed` keeps the intrabar recalculation on a fill bar
    (calc_on_order_fills) from trailing off a partial close, which would latch a
    tighter stop than the backtest ever had.
    """
    w = spec.wrapper
    if not _tightens(spec):
        return []
    lines = [
        f"if barstate.isconfirmed and strategy.position_size != 0 and not na({stop_var})",
    ]
    if w.break_even.type == "after_r":
        lines += [
            "    float favourable = in_long ? high - entry_price : entry_price - low",
            f"    if favourable >= {_wrapper_value(w.break_even.trigger_r)} * {risk_var}",
            f"        {stop_var} := in_long ? math.max({stop_var}, entry_price)"
            f" : math.min({stop_var}, entry_price)",
        ]
    if w.trail.type == "atr":
        lines += [
            "    if not na(atr_14)",
            f"        float trailed = close - dir_sign * {_wrapper_value(w.trail.mult)} * atr_14",
            f"        {stop_var} := in_long ? math.max({stop_var}, trailed)"
            f" : math.min({stop_var}, trailed)",
        ]
    return lines


def _managed_exit_lines(spec: StrategySpec) -> list[str]:
    """The ATR-stop / ratio-target exit, held in state so a trail can move it.

    Only emitted when a trail or break-even is active. Without one the stop
    never moves, so the stateless form stays -- and stays byte-identical.
    """
    w = spec.wrapper
    return [
        "",
        "in_long = strategy.position_size > 0",
        "entry_price = strategy.position_avg_price",
        "dir_sign = in_long ? 1.0 : -1.0",
        "var float cur_stop = na",
        "var float cur_target = na",
        "var float risk_distance = na",
        "if strategy.position_size == 0",
        "    cur_stop := na",
        "    cur_target := na",
        "    risk_distance := na",
        "else if na(cur_stop)",
        "    // signal_atr is the SIGNAL bar's ATR, so a resting order that fills bars",
        "    // later still risks the distance the hypothesis was written with",
        f"    risk_distance := nz(signal_atr, atr_14) * {_wrapper_value(w.stop.mult)}",
        "    cur_stop := entry_price - dir_sign * risk_distance",
        f"    cur_target := entry_price + dir_sign * risk_distance"
        f" * {_wrapper_value(w.target.ratio)}",
    ] + _tighten_lines(spec, "cur_stop", "risk_distance") + [
        "if strategy.position_size > 0 and not na(cur_stop)",
        '    strategy.exit("Long exit", "Long", stop=cur_stop, limit=cur_target)',
        "if strategy.position_size < 0 and not na(cur_stop)",
        '    strategy.exit("Short exit", "Short", stop=cur_stop, limit=cur_target)',
        "",
    ]


def _level_exit_lines(spec: StrategySpec) -> list[str]:
    """Freeze the stop/target on the bar the position OPENED, from that bar's levels.

    That is where popper/dsl/compiler.py resolves them for a MARKET entry
    (`try_open` reads stop_lvl[i]/tgt_lvl[i] at the fill bar and never moves them
    afterwards). `_exit_series` supplies the same series the compiler's
    `exit_anchor` builds, so the value is final even during Pine's on-fill
    intrabar recalculation.

    A RESTING order settles its risk one step earlier: `try_open` takes an
    `anchor_i` of the PLACEMENT bar, so the level was latched there
    (`_placement_latch`) and is only read back here. Recomputing the anchor on
    this bar instead is the exact mismatch that change removed.
    """
    w = spec.wrapper
    resting = w.entry.type != "market"
    if w.stop.type == "level" and resting:
        stop_expr = "signal_stop_lvl"
    elif w.stop.type == "level":
        anchor = _exit_series(_exit_anchor(spec, w.stop, "wrapper.stop"))
        stop_expr = f"{anchor} + ({_wrapper_value(w.stop.offset)})"
    else:
        stop_expr = (
            "entry_price - dir_sign * nz(signal_atr, atr_14) * "
            f"{_wrapper_value(w.stop.mult)}"
        )
    if w.target.type == "level" and resting:
        target_expr = "signal_target_lvl"
    elif w.target.type == "level":
        anchor = _exit_series(_exit_anchor(spec, w.target, "wrapper.target"))
        target_expr = f"{anchor} + ({_wrapper_value(w.target.offset)})"
    else:
        target_expr = (
            "entry_price + dir_sign * math.abs(entry_price - new_stop) * "
            f"{_wrapper_value(w.target.ratio)}"
        )
    risk_lines = ["var float risk_distance = na"] if _tightens(spec) else []
    risk_reset = ["    risk_distance := na"] if _tightens(spec) else []
    risk_set = (["        risk_distance := math.abs(entry_price - new_stop)"]
                if _tightens(spec) else [])
    return [
        "",
        "in_long = strategy.position_size > 0",
        "entry_price = strategy.position_avg_price",
        "dir_sign = in_long ? 1.0 : -1.0",
        "var float lvl_stop = na",
        "var float lvl_target = na",
    ] + risk_lines + [
        "if strategy.position_size == 0",
        "    lvl_stop := na",
        "    lvl_target := na",
    ] + risk_reset + [
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
    ] + risk_set + [
        "    else",
        '        strategy.close_all(comment="no level", immediately=true)',
    ] + _tighten_lines(spec, "lvl_stop", "risk_distance") + [
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
    lines += _structural_lines(features)
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
            entry, _entry_anchor(spec, entry.anchor) if entry.anchor else None,
            _placement_latch(spec),
        )
    if spec.wrapper.stop.type == "level" or spec.wrapper.target.type == "level":
        lines += _level_exit_lines(spec)
    elif _tightens(spec):
        lines += _managed_exit_lines(spec)
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
