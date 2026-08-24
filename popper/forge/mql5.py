"""Deterministic StrategySpec to MetaTrader 5 Expert Advisor generator."""

import re

from popper.artifacts import StrategySpec
from popper.dsl.features import (
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
    "SwingPivot": '''
// Most recent CONFIRMED swing pivot -- features/__init__.py::_swing_pivots, SHARED
// by bos/mss/equilibrium/fib_pos/order blocks. A centre needs `lookback` strictly
// lower (higher) bars on EACH side, so it is only visible once `lookback` bars have
// printed after it: the newest candidate is at shift + lookback. Scanning back for
// the newest qualifying centre IS the forward fill.
double SwingPivot(const int shift, const int lookback, const bool want_high)
{
   int total = Bars(_Symbol, _Period);
   int last = (int)MathMin(total - 1 - lookback, shift + lookback + InpStructureBars);
   for(int m = shift + lookback; m <= last; m++)
   {
      double v = want_high ? iHigh(_Symbol, _Period, m) : iLow(_Symbol, _Period, m);
      bool ok = true;
      for(int k = 1; k <= lookback; k++)
      {
         double newer = want_high ? iHigh(_Symbol, _Period, m - k) : iLow(_Symbol, _Period, m - k);
         double older = want_high ? iHigh(_Symbol, _Period, m + k) : iLow(_Symbol, _Period, m + k);
         if(want_high ? (newer >= v || older >= v) : (newer <= v || older <= v))
         {
            ok = false;
            break;
         }
      }
      if(ok) return v;
   }
   return EMPTY_VALUE;   // no pivot within the scan window: treat as not formed
}
''',
    "Bos": '''
// Break of Structure: +1 the bar the close FIRST crosses above the most recent
// confirmed swing high, -1 below the swing low, else 0. A fresh-break EVENT, so
// the previous bar's state is recomputed at shift + 1 rather than remembered.
double Bos(const int shift, const int lookback)
{
   if(Bars(_Symbol, _Period) < shift + 2) return 0.0;   // out[0] is 0 in the Python
   double hi = SwingPivot(shift, lookback, true);
   double lo = SwingPivot(shift, lookback, false);
   double hi_prev = SwingPivot(shift + 1, lookback, true);
   double lo_prev = SwingPivot(shift + 1, lookback, false);
   double c = iClose(_Symbol, _Period, shift);
   double c_prev = iClose(_Symbol, _Period, shift + 1);
   bool up_now  = hi      != EMPTY_VALUE && c      > hi;
   bool up_prev = hi_prev != EMPTY_VALUE && c_prev > hi_prev;
   bool dn_now  = lo      != EMPTY_VALUE && c      < lo;
   bool dn_prev = lo_prev != EMPTY_VALUE && c_prev < lo_prev;
   if(up_now && !up_prev) return 1.0;
   if(dn_now && !dn_prev) return -1.0;
   return 0.0;
}
''',
    "Cisd": '''
// Change in State of Delivery, features/__init__.py::cisd. Track the current run of
// same-direction closes, arm that run's OPEN, and fire when a close crosses back
// through it. The Python walks from bar 0; the EA replays InpStructureBars instead.
double Cisd(const int shift, const int run)
{
   int last = (int)MathMin(Bars(_Symbol, _Period) - 1, shift + InpStructureBars);
   int run_dir = 0, run_len = 0;
   double run_open = EMPTY_VALUE, arm_dn = EMPTY_VALUE, arm_up = EMPTY_VALUE;
   double out = 0.0;
   for(int i = last; i >= shift; i--)
   {
      double o = iOpen(_Symbol, _Period, i), c = iClose(_Symbol, _Period, i);
      out = 0.0;
      if(arm_dn != EMPTY_VALUE && c > arm_dn)      { out =  1.0; arm_dn = EMPTY_VALUE; }
      else if(arm_up != EMPTY_VALUE && c < arm_up) { out = -1.0; arm_up = EMPTY_VALUE; }
      int d = c > o ? 1 : (c < o ? -1 : 0);
      if(d != 0 && d == run_dir) run_len++;
      else if(d != 0) { run_dir = d; run_len = 1; run_open = o; }
      if(run_dir == -1 && run_len >= run)     arm_dn = run_open;
      else if(run_dir == 1 && run_len >= run) arm_up = run_open;
   }
   return out;
}
''',
    "Equilibrium": '''
// 50% of the current dealing range: midpoint of the last confirmed swing high/low.
double Equilibrium(const int shift, const int lookback)
{
   double hi = SwingPivot(shift, lookback, true), lo = SwingPivot(shift, lookback, false);
   if(hi == EMPTY_VALUE || lo == EMPTY_VALUE) return EMPTY_VALUE;
   return (hi + lo) / 2.0;
}
''',
    "FibPos": '''
// Where the close sits in the last confirmed swing range: 0 at the low, 1 at the
// high, outside [0,1] on a break-out. A zero-width range is undefined.
double FibPos(const int shift, const int lookback)
{
   double hi = SwingPivot(shift, lookback, true), lo = SwingPivot(shift, lookback, false);
   if(hi == EMPTY_VALUE || lo == EMPTY_VALUE || hi == lo) return EMPTY_VALUE;
   return (iClose(_Symbol, _Period, shift) - lo) / (hi - lo);
}
''',
    "Mss": '''
// Market Structure Shift: the first BOS whose direction OPPOSES the previous one.
// features/__init__.py carries `prev` forward from bar 0; walking back to the
// nearest EARLIER non-zero BOS is the same statement read from the other end.
double Mss(const int shift, const int lookback)
{
   double b = Bos(shift, lookback);
   if(b == 0.0) return 0.0;
   int total = Bars(_Symbol, _Period);
   for(int i = shift + 1; i < total && i <= shift + InpStructureBars; i++)
   {
      double previous = Bos(i, lookback);
      if(previous != 0.0) return previous != b ? b : 0.0;
   }
   return 0.0;   // no earlier BOS in range, so prev is 0 and this is not a shift
}
''',
    "OrderBlock": '''
// 50% level of the nearest UNMITIGATED order block -- features/__init__.py::
// _order_blocks. A bullish OB is the last down-close candle before an up-move that
// DISPLACES (breaks the confirmed swing high, or a body >= disp * ATR); cleared
// once price fully sweeps back through the zone. The ATR is the Python's exact
// atr_14 (ewm seeded on the first true range), recursed inside this same walk --
// iATR seeds on an SMA and would arm the displacement test on different candles.
double OrderBlock(const int shift, const int lookback, const double disp, const bool want_bull)
{
   int last = (int)MathMin(Bars(_Symbol, _Period) - 1, shift + InpStructureBars);
   double b_lo = EMPTY_VALUE, b_hi = EMPTY_VALUE, s_lo = EMPTY_VALUE, s_hi = EMPTY_VALUE;
   double d_lo = EMPTY_VALUE, d_hi = EMPTY_VALUE, u_lo = EMPTY_VALUE, u_hi = EMPTY_VALUE;
   double atr = 0.0, prev_close = EMPTY_VALUE, out = EMPTY_VALUE;
   int seen = 0;
   for(int i = last; i >= shift; i--)
   {
      double o = iOpen(_Symbol, _Period, i), h = iHigh(_Symbol, _Period, i);
      double l = iLow(_Symbol, _Period, i), c = iClose(_Symbol, _Period, i);
      double range = prev_close == EMPTY_VALUE ? h - l
         : MathMax(h - l, MathMax(MathAbs(h - prev_close), MathAbs(l - prev_close)));
      atr = seen == 0 ? range : atr + (range - atr) / 14.0;
      seen++;
      prev_close = c;
      double atr_value = seen >= 14 ? atr : EMPTY_VALUE;
      if(c > o && d_lo != EMPTY_VALUE)
      {
         double sw = SwingPivot(i, lookback, true);
         bool broke = sw != EMPTY_VALUE && c > sw;
         bool big = atr_value != EMPTY_VALUE && (c - o) >= disp * atr_value;
         if(broke || big) { b_lo = d_lo; b_hi = d_hi; }
      }
      if(c < o && u_lo != EMPTY_VALUE)
      {
         double sw = SwingPivot(i, lookback, false);
         bool broke = sw != EMPTY_VALUE && c < sw;
         bool big = atr_value != EMPTY_VALUE && (o - c) >= disp * atr_value;
         if(broke || big) { s_lo = u_lo; s_hi = u_hi; }
      }
      if(b_lo != EMPTY_VALUE && l < b_lo) { b_lo = EMPTY_VALUE; b_hi = EMPTY_VALUE; }
      if(s_hi != EMPTY_VALUE && h > s_hi) { s_lo = EMPTY_VALUE; s_hi = EMPTY_VALUE; }
      out = EMPTY_VALUE;
      if(want_bull) { if(b_lo != EMPTY_VALUE) out = (b_hi + b_lo) / 2.0; }
      else          { if(s_hi != EMPTY_VALUE) out = (s_hi + s_lo) / 2.0; }
      // an up-close bar can only arm a BULL block from a previous DOWN-close
      // candle, so a candle is never its own order block
      if(c < o)      { d_lo = l; d_hi = h; }
      else if(c > o) { u_lo = l; u_hi = h; }
   }
   return out;
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
# helpers that replay a structural state machine over a bounded history window
_STRUCTURE_HELPERS = {"SwingPivot", "Bos", "Cisd", "Equilibrium", "FibPos", "Mss",
                      "OrderBlock"}

_HELPERS = {**_LEVEL_HELPERS, **_FEATURE_HELPERS}

# MQL5 wants a function defined before it is called, so emission order is fixed
# here rather than sorted: dependencies first. The four level helpers keep the
# alphabetical order they have always been emitted in.
_HELPER_ORDER = [
    "LevelBb", "LevelFractal", "LevelSession", "LevelSwing",
    "Fvg", "LiquiditySweep", "OpeningRange", "PopEma", "Psar", "RsiFast",
    "SwingPivot", "Bos", "Cisd", "Equilibrium", "FibPos", "Mss", "OrderBlock",
]
assert set(_HELPER_ORDER) == set(_HELPERS)

_SMC_HELPER = {"bos": "Bos", "mss": "Mss", "cisd": "Cisd",
               "equilibrium": "Equilibrium", "ob_bull_mid": "OrderBlock",
               "ob_bear_mid": "OrderBlock"}


def _feature_helpers(features: set[str]) -> set[str]:
    """The helper functions the emitted feature lines call."""
    wanted = {"bb_line": "LevelBb", "rsi_fast": "RsiFast", "psar": "Psar",
              "opening_range": "OpeningRange", "fvg": "Fvg",
              "liquidity_sweep": "LiquiditySweep", "htf_trend": "PopEma",
              "fib_pos": "FibPos"}
    used = {helper for name, helper in wanted.items() if name in features}
    for name in features:
        knob = smc_knob(name)
        if knob:
            used.add(_SMC_HELPER[knob[0]])
    if used & {"Bos", "Mss", "Equilibrium", "FibPos", "OrderBlock"}:
        used.add("SwingPivot")
    if "Mss" in used:
        used.add("Bos")
    return used


def _helper_source(names: set[str]) -> str:
    return "".join(_HELPERS[key] for key in _HELPER_ORDER if key in names) + (
        "\n" if names else "")


def _level_helper_names(names: set[str]) -> set[str]:
    return {_level_call(name, "0").split("(")[0] for name in names}


def _exit_anchor(spec: StrategySpec, rule, path: str) -> str:
    name = anchor_level(spec, rule.anchor)
    if name not in _SUPPORTED_LEVELS:
        raise Mql5Unsupported(f"MQL5 target does not implement level-feature {name!r} ({path})")
    return name


def _exit_call(name: str, resting: bool = False) -> str:
    """The level call an EXIT prices from, at the bar the compiler anchors to.

    For a MARKET entry that bar is the one the trade opens on -- shift 0. For a
    RESTING order it is the bar the order was PLACED on, which is the signal bar,
    shift 1: `try_open`'s `anchor_i` is `rest_anchor_i`, so 1R is settled when
    the order is sent and not wherever the level has drifted to by the fill.

    Both are then shifted one further bar for bb_line and the two fractals, which
    read their own bar's OHLC: popper/dsl/compiler.py's `exit_anchor` shifts
    exactly those three (INTRA_BAR_LEVELS) before resolving an exit.

    ENTRY anchors stay at shift 1 (the signal bar) and are never shifted again --
    so a spec anchoring both to the same intra-bar level reads two different
    bars, which is the point.
    """
    shift = (1 if resting else 0) + (1 if name in INTRA_BAR_LEVELS else 0)
    return _level_call(name, str(shift))


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
                         if feature not in _SUPPORTED and not _EMA.match(feature)
                         and smc_knob(feature) is None)
    if unsupported:
        raise Mql5Unsupported(f"MQL5 target does not implement features: {', '.join(unsupported)}")
    if spec.wrapper.stop.type not in ("atr", "level"):
        raise Mql5Unsupported(f"MQL5 target does not implement stop type {spec.wrapper.stop.type!r}")
    if spec.wrapper.target.type not in ("ratio", "level"):
        raise Mql5Unsupported(f"MQL5 target does not implement target type {spec.wrapper.target.type!r}")
    if spec.wrapper.trail.type not in ("none", "atr"):
        raise Mql5Unsupported(f"MQL5 target does not implement trail type {spec.wrapper.trail.type!r}")
    if spec.wrapper.break_even.type not in ("none", "after_r"):
        raise Mql5Unsupported(
            f"MQL5 target does not implement break-even type {spec.wrapper.break_even.type!r}")
    if spec.wrapper.constraints.no_trade:
        # NOT a Forge gap: popper/dsl/compiler.py never reads no_trade, so the
        # backtest traded straight through those windows. An EA that honoured the
        # tag would trade a different strategy from the one that was falsified.
        raise Mql5Unsupported(
            "MQL5 target refuses constraints.no_trade: the backtest does not enforce it "
            "(needs the news calendar), so emitting it would deploy a strategy that "
            "trades differently from the one that passed the gauntlet")


def _reject_unsupported(spec) -> None:
    """Refuse to emit code that would trade differently from the backtest."""
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
    if helpers & _STRUCTURE_HELPERS:
        # ponytail: CALIBRATION KNOB. The SMC state machines run from the first bar
        # of the dataset in Python; an EA replays this much history instead. It must
        # comfortably exceed the spacing between confirmed pivots at the lookback in
        # use -- a large bos_<n> on a thin feed needs a larger window.
        inputs.append("input int InpStructureBars = 1000;")
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
    for name in sorted(features):
        knob = smc_knob(name)
        if not knob:
            continue
        base, n = knob
        if base in ("bos", "mss", "cisd"):
            call = f"{_SMC_HELPER[base]}(1, {n})"
        elif base == "equilibrium":
            call = f"Equilibrium(1, {n})"
        else:
            call = (f"OrderBlock(1, {n}, {_number(OB_DISP_MULT)}, "
                    f"{'true' if base == 'ob_bull_mid' else 'false'})")
        feature_lines.append(f"   double {name} = {call};")
    if "fib_pos" in features:
        feature_lines.append(f"   double fib_pos = FibPos(1, {SWING_LOOKBACK});")
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

    w = spec.wrapper
    entry = w.entry
    resting = entry.type != "market"
    level_exit = w.stop.type == "level" or w.target.type == "level"
    # A level stop/target is an ABSOLUTE price. A resting order anchors it to the
    # placement bar (`_exit_call(..., resting=True)`), so the same value serves
    # the order that is sent and the position it becomes; `held` replays the
    # stored one at the fill instead of re-reading the level on the fill bar.
    stop_level = target_level = None
    if w.stop.type == "level":
        stop_level = (f"{_exit_call(_exit_anchor(spec, w.stop, 'wrapper.stop'), resting)}"
                      f" + ({_wrapper_value(w.stop.offset)})")
    if w.target.type == "level":
        target_level = (f"{_exit_call(_exit_anchor(spec, w.target, 'wrapper.target'), resting)}"
                        f" + ({_wrapper_value(w.target.offset)})")

    def stop_at(price: str, held: str | None = None) -> str:
        if w.stop.type != "level":
            return f"{price} + (is_long ? -1.0 : 1.0) * atr * {stop_mult}"
        return held or stop_level

    def target_at(price: str, held: str | None = None) -> str:
        if w.target.type != "level":
            return f"{price} + (is_long ? 1.0 : -1.0) * stop_distance * {rr}"
        return held or target_level

    tighten = spec.wrapper.trail.type == "atr" or spec.wrapper.break_even.type == "after_r"
    tighten_globals = tighten_helper = ""
    if tighten:
        moves = []
        if spec.wrapper.break_even.type == "after_r":
            moves += [
                "   // favourable excursion on the last COMPLETED bar, measured against the",
                "   // ORIGINAL 1R -- never the already-trailed distance",
                "   double favourable = is_long ? iHigh(_Symbol, _Period, 1) - entry",
                "                               : entry - iLow(_Symbol, _Period, 1);",
                f"   if(favourable >= {_wrapper_value(spec.wrapper.break_even.trigger_r)} * managed_risk)",
                "      next_stop = is_long ? MathMax(next_stop, entry) : MathMin(next_stop, entry);",
            ]
        if spec.wrapper.trail.type == "atr":
            moves += [
                "   double atr = BufferValue(hATR, 1);",
                "   if(atr != EMPTY_VALUE && MathIsValidNumber(atr))",
                "   {",
                "      double trailed = iClose(_Symbol, _Period, 1)"
                f" - (is_long ? 1.0 : -1.0) * {_wrapper_value(spec.wrapper.trail.mult)} * atr;",
                "      next_stop = is_long ? MathMax(next_stop, trailed)"
                " : MathMin(next_stop, trailed);",
                "   }",
            ]
        tighten_globals = "\ndouble managed_risk = 0.0;"
        if not resting:
            tighten_globals = "\ndatetime managed_entry = 0;" + tighten_globals
        tighten_helper = '''
// compiler.py tightens the stop AFTER its exit checks, from THAT bar's data, so a
// tightened stop only binds from the NEXT bar. This EA acts once per new bar, so
// shift 1 IS that bar and the modified stop is live for the bar now opening --
// the same timing. Both moves only ever tighten.
// ponytail: managed_risk is captured the first time a position is seen. Restart
// the EA mid-trade and it is recaptured from the CURRENT stop, so a break-even
// that has already moved would re-arm early. Flat restarts are unaffected.
void TightenStop()
{{
   if(!PositionSelect(_Symbol) || managed_risk <= 0.0) return;
   bool is_long = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
   double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   double stop = PositionGetDouble(POSITION_SL);
   double target = PositionGetDouble(POSITION_TP);
   double next_stop = stop;
{moves}
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   next_stop = NormalizeDouble(next_stop, digits);
   if(next_stop != stop) trade.PositionModify(_Symbol, next_stop, target);
}}
'''.format(moves=chr(10).join(moves))
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
        if level_exit:
            pending_globals += "\ndouble pending_stop = 0.0;\ndouble pending_target = 0.0;"
            reprice = f'''
// A gap can fill a resting order better (limit) or worse (stop) than its price, and
// the backtest measures 1R from the FILL, so re-anchor once the fill is known. A
// LEVEL stop/target is not re-read here: compiler.py resolves it from the PLACEMENT
// bar (try_open's anchor_i), which is the bar the order -- and so its volume -- was
// committed on. Recomputing the level now would price the trade from a bar the
// order was never sent with.
void RepriceStops(const double atr)
{{
   // ponytail: restarting the EA mid-order loses the placement levels. Leaving the
   // broker's own stops alone is the safe fallback -- they were set from those very
   // values when the order went in.
   if(atr <= 0.0 || pending_stop == 0.0 || pending_target == 0.0) return;
   double fill = PositionGetDouble(POSITION_PRICE_OPEN);
   bool is_long = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
   double stop = {stop_at("fill", "pending_stop")};
   double stop_distance = MathAbs(fill - stop);
   double target = {target_at("fill", "pending_target")};
   // GUARD, same as try_open: a stop or target on the wrong side of the FILL is an
   // incoherent trade the backtest never opened. The fill already happened, so
   // flatten at once rather than hold a position the hypothesis never took.
   if(!MathIsValidNumber(stop) || !MathIsValidNumber(target)
      || (is_long ? !(stop < fill && target > fill) : !(stop > fill && target < fill)))
   {{
      trade.PositionClose(_Symbol);
      return;
   }}
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   trade.PositionModify(_Symbol, NormalizeDouble(stop, digits), NormalizeDouble(target, digits));
}}
'''
            sizing = f'''   // 1R off the PLACEMENT bar, which is where compiler.py's anchor_i reads it and
   // the only timing a broker can size an order from
   double stop = {stop_at("price")};
   if(stop == EMPTY_VALUE || !MathIsValidNumber(stop)) return false;
   double stop_distance = MathAbs(price - stop);
   double target = {target_at("price")};
   if(target == EMPTY_VALUE || !MathIsValidNumber(target)) return false;
   if(is_long ? !(stop < price && target > price) : !(stop > price && target < price))
      return false;
   double lots = RiskLots(stop_distance);
   if(lots <= 0.0) return false;
   pending_stop = stop;
   pending_target = target;
   stop = NormalizeDouble(stop, digits);
   target = NormalizeDouble(target, digits);'''
        else:
            reprice = f'''
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
'''
            sizing = f'''   double stop_distance = atr * {stop_mult};
   double reward_distance = stop_distance * {rr};
   double lots = RiskLots(stop_distance);
   if(lots <= 0.0) return false;
   double stop = NormalizeDouble(price + (is_long ? -stop_distance : stop_distance), digits);
   double target = NormalizeDouble(price + (is_long ? reward_distance : -reward_distance), digits);'''
        pending_helpers = f'''{reprice}
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
{sizing}
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
        ] + ([
            "         // 1R, captured before anything tightens the stop",
            "         managed_risk = MathAbs(PositionGetDouble(POSITION_PRICE_OPEN)"
            " - PositionGetDouble(POSITION_SL));",
        ] if tighten else []) + [
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

    if level_exit:
        # when the entry rests, this function is only reachable from PlacePending's
        # already-through-the-price branch, on the tick that placed the order -- so
        # shift 1 is still the anchor bar there
        anchor_bar = (
            "   // resolved on the PLACEMENT bar -- shift 1, the bar whose close fired\n"
            "   // the signal -- which is where compiler.py's try_open reads\n"
            "   // stop_lvl[anchor_i] once an order rests."
            if resting else
            "   // resolved on the bar the trade OPENS on -- shift 0, the bar whose first tick\n"
            "   // fired this call -- exactly where compiler.py's try_open reads stop_lvl[i]."
        )
        open_position = f'''bool OpenPosition(const bool is_long, const double atr)
{{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double price = SymbolInfoDouble(_Symbol, is_long ? SYMBOL_ASK : SYMBOL_BID);
{anchor_bar}
   double stop = {stop_at("price")};
   if(stop == EMPTY_VALUE || !MathIsValidNumber(stop)) return false;
   double stop_distance = MathAbs(price - stop);
   double target = {target_at("price")};
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

    if tighten and not resting:
        position_inner += [
            "      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);",
            "      if(opened != managed_entry)",
            "      {",
            "         managed_entry = opened;",
            "         // 1R, captured before anything tightens the stop",
            "         managed_risk = MathAbs(PositionGetDouble(POSITION_PRICE_OPEN)"
            " - PositionGetDouble(POSITION_SL));",
            "      }",
        ]
    if spec.wrapper.time_stop.type == "bars":
        max_bars = _wrapper_value(spec.wrapper.time_stop.max_bars)
        position_inner += [
            "      // completed bars since the fill. The broker stop/target run intrabar, so a",
            "      // bar that breaches the stop AND expires the clock is already out as a stop.",
            f"      if(iBarShift(_Symbol, _Period, (datetime)PositionGetInteger(POSITION_TIME)) - 1 >= (int){max_bars})",
            "         trade.PositionClose(_Symbol);",
        ]
        if tighten:
            # the backtest tightens only when the time stop did NOT close the trade
            position_inner += ["      else", "         TightenStop();"]
    elif tighten:
        position_inner.append("      TightenStop();")

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
int day_key = -1;{pending_globals}{tighten_globals}

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
{pending_helpers}{tighten_helper}
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
