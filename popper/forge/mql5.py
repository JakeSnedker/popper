"""Deterministic StrategySpec to MetaTrader 5 Expert Advisor generator."""

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
    # step-5: window features (no structural pivots)
    "fvg", "htf_trend", "liquidity_sweep",
}
# Every level-feature renders as a series (tests/test_forge_levels.py proves each
# one numerically against popper.dsl.features.levels.compute_level on real bars).
_SUPPORTED_LEVELS = set(LEVEL_FEATURES)
_EMA = re.compile(r"^ema_(\d+)$")
_PARAM = re.compile(r"^p\[(\w+)\]$")


class Mql5Unsupported(ValueError):
    pass


def _level_call(name: str, shift: str) -> str:
    """The MQL5 expression for level-feature `name` evaluated at bar `shift`."""
    if name.startswith("prior_swing_"):
        return (f"LevelSwing({shift}, {DEFAULT_SWING_N}, "
                f"{'true' if name.endswith('_hi') else 'false'}, "
                f"{'true' if '_body_' in name else 'false'})")
    if name.startswith("prior_fractal_"):
        return f"LevelFractal({shift}, {'true' if name.endswith('_hi') else 'false'})"
    if name.startswith("session_"):
        start, end = SESSIONS[name.split("_")[1]]
        return (f"LevelSession({shift}, {start}, {end}, "
                f"{'true' if name.endswith('_hi') else 'false'})")
    if name == "bb_line":
        return f"LevelBb({shift})"
    raise Mql5Unsupported(f"MQL5 target does not implement level-feature {name!r}")


_LEVEL_HELPERS = {
    "LevelSwing": '''
// Rolling extreme of the PRIOR n bars -- the shift(1) in levels.py, which is what
// keeps a swing level causal. Bar `shift` itself is never looked at.
double LevelSwing(const int shift, const int n, const bool want_high, const bool body)
{
   if(Bars(_Symbol, _Period) < shift + n + 1) return EMPTY_VALUE;
   double best = EMPTY_VALUE;
   for(int i = shift + 1; i <= shift + n; i++)
   {
      double v;
      if(body) v = want_high ? MathMax(iOpen(_Symbol, _Period, i), iClose(_Symbol, _Period, i))
                             : MathMin(iOpen(_Symbol, _Period, i), iClose(_Symbol, _Period, i));
      else     v = want_high ? iHigh(_Symbol, _Period, i) : iLow(_Symbol, _Period, i);
      if(best == EMPTY_VALUE) best = v;
      else best = want_high ? MathMax(best, v) : MathMin(best, v);
   }
   return best;
}
''',
    "LevelFractal": '''
// Most recent CONFIRMED Bill Williams 5-bar fractal, carried forward. A fractal
// at bar j needs 2 higher/lower bars on EACH side, so it is only confirmed two
// bars later: the scan starts at shift+2 and reads j-1/j-2, which are already
// complete. Scanning back for the newest one IS the forward fill.
double LevelFractal(const int shift, const bool want_high)
{
   int last = (int)MathMin(Bars(_Symbol, _Period) - 3, shift + 1 + InpLevelScanBars);
   for(int j = shift + 2; j <= last; j++)
   {
      double v = want_high ? iHigh(_Symbol, _Period, j) : iLow(_Symbol, _Period, j);
      double a = want_high ? iHigh(_Symbol, _Period, j - 1) : iLow(_Symbol, _Period, j - 1);
      double b = want_high ? iHigh(_Symbol, _Period, j - 2) : iLow(_Symbol, _Period, j - 2);
      double c = want_high ? iHigh(_Symbol, _Period, j + 1) : iLow(_Symbol, _Period, j + 1);
      double d = want_high ? iHigh(_Symbol, _Period, j + 2) : iLow(_Symbol, _Period, j + 2);
      if(want_high ? (v > a && v > b && v > c && v > d) : (v < a && v < b && v < c && v < d))
         return v;
   }
   return EMPTY_VALUE;   // no fractal within the scan window: treat as not formed
}
''',
    "LevelSession": '''
// High/low of the PREVIOUS day's session, mapped onto every bar of the current
// day. Previous-day is unambiguously complete, so it is causal. "Previous day"
// means the previous day PRESENT IN THE DATA (levels.py shifts the per-day
// groupby), so a weekend maps Monday back to Friday.
double LevelSession(const int shift, const int start_hour, const int end_hour, const bool want_high)
{
   datetime now = iTime(_Symbol, _Period, shift) + InpBrokerToUtcHours * 3600;
   if(now <= 0) return EMPTY_VALUE;
   int today = (int)(now / 86400);
   int prev = -1;
   double best = EMPTY_VALUE;
   int total = Bars(_Symbol, _Period);
   for(int i = shift; i < total && i <= shift + InpLevelScanBars; i++)
   {
      datetime bar_utc = iTime(_Symbol, _Period, i) + InpBrokerToUtcHours * 3600;
      if(bar_utc <= 0) break;
      int day = (int)(bar_utc / 86400);
      if(day >= today) continue;
      if(prev < 0) prev = day;
      if(day != prev) break;              // walked past the previous day
      MqlDateTime parts;
      TimeToStruct(bar_utc, parts);
      if(parts.hour < start_hour || parts.hour >= end_hour) continue;
      double v = want_high ? iHigh(_Symbol, _Period, i) : iLow(_Symbol, _Period, i);
      if(best == EMPTY_VALUE) best = v;
      else best = want_high ? MathMax(best, v) : MathMin(best, v);
   }
   return best;   // EMPTY_VALUE when that day never traded the session
}
''',
    "LevelBb": '''
// Bollinger middle line = SMA(20) of close, INCLUDING bar `shift` itself.
double LevelBb(const int shift)
{
   if(Bars(_Symbol, _Period) < shift + 20) return EMPTY_VALUE;
   double sum = 0.0;
   for(int i = shift; i < shift + 20; i++) sum += iClose(_Symbol, _Period, i);
   return sum / 20.0;
}
''',
}


