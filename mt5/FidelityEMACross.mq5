//+------------------------------------------------------------------+
//|                                            FidelityEMACross.mq5 |
//|          Matched-pair fidelity benchmark EA for the Popper engine|
//|   Pairs with: examples/hypotheses/fidelity_ema_cross.yaml        |
//|   Compare:    python scripts/fidelity_diff.py                    |
//+------------------------------------------------------------------+
//
// PURPOSE
//   The strategy logic below is PROVABLY IDENTICAL to the Popper DSL spec
//   fidelity_ema_cross.yaml, so any difference between this EA's Strategy
//   Tester results and Popper's run(spec) is PURELY a fill-model difference
//   (spread, intrabar tick path, exit quantization). That difference is the
//   only thing the benchmark measures.
//
// MATCHED SEMANTICS (mirrors popper/dsl/compiler.py CompiledStrategy.__call__)
//   * Signal evaluated on the just-completed bar's CLOSE:
//       open[1] < EMA50[1]  AND  close[1] > EMA50[1]     (long-only "cross")
//     (Popper's DSL grammar has no lag operator, so "cross above" is encoded
//     on both sides as: the bar's body straddles the current EMA-50.)
//   * Market entry at the NEXT bar's open  (= first tick of the new bar).
//   * SL = fill_price - 2 * ATR14(signal bar)   [anchored to the ACTUAL fill]
//   * TP = fill_price + 2R = fill_price + 4 * ATR14(signal bar)
//   * One position at a time; a signal while a position is open is IGNORED
//     (not queued) — exactly like the compiler (step 4 requires pos == 0).
//   * No trailing stop, no break-even, no daily trade cap.
//   * If ATR is not yet formed (fewer than 14 completed bars) the entry is
//     SKIPPED and the signal dropped, matching the compiler's NaN-ATR guard.
//   * Sizing does not affect fills; a fixed lot stands in for risk_pct=0.5.
//
// INDICATOR MATH — deliberately NOT iMA / iATR (their seeding differs):
//   Popper: ema_50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
//     -> seed:  ema = close of the FIRST processed bar
//     -> step:  ema += (close - ema) * 2/(50+1)
//     -> valid: only after 50 completed bars (min_periods)
//   Popper: atr_14 = TR.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
//     -> TR[first] = high - low (no prev close), else Wilder true range
//     -> seed:  atr = TR of the FIRST processed bar
//     -> step:  atr += (TR - atr) / 14
//     -> valid: only after 14 completed bars
//   Both recursions are replicated verbatim below, seeded at the FIRST
//   completed bar at/after the tester start date — Popper seeds its features
//   at the first bar of the sliced frame, so use the SAME start date in the
//   tester and in fidelity_diff.py --start.
//
// RECOMMENDED STRATEGY TESTER SETTINGS
//   Symbol: US30 (same feed that produced data/us30/US30_M1.parquet)
//   Period: M1
//   Dates:  identical to the fidelity_diff.py --start/--end window
//   Model:  FIRST PASS -> "Open prices only".  Entries are exact (they only
//           ever happen at bar opens on both sides), but SL/TP exits are then
//           only evaluated at bar opens, so exit prices quantize to opens.
//           SECOND PASS -> "1 minute OHLC" or "Every tick based on real
//           ticks" for realistic intrabar exits.
//   KNOWN CAVEAT (unavoidable, document — do not "fix"): when one bar spans
//           BOTH the stop and the target, Popper always fills the STOP first
//           (conservative). MT5's tick models replay an intrabar path (e.g.
//           O-L-H-C on an up bar) that can hit the TP first. Those trades
//           will show as exit-reason flips with matched entries — a fill-
//           model difference, not a logic mismatch.
//   Spread: Popper's benchmark run is frictionless; the tester's spread is
//           part of the fill gap being measured. For a pure-logic shakeout,
//           test with a fixed minimal spread first.
//
// CSV EXPORT
//   On tester finish (OnDeinit), every closed trade is written to
//   InpCsvName in the COMMON files dir:
//     %APPDATA%\MetaQuotes\Terminal\Common\Files\  (FILE_COMMON)
//   Columns: entry_time, entry_price, exit_time, exit_price, direction,
//            exit_reason   (exit_reason: stop | target | eod)
//
#property copyright "Popper fidelity benchmark"
#property version   "1.00"

#include <Trade\Trade.mqh>

input double InpLots    = 1.0;                      // fixed lot (sizing does not affect fills)
input long   InpMagic   = 771001;                   // magic number
input string InpCsvName = "fidelity_ema_cross.csv"; // export file (common Files dir)

CTrade g_trade;

// --- Popper-replicated indicator state (recursions, NOT iMA/iATR) ---------
#define EMA_N 50
#define ATR_N 14
#define SL_ATR_MULT 2.0
#define RR 2.0

double   g_ema        = 0.0;   // EMA(50), Popper seeding
double   g_atr        = 0.0;   // Wilder ATR(14), Popper seeding
double   g_prev_close = 0.0;   // previous completed bar's close (for TR)
long     g_bars_done  = 0;     // completed bars processed since seed
datetime g_seed_time  = 0;     // first bar-open time seen (= tester start bar)
datetime g_last_bar   = 0;     // last bar-0 open time (new-bar detector)

