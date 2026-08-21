"""Deterministic StrategySpec to MetaTrader 5 Expert Advisor generator."""

import re

from popper.artifacts import StrategySpec
from popper.forge.expr import compile_expression, input_name

_SUPPORTED = {
    "atr_14", "session_ny", "rel_volume", "delta_proxy", "vol_regime",
    "trend_regime", "regime_score", "cusum_shift",
}
_EMA = re.compile(r"^ema_(\d+)$")
_PARAM = re.compile(r"^p\[(\w+)\]$")


class Mql5Unsupported(ValueError):
    pass


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
    if spec.wrapper.stop.type != "atr" or spec.wrapper.target.type != "ratio":
        raise Mql5Unsupported("MQL5 target currently requires ATR stop and ratio target")
    if spec.wrapper.trail.type != "none" or spec.wrapper.break_even.type != "none":
        raise Mql5Unsupported("MQL5 target does not yet support trail or break-even wrappers")
    if spec.wrapper.constraints.no_trade:
        raise Mql5Unsupported("MQL5 target does not yet support no_trade constraints")


def _reject_unsupported(spec) -> None:
    """Refuse to emit code that would trade differently from the backtest.

    Offset-priced resting entries and time exits are rendered below. An ANCHORED
    resting order is not: its price is a level-feature series, and MQL5
    implements no level features at all -- the same reason `_validate` rejects a
    level stop or target. Falling back to an offset price would deploy a
    strategy nobody tested.
    """
    entry = spec.wrapper.entry
    if entry.type != "market" and entry.anchor:
        raise Mql5Unsupported(
            f"MQL5 target cannot price a {entry.type} entry off level-feature "
            f"{entry.anchor!r}: level features are not implemented for this target"
        )


def generate_mql5(spec: StrategySpec) -> str:
    _reject_unsupported(spec)
    _validate(spec)
    features = set(spec.alpha.features)
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
    for name, grid in sorted(spec.param_space.items()):
        inputs.append(f"input double {input_name(name, 'mql5')} = {_number(grid[len(grid) // 2])};")

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
    if "session_ny" in features:
        feature_lines += [
            "   MqlDateTime utc;",
            "   TimeToStruct(iTime(_Symbol, _Period, 1) + InpBrokerToUtcHours * 3600, utc);",
            "   double session_ny = (utc.hour >= 13 && utc.hour < 21) ? 1.0 : 0.0;",
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
   double price = NormalizeDouble(signal_close {price_sign} (is_long ? 1.0 : -1.0) * {offset} * atr, digits);
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

    if spec.wrapper.time_stop.type == "bars":
        max_bars = _wrapper_value(spec.wrapper.time_stop.max_bars)
        position_inner += [
            "      // completed bars since the fill. The broker stop/target run intrabar, so a",
            "      // bar that breaches the stop AND expires the clock is already out as a stop.",
            f"      if(iBarShift(_Symbol, _Period, (datetime)PositionGetInteger(POSITION_TIME)) - 1 >= (int){max_bars})",
            "         trade.PositionClose(_Symbol);",
        ]

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

bool OpenPosition(const bool is_long, const double atr)
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
}}
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