# Helpers backing a signal-FEATURE. Same shape as the level helpers: a pure
# function of `shift`, so "the value at bar i" is one call, and the mirror in
# tests/test_forge_features.py is the same function evaluated bar by bar.
#
# Some of these replay a recursion the Python seeds at its first bar. An EA has
# no such origin, so it re-walks InpWarmupBars of history: the seed's influence
# decays geometrically and the walk is what the mirror reproduces exactly.
_FEATURE_HELPERS = {
    "RsiFast": '''
// Wilder RSI(7) as features/__init__.py::rsi_fast computes it. That seeds
// ewm(alpha=1/7, adjust=False) on the FIRST close-to-close delta where iRSI seeds
// on an SMA -- different values, so iRSI is not a match. Warm-up and a zero
// average loss both return 50.0: the .fillna(50.0) at the end of the Python.
double RsiFast(const int shift)
{
   int last = (int)MathMin(Bars(_Symbol, _Period) - 1, shift + InpWarmupBars);
   if(last - shift < 7) return 50.0;
   double gain = 0.0, loss = 0.0;
   for(int i = last - 1; i >= shift; i--)
   {
      double delta = iClose(_Symbol, _Period, i) - iClose(_Symbol, _Period, i + 1);
      double up = MathMax(delta, 0.0), down = MathMax(-delta, 0.0);
      if(i == last - 1) { gain = up; loss = down; }
      else { gain += (up - gain) / 7.0; loss += (down - loss) / 7.0; }
   }
   if(loss <= 0.0) return 50.0;
   return 100.0 - 100.0 / (1.0 + gain / loss);
}
''',
    "Psar": '''
// Parabolic SAR (af 0.02, step 0.02, max 0.2) as features/__init__.py::psar runs
// it. iSAR's flip and clamp rules differ, so the forward loop is replayed here.
// The first bar of the walk has no SAR yet (the Python leaves out[0] NaN).
double Psar(const int shift)
{
   int last = (int)MathMin(Bars(_Symbol, _Period) - 1, shift + InpWarmupBars);
   if(last <= shift) return EMPTY_VALUE;
   bool uptrend = true;
   double af = 0.02;
   double sar = iLow(_Symbol, _Period, last), ep = iHigh(_Symbol, _Period, last);
   double out = EMPTY_VALUE;
   for(int i = last - 1; i >= shift; i--)
   {
      int step = last - i;   // 1-based position in the walk == the Python loop's i
      double high = iHigh(_Symbol, _Period, i), low = iLow(_Symbol, _Period, i);
      sar = sar + af * (ep - sar);
      if(uptrend)
      {
         double prev = iLow(_Symbol, _Period, i + 1);
         sar = MathMin(sar, MathMin(prev, step >= 2 ? iLow(_Symbol, _Period, i + 2) : prev));
         if(low < sar)      { uptrend = false; sar = ep; ep = low; af = 0.02; }
         else if(high > ep) { ep = high; af = MathMin(af + 0.02, 0.2); }
      }
      else
      {
         double prev = iHigh(_Symbol, _Period, i + 1);
         sar = MathMax(sar, MathMax(prev, step >= 2 ? iHigh(_Symbol, _Period, i + 2) : prev));
         if(high > sar)    { uptrend = true; sar = ep; ep = high; af = 0.02; }
         else if(low < ep) { ep = low; af = MathMin(af + 0.02, 0.2); }
      }
      out = sar;
   }
   return out;
}
''',
    "Fvg": '''
// Fair Value Gap presence: +1 on a bullish 3-bar gap (the high two bars back is
// below this bar's low), -1 on the mirror. features/__init__.py compares against
// NaN during warm-up, which is False, so the first two bars are 0.0, not unformed.
double Fvg(const int shift)
{
   if(Bars(_Symbol, _Period) < shift + 3) return 0.0;
   double value = 0.0;
   if(iHigh(_Symbol, _Period, shift + 2) < iLow(_Symbol, _Period, shift)) value += 1.0;
   if(iLow(_Symbol, _Period, shift + 2) > iHigh(_Symbol, _Period, shift)) value -= 1.0;
   return value;
}
''',
    "LiquiditySweep": '''
// +1 when the low pierces the PRIOR 20-bar low and the close reclaims it, -1 on
// the mirror. The baseline window is rolling(20).min().shift(1), so it starts at
// shift + 1: this bar is never part of the level it has to pierce.
double LiquiditySweep(const int shift)
{
   if(Bars(_Symbol, _Period) < shift + 21) return 0.0;
   double prior_lo = iLow(_Symbol, _Period, shift + 1);
   double prior_hi = iHigh(_Symbol, _Period, shift + 1);
   for(int i = shift + 2; i <= shift + 20; i++)
   {
      prior_lo = MathMin(prior_lo, iLow(_Symbol, _Period, i));
      prior_hi = MathMax(prior_hi, iHigh(_Symbol, _Period, i));
   }
   double bar_hi = iHigh(_Symbol, _Period, shift), bar_lo = iLow(_Symbol, _Period, shift);
   double bar_close = iClose(_Symbol, _Period, shift);
   double value = 0.0;
   if(bar_lo < prior_lo && bar_close > prior_lo) value += 1.0;
   if(bar_hi > prior_hi && bar_close < prior_hi) value -= 1.0;
   return value;
}
''',
    "PopEma": '''
// ema() in features/__init__.py is ewm(span, adjust=False, min_periods=span): the
// recursion is seeded on the FIRST close, where iMA/MODE_EMA seeds on an SMA. At
// span 240 the two disagree for thousands of M5 bars, so the walk is replayed.
double PopEma(const int shift, const int period)
{
   int last = (int)MathMin(Bars(_Symbol, _Period) - 1, shift + InpWarmupBars);
   if(last - shift + 1 < period) return EMPTY_VALUE;
   double alpha = 2.0 / (period + 1.0);
   double value = iClose(_Symbol, _Period, last);
   for(int i = last - 1; i >= shift; i--)
      value += (iClose(_Symbol, _Period, i) - value) * alpha;
   return value;
}
''',
    "OpeningRange": '''
// High-low range of the first %(bars)d NY-session bars of the bar's OWN UTC day,
// published only once all of them have printed (pos >= %(last)d in
// features/__init__.py::opening_range) and then held for the rest of that day.
double OpeningRange(const int shift)
{
   datetime now = iTime(_Symbol, _Period, shift) + InpBrokerToUtcHours * 3600;
   if(now <= 0) return EMPTY_VALUE;
   int today = (int)(now / 86400);
   int total = Bars(_Symbol, _Period);
   int oldest = shift;
   for(int i = shift; i < total && i <= shift + InpLevelScanBars; i++)
   {
      datetime bar_utc = iTime(_Symbol, _Period, i) + InpBrokerToUtcHours * 3600;
      if(bar_utc <= 0 || (int)(bar_utc / 86400) != today) break;
      oldest = i;
   }
   int pos = -1;
   double range_hi = EMPTY_VALUE, range_lo = EMPTY_VALUE, range = EMPTY_VALUE;
   for(int i = oldest; i >= shift; i--)
   {
      MqlDateTime parts;
      TimeToStruct(iTime(_Symbol, _Period, i) + InpBrokerToUtcHours * 3600, parts);
      if(parts.hour < %(start)d || parts.hour >= %(end)d) continue;
      pos++;
      if(pos >= %(bars)d) continue;
      double h = iHigh(_Symbol, _Period, i), l = iLow(_Symbol, _Period, i);
      range_hi = range_hi == EMPTY_VALUE ? h : MathMax(range_hi, h);
      range_lo = range_lo == EMPTY_VALUE ? l : MathMin(range_lo, l);
      range = range_hi - range_lo;
   }
   return pos >= %(last)d ? range : EMPTY_VALUE;
}
''' % {"start": SESSIONS["ny"][0], "end": SESSIONS["ny"][1],
       "bars": OPENING_RANGE_BARS, "last": OPENING_RANGE_BARS - 1},
}