//+------------------------------------------------------------------+
int OnInit()
  {
   g_trade.SetExpertMagicNumber(InpMagic);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| True while an EA position exists on this symbol/magic            |
//+------------------------------------------------------------------+
bool HasPosition()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic)
         return(true);
     }
   return(false);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   datetime bar0 = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar0 == g_last_bar) return;          // act only on the first tick of a new bar
   g_last_bar = bar0;

   if(g_seed_time == 0)                    // very first tick: bar[1] predates the
     {                                     // tester start -> Popper never saw it
      g_seed_time = bar0;
      return;
     }

   // --- the just-completed bar (Popper's "bar i", signal bar) -------------
   datetime t1 = iTime(_Symbol, PERIOD_CURRENT, 1);
   if(t1 < g_seed_time) return;            // safety: never process pre-start bars
   double o1 = iOpen(_Symbol, PERIOD_CURRENT, 1);
   double h1 = iHigh(_Symbol, PERIOD_CURRENT, 1);
   double l1 = iLow(_Symbol, PERIOD_CURRENT, 1);
   double c1 = iClose(_Symbol, PERIOD_CURRENT, 1);

   // --- update EMA/ATR with the completed bar (Popper recursions) ---------
   double tr = (g_bars_done == 0)
               ? (h1 - l1)                                            // TR[first]: no prev close
               : MathMax(h1 - l1, MathMax(MathAbs(h1 - g_prev_close),
                                          MathAbs(l1 - g_prev_close)));
   if(g_bars_done == 0)
     {
      g_ema = c1;                          // ewm(adjust=False) seeds at first value
      g_atr = tr;
     }
   else
     {
      g_ema += (c1 - g_ema) * (2.0 / (EMA_N + 1.0));
      g_atr += (tr - g_atr) / (double)ATR_N;
     }
   g_prev_close = c1;
   g_bars_done++;

   // min_periods: features are NaN (signal cannot fire, ATR invalid) until formed
   bool ema_ok = (g_bars_done >= EMA_N);
   bool atr_ok = (g_bars_done >= ATR_N) && (g_atr > 0.0);

   // --- signal on the completed bar's close -> market entry NOW (new bar open)
   if(HasPosition()) return;               // signal while in a position: ignored
   if(!ema_ok) return;
   if(!(o1 < g_ema && c1 > g_ema)) return; // "close crosses above ema_50"
   if(!atr_ok) return;                     // compiler guard: NaN/zero ATR -> skip

   if(!g_trade.Buy(InpLots, _Symbol))
     {
      PrintFormat("Buy failed: %d %s", g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
      return;
     }

   // anchor SL/TP to the ACTUAL fill price, exactly like the compiler
   // (e = fill; stop = e - 2*ATR; risk = 2*ATR; target = e + 2*risk)
   if(PositionSelect(_Symbol))
     {
      double e    = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl   = e - SL_ATR_MULT * g_atr;
      double tp   = e + RR * SL_ATR_MULT * g_atr;    // = e + 4*ATR
      int    digs = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
      if(!g_trade.PositionModify(_Symbol, NormalizeDouble(sl, digs), NormalizeDouble(tp, digs)))
         PrintFormat("PositionModify failed: %d %s", g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
     }
  }

//+------------------------------------------------------------------+
//| Deal reason -> Popper exit-reason vocabulary                     |
//+------------------------------------------------------------------+
string ExitReason(long reason)
  {
   if(reason == DEAL_REASON_SL) return("stop");
   if(reason == DEAL_REASON_TP) return("target");
   return("eod");   // tester force-close at end / anything else
  }

//+------------------------------------------------------------------+
//| Export every closed trade to CSV (common Files dir)              |
//+------------------------------------------------------------------+
void ExportTrades()
  {
   if(!HistorySelect(0, TimeCurrent() + 86400)) return;

   int fh = FileOpen(InpCsvName, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(fh == INVALID_HANDLE)
     {
      PrintFormat("CSV open failed: %d", GetLastError());
      return;
     }
   FileWrite(fh, "entry_time", "entry_price", "exit_time", "exit_price", "direction", "exit_reason");

   // pair DEAL_ENTRY_IN with its DEAL_ENTRY_OUT via the position id
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong in_ticket = HistoryDealGetTicket(i);
      if(in_ticket == 0) continue;
      if(HistoryDealGetInteger(in_ticket, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
      if(HistoryDealGetInteger(in_ticket, DEAL_MAGIC) != InpMagic) continue;
      if(HistoryDealGetString(in_ticket, DEAL_SYMBOL) != _Symbol) continue;

      long pos_id = HistoryDealGetInteger(in_ticket, DEAL_POSITION_ID);
      for(int j = i + 1; j < total; j++)
        {
         ulong out_ticket = HistoryDealGetTicket(j);
         if(out_ticket == 0) continue;
         if(HistoryDealGetInteger(out_ticket, DEAL_POSITION_ID) != pos_id) continue;
         if(HistoryDealGetInteger(out_ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

         FileWrite(fh,
                   TimeToString((datetime)HistoryDealGetInteger(in_ticket, DEAL_TIME),
                                TIME_DATE | TIME_MINUTES | TIME_SECONDS),
                   DoubleToString(HistoryDealGetDouble(in_ticket, DEAL_PRICE), 2),
                   TimeToString((datetime)HistoryDealGetInteger(out_ticket, DEAL_TIME),
                                TIME_DATE | TIME_MINUTES | TIME_SECONDS),
                   DoubleToString(HistoryDealGetDouble(out_ticket, DEAL_PRICE), 2),
                   "long",
                   ExitReason(HistoryDealGetInteger(out_ticket, DEAL_REASON)));
         break;
        }
     }
   FileClose(fh);
   PrintFormat("Fidelity CSV written to Common\\Files\\%s", InpCsvName);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ExportTrades();
  }

//+------------------------------------------------------------------+
double OnTester()
  {
   return(0.0);   // export happens in OnDeinit (runs in tester too)
  }
//+------------------------------------------------------------------+