# helpers that re-walk history and so need InpWarmupBars, and helpers that scan
# back over calendar days and so need InpLevelScanBars
_WARMUP_HELPERS = {"RsiFast", "Psar", "PopEma"}
_SCAN_HELPERS = {"OpeningRange"}

_HELPERS = {**_LEVEL_HELPERS, **_FEATURE_HELPERS}


def _feature_helpers(features: set[str]) -> set[str]:
    """The helper functions the emitted feature lines call."""
    wanted = {"bb_line": "LevelBb", "rsi_fast": "RsiFast", "psar": "Psar",
              "opening_range": "OpeningRange", "fvg": "Fvg",
              "liquidity_sweep": "LiquiditySweep", "htf_trend": "PopEma"}
    return {helper for name, helper in wanted.items() if name in features}


def _helper_source(names: set[str]) -> str:
    return "".join(_HELPERS[key] for key in sorted(names)) + ("\n" if names else "")


def _level_helper_names(names: set[str]) -> set[str]:
    return {_level_call(name, "0").split("(")[0] for name in names}


def _exit_anchor(spec: StrategySpec, rule, path: str) -> str:
    name = anchor_level(spec, rule.anchor)
    if name not in _SUPPORTED_LEVELS:
        raise Mql5Unsupported(f"MQL5 target does not implement level-feature {name!r} ({path})")
    return name


def _exit_call(name: str) -> str:
    """The level call an EXIT prices from, evaluated on the bar the trade opens.

    Shift 0 is that bar. Most levels are functions of bars <= i-1, so shift 0 is
    already final. bb_line and the two fractals read bar i's own OHLC, and
    popper/dsl/compiler.py's `exit_anchor` shifts exactly those three
    (INTRA_BAR_LEVELS) one bar back before resolving an exit -- so the EA reads
    shift 1, the last fully-closed bar, which is the price the backtest used.

    ENTRY anchors stay at shift 1 (the signal bar) and are never shifted again.
    """
    return _level_call(name, "1" if name in INTRA_BAR_LEVELS else "0")


def _entry_anchor(spec: StrategySpec, name: str) -> str:
    resolved = anchor_level(spec, name)
    if resolved not in _SUPPORTED_LEVELS:
        raise Mql5Unsupported(
            f"MQL5 target does not implement level-feature {resolved!r} (wrapper.entry.anchor)"
        )
    return resolved


def _spec_levels(spec: StrategySpec) -> set[str]:
    """Every level-feature this spec needs rendered."""
    names = set()
    w = spec.wrapper
    if w.stop.type == "level":
        names.add(_exit_anchor(spec, w.stop, "wrapper.stop"))
    if w.target.type == "level":
        names.add(_exit_anchor(spec, w.target, "wrapper.target"))
    if w.entry.type != "market" and w.entry.anchor:
        names.add(_entry_anchor(spec, w.entry.anchor))
    return names


def _number(value) -> str:
    return f"{float(value):.10g}"


def _wrapper_value(value) -> str:
    match = _PARAM.match(value) if isinstance(value, str) else None
    return input_name(match.group(1), "mql5") if match else _number(value)


def _validate(spec: StrategySpec) -> None:
    if spec.alpha.higher_timeframe_features:
        raise Mql5Unsupported("MQL5 target does not yet implement causal higher-timeframe features")
    unsupported = sorted(feature for feature in spec.alpha.features
                         if feature not in _SUPPORTED and not _EMA.match(feature))
    if unsupported:
        raise Mql5Unsupported(f"MQL5 target does not implement features: {', '.join(unsupported)}")
    if spec.wrapper.stop.type not in ("atr", "level"):
        raise Mql5Unsupported(f"MQL5 target does not implement stop type {spec.wrapper.stop.type!r}")
    if spec.wrapper.target.type not in ("ratio", "level"):
        raise Mql5Unsupported(f"MQL5 target does not implement target type {spec.wrapper.target.type!r}")
    if spec.wrapper.trail.type != "none" or spec.wrapper.break_even.type != "none":
        raise Mql5Unsupported("MQL5 target does not yet support trail or break-even wrappers")
    if spec.wrapper.constraints.no_trade:
        raise Mql5Unsupported("MQL5 target does not yet support no_trade constraints")


def _reject_unsupported(spec) -> None:
    """Refuse to emit code that would trade differently from the backtest.

    A level exit combined with a RESTING entry is the one combination left out:
    the compiler measures 1R from the fill bar's level, and RiskLots needs that
    distance when the pending order is SENT, bars before the level is known.
    Sizing off the placement-bar level would deploy a position size nobody
    backtested.
    """
    w = spec.wrapper
    level_exit = w.stop.type == "level" or w.target.type == "level"
    if level_exit and w.entry.type != "market":
        raise Mql5Unsupported(
            f"MQL5 target cannot combine a level exit with a {w.entry.type} entry: "
            "the risk distance is only known at the fill, after the order is sent"
        )
    _spec_levels(spec)  # raises on any anchor this target cannot render


def generate_mql5(spec: StrategySpec) -> str:
    _reject_unsupported(spec)
    _validate(spec)
    features = set(spec.alpha.features)
    levels = _spec_levels(spec)
    ema_periods = {int(match.group(1)) for feature in features if (match := _EMA.match(feature))}
    ema_periods |= {20, 48, 50, 240}
    title = re.sub(r"[^A-Za-z0-9 _.-]", "", spec.name)[:80]
    inputs = [
        "input long InpMagic = 260727;",
        f"input double InpRiskPct = {_number(spec.wrapper.sizing.risk_pct)};",
        f"input int InpMaxTradesPerDay = {spec.wrapper.constraints.max_trades_per_day or 0};",
        "input int InpBrokerToUtcHours = 0;",
        "input int InpCusumScanBars = 5000;",
    ]
    helpers = _level_helper_names(levels) | _feature_helpers(features)
    if levels or helpers & _SCAN_HELPERS:
        # ponytail: CALIBRATION KNOB. levels.py forward-fills a fractal and looks
        # back to the previous trading day with no bound; an EA has to bound the
        # scan. Raise it if a level goes EMPTY_VALUE on a thin/illiquid feed.
        inputs.append("input int InpLevelScanBars = 5000;")
    if helpers & _WARMUP_HELPERS:
        # ponytail: CALIBRATION KNOB. The Python seeds its recursions at the first
        # bar of the dataset; an EA has no such origin and re-walks this much
        # history instead. The seed's influence decays geometrically -- 2000 M5
        # bars puts RSI(7) below 1e-9. Raise it if a feed's warm-up is thinner.
        inputs.append("input int InpWarmupBars = 2000;")
    for name, grid in sorted(spec.param_space.items()):
        default = grid[len(grid) // 2]
        if isinstance(default, str):
            continue  # a categorical (an anchor sweep): resolved to a name, not an input
        inputs.append(f"input double {input_name(name, 'mql5')} = {_number(default)};")

    handles = ["int hATR = INVALID_HANDLE;", "int hRSI7 = INVALID_HANDLE;", "int hSAR = INVALID_HANDLE;"]
    handles += [f"int hEMA{period} = INVALID_HANDLE;" for period in sorted(ema_periods)]
    on_init = [
        "   hATR = iATR(_Symbol, _Period, 14);",
        "   hRSI7 = iRSI(_Symbol, _Period, 7, PRICE_CLOSE);",
        "   hSAR = iSAR(_Symbol, _Period, 0.02, 0.2);",
    ]
    on_init += [f"   hEMA{period} = iMA(_Symbol, _Period, {period}, 0, MODE_EMA, PRICE_CLOSE);"
                for period in sorted(ema_periods)]
    handle_names = ["hATR", "hRSI7", "hSAR"] + [f"hEMA{period}" for period in sorted(ema_periods)]
    validity = " || ".join(f"{name} == INVALID_HANDLE" for name in handle_names)
    releases = [f"   IndicatorRelease({name});" for name in handle_names]

    feature_lines = [
        "   double open = iOpen(_Symbol, _Period, 1);",
        "   double high = iHigh(_Symbol, _Period, 1);",
        "   double low = iLow(_Symbol, _Period, 1);",
        "   double close = iClose(_Symbol, _Period, 1);",
        "   double volume = (double)iVolume(_Symbol, _Period, 1);",
        "   double atr_14 = BufferValue(hATR, 1);",
    ]
    for feature in sorted(features):
        match = _EMA.match(feature)
        if match:
            feature_lines.append(f"   double {feature} = BufferValue(hEMA{int(match.group(1))}, 1);")
    # ny first so a session_ny-only spec keeps emitting exactly what it always did
    sessions = [s for s in ("ny", "asia", "london") if f"session_{s}" in features]
    if sessions:
        feature_lines += [
            "   MqlDateTime utc;",
            "   TimeToStruct(iTime(_Symbol, _Period, 1) + InpBrokerToUtcHours * 3600, utc);",
        ]
        feature_lines += [
            f"   double session_{s} = (utc.hour >= {SESSIONS[s][0]} && "
            f"utc.hour < {SESSIONS[s][1]}) ? 1.0 : 0.0;" for s in sessions
        ]
    if {"rel_volume", "delta_proxy"} & features:
        feature_lines.append("   double rel_volume = RelativeVolume(1);")
    if "delta_proxy" in features:
        feature_lines.append("   double delta_proxy = Sign(iClose(_Symbol, _Period, 1) - iOpen(_Symbol, _Period, 1)) * rel_volume;")
    if "vol_regime" in features:
        feature_lines.append("   double vol_regime = VolRegime(1);")
    if "trend_regime" in features:
        feature_lines.append("   double trend_regime = MathAbs(BufferValue(hEMA48, 1) - BufferValue(hEMA240, 1)) / atr_14 >= 0.5 ? 1.0 : 0.0;")
    if "regime_score" in features:
        feature_lines.append("   double regime_score = RegimeScore(1);")
    if "cusum_shift" in features:
        feature_lines.append("   double cusum_shift = CusumShift(1);")
    if "candle_size" in features:
        feature_lines.append("   double candle_size = high - low;")
    if "body_pressure" in features:
        feature_lines.append(
            "   // a zero-range bar leaves body_pressure undefined, not zero\n"
            "   double body_pressure = (high - low) == 0.0 ? EMPTY_VALUE"
            " : (close - open) / (high - low);")
    if "bb_line" in features:
        feature_lines.append("   double bb_line = LevelBb(1);")
    if "rsi_fast" in features:
        feature_lines.append("   double rsi_fast = RsiFast(1);")
    if "psar" in features:
        feature_lines.append("   double psar = Psar(1);")
    if "opening_range" in features:
        feature_lines.append("   double opening_range = OpeningRange(1);")
    if "fvg" in features:
        feature_lines.append("   double fvg = Fvg(1);")
    if "liquidity_sweep" in features:
        feature_lines.append("   double liquidity_sweep = LiquiditySweep(1);")
    if "htf_trend" in features:
        feature_lines += [
            "   double htf_ema_fast = PopEma(1, 48);",
            "   double htf_ema_slow = PopEma(1, 240);",
            "   double htf_trend = (htf_ema_fast == EMPTY_VALUE || htf_ema_slow == EMPTY_VALUE)",
            "      ? EMPTY_VALUE : Sign(htf_ema_fast - htf_ema_slow);",
        ]

    long_signal = compile_expression(spec.alpha.signal, "mql5")
    short_signal = compile_expression(spec.alpha.signal_short, "mql5") if spec.alpha.signal_short else "false"
    stop_mult = _wrapper_value(spec.wrapper.stop.mult)
    rr = _wrapper_value(spec.wrapper.target.ratio)
    guard_names = sorted(features | {"atr_14"})
    feature_guard = " || ".join(
        f"{name} == EMPTY_VALUE || !MathIsValidNumber({name})" for name in guard_names
    )

    entry = spec.wrapper.entry
    resting = entry.type != "market"
    expiry_bars = int(entry.expiry_bars)
    pending_globals = pending_helpers = ""
    entry_calls = [
        "   if(long_signal && OpenPosition(true, atr_14)) entries_today++;",
        "   else if(short_signal && OpenPosition(false, atr_14)) entries_today++;",
    ]
    position_inner: list[str] = []
    pending_lines: list[str] = []

    if resting:
        offset = _wrapper_value(entry.offset)
        # a limit rests at a BETTER price than the signal close, a stop at a WORSE one
        price_sign = "-" if entry.type == "limit" else "+"
        if entry.anchor:
            # the backtest rests the order AT the anchor's value on the SIGNAL bar
            # (shift 1 here) and applies no offset -- compiler.py: rest_px =
            # entry_lvl[i]. A level that has not formed drops the signal rather
            # than resting an order at a made-up price.
            price_line = (
                f"double anchor = {_level_call(_entry_anchor(spec, entry.anchor), '1')};\n"
                "   if(anchor == EMPTY_VALUE || !MathIsValidNumber(anchor)) return false;\n"
                "   double price = NormalizeDouble(anchor, digits);"
            )
        else:
            price_line = (
                f"double price = NormalizeDouble(signal_close {price_sign} "
                f"(is_long ? 1.0 : -1.0) * {offset} * atr, digits);"
            )
        through = ("market <= price : market >= price" if entry.type == "limit"
                   else "market >= price : market <= price")
        buy_fn, sell_fn = (("BuyLimit", "SellLimit") if entry.type == "limit"
                           else ("BuyStop", "SellStop"))
        comment = f"Popper {spec.id or ''}"
        pending_globals = (
            "\nulong pending_ticket = 0;"
            "\ndouble pending_atr = 0.0;"
            "\ndatetime managed_entry = 0;"
        )
        pending_helpers = f'''
// A gap can fill a resting order better (limit) or worse (stop) than its price.
// The backtest measures the stop from the FILL, so re-anchor once the fill is known.
void RepriceStops(const double atr)
{{
   if(atr <= 0.0) return;
   double fill = PositionGetDouble(POSITION_PRICE_OPEN);
   bool is_long = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
   double stop_distance = atr * {stop_mult};
   double reward_distance = stop_distance * {rr};
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   trade.PositionModify(_Symbol,
      NormalizeDouble(fill + (is_long ? -stop_distance : stop_distance), digits),
      NormalizeDouble(fill + (is_long ? reward_distance : -reward_distance), digits));
}}

bool PlacePending(const bool is_long, const double atr, const double signal_close)
{{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   {price_line}
   double market = SymbolInfoDouble(_Symbol, is_long ? SYMBOL_ASK : SYMBOL_BID);
   // price is already through the resting level: the backtest fills such a touch at
   // the bar's open, so take it at market rather than let the broker reject it
   if(is_long ? {through})
   {{
      if(!OpenPosition(is_long, atr)) return false;
      pending_atr = atr;
      return true;
   }}
   double stop_distance = atr * {stop_mult};
   double reward_distance = stop_distance * {rr};
   double lots = RiskLots(stop_distance);
   if(lots <= 0.0) return false;
   double stop = NormalizeDouble(price + (is_long ? -stop_distance : stop_distance), digits);
   double target = NormalizeDouble(price + (is_long ? reward_distance : -reward_distance), digits);
   ENUM_ORDER_TYPE_TIME time_type = ORDER_TIME_GTC;
   datetime expiration = 0;
   // broker-side backstop for when the EA is not running; the bar count in OnTick
   // is what actually enforces expiry_bars
   if((SymbolInfoInteger(_Symbol, SYMBOL_EXPIRATION_MODE) & SYMBOL_EXPIRATION_SPECIFIED) != 0)
   {{
      time_type = ORDER_TIME_SPECIFIED;
      expiration = iTime(_Symbol, _Period, 0) + {expiry_bars} * PeriodSeconds();
   }}
   bool placed = is_long
      ? trade.{buy_fn}(lots, price, _Symbol, stop, target, time_type, expiration, "{comment}")
      : trade.{sell_fn}(lots, price, _Symbol, stop, target, time_type, expiration, "{comment}");
   if(placed)
   {{
      pending_ticket = trade.ResultOrder();
      pending_atr = atr;
   }}
   return placed;
}}
'''
        entry_calls = [
            "   if(long_signal) PlacePending(true, atr_14, close);",
            "   else if(short_signal) PlacePending(false, atr_14, close);",
        ]
        position_inner += [
            "      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);",
            "      if(opened != managed_entry)",
            "      {",
            "         managed_entry = opened;",
            "         pending_ticket = 0;",
            "         entries_today++;   // the backtest counts a trade when it FILLS",
            "         RepriceStops(pending_atr);",
            "         pending_atr = 0.0;",
            "      }",
        ]
        pending_lines = [
            "   if(pending_ticket != 0)",
            "   {",
            "      if(!OrderSelect(pending_ticket)) pending_ticket = 0;   // filled or already gone",
            f"      else if(iBarShift(_Symbol, _Period, (datetime)OrderGetInteger(ORDER_TIME_SETUP)) >= {expiry_bars})",
            "      {",
            "         // rested its full life. The backtest can re-arm on this very bar, so",
            "         // fall through to the signal below rather than returning.",
            "         trade.OrderDelete(pending_ticket);",
            "         pending_ticket = 0;",
            "      }",
            "      else return;   // still resting: no new signal while an order is live",
            "   }",
        ]

    w = spec.wrapper
    if w.stop.type == "level" or w.target.type == "level":
        stop_expr = (
            f"{_exit_call(_exit_anchor(spec, w.stop, 'wrapper.stop'))}"
            f" + ({_wrapper_value(w.stop.offset)})" if w.stop.type == "level"
            else f"price + (is_long ? -1.0 : 1.0) * atr * {stop_mult}"
        )
        target_expr = (
            f"{_exit_call(_exit_anchor(spec, w.target, 'wrapper.target'))}"
            f" + ({_wrapper_value(w.target.offset)})" if w.target.type == "level"
            else f"price + (is_long ? 1.0 : -1.0) * stop_distance * {rr}"
        )
        open_position = f'''bool OpenPosition(const bool is_long, const double atr)
{{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double price = SymbolInfoDouble(_Symbol, is_long ? SYMBOL_ASK : SYMBOL_BID);
   // resolved on the bar the trade OPENS on -- shift 0, the bar whose first tick
   // fired this call -- exactly where compiler.py's try_open reads stop_lvl[i].
   double stop = {stop_expr};
   if(stop == EMPTY_VALUE || !MathIsValidNumber(stop)) return false;
   double stop_distance = MathAbs(price - stop);
   double target = {target_expr};
   if(target == EMPTY_VALUE || !MathIsValidNumber(target)) return false;
   // GUARD, same as try_open: an unformed anchor or a level on the wrong side of
   // entry is an incoherent trade. The backtest never opened it, so neither do we.
   if(is_long ? !(stop < price && target > price) : !(stop > price && target < price))
      return false;
   double lots = RiskLots(stop_distance);
   if(lots <= 0.0) return false;
   return is_long
      ? trade.Buy(lots, _Symbol, 0.0, NormalizeDouble(stop, digits), NormalizeDouble(target, digits), "Popper {spec.id or ''}")
      : trade.Sell(lots, _Symbol, 0.0, NormalizeDouble(stop, digits), NormalizeDouble(target, digits), "Popper {spec.id or ''}");
}}'''
    else:
        open_position = f'''bool OpenPosition(const bool is_long, const double atr)
{{
   double stop_distance = atr * {stop_mult};
   double reward_distance = stop_distance * {rr};
   double lots = RiskLots(stop_distance);
   if(lots <= 0.0) return false;
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double price = SymbolInfoDouble(_Symbol, is_long ? SYMBOL_ASK : SYMBOL_BID);
   double stop = NormalizeDouble(price + (is_long ? -stop_distance : stop_distance), digits);
   double target = NormalizeDouble(price + (is_long ? reward_distance : -reward_distance), digits);
   return is_long
      ? trade.Buy(lots, _Symbol, 0.0, stop, target, "Popper {spec.id or ''}")
      : trade.Sell(lots, _Symbol, 0.0, stop, target, "Popper {spec.id or ''}");
}}'''

    if spec.wrapper.time_stop.type == "bars":
        max_bars = _wrapper_value(spec.wrapper.time_stop.max_bars)
        position_inner += [
            "      // completed bars since the fill. The broker stop/target run intrabar, so a",
            "      // bar that breaches the stop AND expires the clock is already out as a stop.",
            f"      if(iBarShift(_Symbol, _Period, (datetime)PositionGetInteger(POSITION_TIME)) - 1 >= (int){max_bars})",
            "         trade.PositionClose(_Symbol);",
        ]

    level_helpers = _helper_source(helpers)
    position_lines = ["   if(PositionSelect(_Symbol)) return;"]
    if position_inner:
        position_lines = (["   if(PositionSelect(_Symbol))", "   {"]
                          + position_inner + ["      return;", "   }"])
    guard_block = "\n".join(
        position_lines + pending_lines
        + ["   if(InpMaxTradesPerDay > 0 && entries_today >= InpMaxTradesPerDay) return;", ""]
    )

    return f'''// Generated by Popper Strategy Forge. Validate against Python before live use.
// hypothesis_id={spec.hypothesis_id} spec_id={spec.id or "unstored"} dsl={spec.dsl_version}
// Feed parity caveat: sessions, tick volume, CUSUM warmup, and broker fills can differ.
#property strict
#property version   "1.00"
#property description "Popper | {title}"

#include <Trade/Trade.mqh>
CTrade trade;

{chr(10).join(inputs)}

{chr(10).join(handles)}
datetime last_bar = 0;
int entries_today = 0;
int day_key = -1;{pending_globals}

double Sign(const double value)
{{
   if(value > 0.0) return 1.0;
   if(value < 0.0) return -1.0;
   return 0.0;
}}

double BufferValue(const int handle, const int shift)
{{
   double values[];
   ArraySetAsSeries(values, true);
   if(CopyBuffer(handle, 0, shift, 1, values) != 1) return EMPTY_VALUE;
   return values[0];
}}

double RelativeVolume(const int shift)
{{
   double baseline = 0.0;
   for(int i = shift + 1; i <= shift + 20; i++) baseline += (double)iVolume(_Symbol, _Period, i);
   baseline /= 20.0;
   return baseline > 0.0 ? (double)iVolume(_Symbol, _Period, shift) / baseline : 1.0;
}}

double VolRegime(const int shift)
{{
   double values[];
   ArraySetAsSeries(values, true);
   if(CopyBuffer(hATR, 0, shift, 500, values) < 500) return EMPTY_VALUE;
   double current = values[0];
   int rank = 0;
   for(int i = 0; i < 500; i++) if(values[i] <= current) rank++;
   double pct = (double)rank / 500.0;
   if(pct >= 0.70) return 1.0;
   if(pct <= 0.30) return -1.0;
   return 0.0;
}}

double RegimeScore(const int shift)
{{
   double close_value = iClose(_Symbol, _Period, shift);
   return (
      Sign(BufferValue(hEMA48, shift) - BufferValue(hEMA240, shift)) +
      Sign(close_value - BufferValue(hEMA50, shift)) +
      Sign(BufferValue(hEMA20, shift) - BufferValue(hEMA50, shift)) +
      Sign(BufferValue(hRSI7, shift) - 50.0) +
      Sign(close_value - BufferValue(hSAR, shift))
   ) / 5.0;
}}

double CusumShift(const int shift)
{{
   int requested = (int)MathMax(InpCusumScanBars, 100) + 52;
   double closes[];
   ArraySetAsSeries(closes, true);
   int got = CopyClose(_Symbol, _Period, shift, requested, closes);
   if(got < 52) return EMPTY_VALUE;
   double positive = 0.0, negative = 0.0;
   for(int i = got - 52; i >= 0; i--)
   {{
      double mean = 0.0;
      for(int j = i; j < i + 50; j++) mean += closes[j] - closes[j + 1];
      mean /= 50.0;
      double variance = 0.0;
      for(int j = i; j < i + 50; j++)
      {{
         double delta = (closes[j] - closes[j + 1]) - mean;
         variance += delta * delta;
      }}
      double stdev = MathSqrt(variance / 49.0);
      double z = stdev > 0.0 ? (closes[i] - closes[i + 1]) / stdev : 0.0;
      positive = MathMax(0.0, positive + z - 0.5);
      negative = MathMax(0.0, negative - z - 0.5);
   }}
   return positive - negative;
}}

double NormalizeLots(double lots)
{{
   double minimum = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maximum = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   lots = MathMax(minimum, MathMin(maximum, lots));
   return NormalizeDouble(MathFloor(lots / step) * step, 2);
}}

double RiskLots(const double stop_distance)
{{
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(stop_distance <= 0.0 || tick_size <= 0.0 || tick_value <= 0.0) return 0.0;
   double risk_money = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPct / 100.0;
   return NormalizeLots(risk_money / ((stop_distance / tick_size) * tick_value));
}}

{level_helpers}{open_position}
{pending_helpers}
int OnInit()
{{
{chr(10).join(on_init)}
   if({validity}) return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagic);
   return INIT_SUCCEEDED;
}}

void OnDeinit(const int reason)
{{
{chr(10).join(releases)}
}}

void OnTick()
{{
   datetime current_bar = iTime(_Symbol, _Period, 0);
   if(current_bar == 0 || current_bar == last_bar) return;
   last_bar = current_bar;

   MqlDateTime bar_time;
   TimeToStruct(iTime(_Symbol, _Period, 1), bar_time);
   int current_day = bar_time.year * 1000 + bar_time.day_of_year;
   if(current_day != day_key) {{ day_key = current_day; entries_today = 0; }}
{guard_block}
{chr(10).join(feature_lines)}
   if({feature_guard} || atr_14 <= 0.0) return;
   bool long_signal = {long_signal};
   bool short_signal = {short_signal};
{chr(10).join(entry_calls)}
}}
'''
