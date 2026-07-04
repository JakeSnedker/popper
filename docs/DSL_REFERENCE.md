# Popper Strategy DSL — Reference (v0.2)

The DSL is the safety layer of the whole system. Popper never runs free-form
code and **never generates MQL5**: every strategy is a validated `StrategySpec`
object, compiled to a pure-Python backtest. This document describes that spec
exactly as the code implements it — schema, feature library, signal grammar,
and the fill/timing model — plus an honest list of what it does *not* yet do.

Source of truth: `popper/artifacts/strategy_spec.py`, `popper/dsl/` (`expr.py`,
`validator.py`, `compiler.py`, `features/__init__.py`).

---

## 1. The core idea: Strategy = Alpha × Wrapper × Context

A strategy is decomposed into three orthogonal parts. This is the load-bearing
abstraction — it lets the falsifier isolate *where* an edge lives.

| Part | Owns | Can it create edge? |
|------|------|---------------------|
| **Alpha** | the signal — *when* to be long/short | **Yes** — this is the edge |
| **Wrapper** | execution — entry, stop, target, trail, break-even, sizing, constraints | **No** — only shapes risk/return of an existing edge |
| **Context** | universe, timeframe, cost model — *where & when* | No — assumed and tagged, not searched |

Consequence used all over the gauntlet: if an alpha has a real edge but the
strategy still fails (e.g. ruin risk), the fix is in the **wrapper**, not the
alpha. The same alpha with a different wrapper is a different strategy.

---

## 2. The `StrategySpec` schema

Every field, its type, and default (from `strategy_spec.py`):

```yaml
id:            null            # assigned by alpha memory as S-#### when stored
hypothesis_id: "H-...."        # the claim this spec expresses (required)
dsl_version:   "0.2"
name:          "my_strategy"   # required

alpha:                         # THE EDGE
  features: [ema_50, atr_14]   # feature-library names only (see §3)
  signal:   "close > ema_50"   # boolean expression (see §4) — fires long (+1)
  signal_short: null           # optional; fires short (-1). null = long-only

wrapper:                       # EXECUTION (all blocks optional; defaults shown)
  entry:      { type: market }                 # fills at NEXT bar open
  stop:       { type: atr,  mult: 2.0 }        # stop distance = mult × ATR(14)
  target:     { type: rr,   ratio: 2.0 }       # take-profit at ratio × risk
  trail:      { type: none }                   # none | atr (mult: 2.0)
  break_even: { type: none }                   # none | after_r (trigger_r: 1.0)
  sizing:     { risk_pct: 0.5 }                # % equity risked per trade
  constraints:
    max_trades_per_day: null                   # int cap, or null = unlimited
    no_trade: []                               # tags (NOT enforced yet — see §7)

context:                       # WHERE & WHEN (tagged, not searched)
  universe:   [US30]
  timeframe:  5m
  cost_model: systematic       # systematic | hft  (DECLARED, not applied — §7)

param_space:                   # the grid the runner + falsifier sweep
  sl_atr: [1.0, 1.5, 2.0]      # every p[name] used above MUST appear here,
  rr:     [1.5, 2.0, 2.5]      # and every key here MUST be referenced
```

### Numeric wrapper fields are literal-or-parameter
Any numeric wrapper field (`stop.mult`, `target.ratio`, `trail.mult`,
`break_even.trigger_r`) accepts **either** a literal number **or** a
`"p[name]"` string that references a `param_space` grid. That is how a wrapper
becomes searchable:

```yaml
stop:   { type: atr, mult: "p[sl_atr]" }   # searched over param_space.sl_atr
target: { type: rr,  ratio: 2.0 }          # fixed
```

### The baseline wrapper
`BASELINE_WRAPPER = Wrapper()` (SL = 2×ATR, TP = 2R, 0.5% risk, no trail/BE) is
the canonical fixed wrapper the gauntlet's early gates use so an alpha's raw
edge is measurable and comparable across alphas.

---

## 3. The feature library

Features are the **only** vocabulary a signal may reference. Each is a pure
`bars -> pd.Series` function, **point-in-time correct**: the value at bar *i*
uses only bars `0..i`. `tests/test_dsl.py` has a prefix-invariance check that
fails if any feature leaks the future. `bars` needs columns `time, open, high,
low, close` (`tick_volume` aliased to `volume`).

**11 named features + the `ema_<n>` family:**

| Feature | Computes | Notes |
|---------|----------|-------|
| `atr_14` | Wilder ATR, period 14 | NaN until 14 bars formed |
| `ema_<n>` | EMA of close, span *n* (1–1000) | e.g. `ema_50`, `ema_200`; NaN until *n* bars |
| `rsi_fast` | Wilder RSI, period 7 | seeded to 50 before formed |
| `bb_line` | Bollinger middle = SMA(20) | *(DECISION: "bb_line" defined as the mid-line)* |
| `htf_trend` | `sign(EMA48 − EMA240)` on native series | *(DECISION: HTF **proxy**, not a true H1 resample)* |
| `psar` | Parabolic SAR (af 0.02 → 0.2) | forward-only loop |
| `opening_range` | H–L range of first 6 M5 bars of the NY session | *(DECISION: anchored to NY cash open; NaN until formed, then constant for the day)* |
| `session_ny` | 1.0 during 13:00–21:00 UTC else 0.0 | *(DECISION: sessions on UTC bar time)* |
| `session_london` | 1.0 during 07:00–13:00 UTC | |
| `session_asia` | 1.0 during 00:00–07:00 UTC | |
| `liquidity_sweep` | +1 bullish sweep of prior 20-bar low & reclaim, −1 mirror, else 0 | stop-run pattern |
| `candle_size` | `high − low` of the bar | raw range |

**Not in the library** (despite appearing in some older docs): `vwap`,
`overnight_gap`, `ema_10/20` are *not* pre-declared — only what's in the table
above. `ema_<n>` covers any span you name.

An unknown feature name is rejected by the validator before any compute.

---

## 4. The signal grammar

A signal is a **bounded boolean expression** — deliberately *not* program
synthesis. It is parsed to a Python AST and statically checked (`expr.py`).

**Allowed:**
- feature names (declared in `alpha.features`) and bar columns `open, high, low, close, volume`
- numeric literals
- `p[name]` parameter references
- arithmetic `+ - * /`
- comparisons `> >= < <= == !=`
- boolean `AND OR NOT` (and parentheses)

**Forbidden — and *why*:**
- **Function calls** (`shift(...)`, `abs(...)`, anything `(...)`) → rejected. This is the structural look-ahead guard: with no calls, there is literally *no way to express* `shift(-1)`, a lead, or future indexing. Look-ahead is prevented by the **absence of the construct**, not by a rule that scans for it.
- attribute access (`x.y`), non-`p[...]` subscripts, non-numeric constants → rejected.

Example (from `ny_open_momentum`):
```
session_ny > 0 AND close > ema_50 AND opening_range > p[or_min] * atr_14
```

**Evaluation semantics:** vectorised to a boolean Series. NaN feature values
compare **False** — a signal never fires on an unformed feature (e.g. before
ATR has 14 bars). `signal` → long entries; optional `signal_short` → short
entries.

---

## 5. The compile & execution model

`compile_to_backtest(spec)` returns a callable `(bars, params) -> result`. The
event loop (`compiler.py`) enforces a strict no-look-ahead timing model:

1. **Signal evaluates on a bar's CLOSE.** A fired signal becomes a *pending*
   entry for the next bar.
2. **Market entry fills at the NEXT bar's open** (`o[i]`). Stop distance is
   `sl_atr × ATR(as of the signal bar)`; target is `rr × risk` from entry.
3. **Within a bar, stop is checked before target** (conservative — if both the
   stop and target sit inside the bar's range, the stop wins).
4. **Gaps fill at the open:** a long stopped out fills at `min(open, stop)`, a
   target at `max(open, target)` — i.e. a gap through your level fills at the
   *worse* open price, never the ideal level.
5. **Trail / break-even update AFTER exit checks**, from the current bar's data,
   so they only tighten *future* bars — never retroactively.
6. **One position at a time.** No pyramiding; a new signal is ignored while a
   position is open. Long and short are mutually exclusive.
7. `max_trades_per_day` caps entries per calendar day (UTC).
8. A position still open at the last bar closes at the final close (`reason: eod`).

**Sizing / equity:** each trade's result is `pnl_r = (exit − entry) / risk ×
direction` (in **R-multiples**). Equity compounds as `equity *= 1 + risk_pct/100
× pnl_r`. All downstream metrics (PF, expectancy, Sharpe, DSR, MC-ruin) are in R
and are therefore broker/account-size independent.

**Three compile targets by design** (only the first is built):
- `compile_to_backtest` → pure-Python research backtest **(built — what the loop uses)**
- `compile_to_sim` → tick-level sim for capacity/realism **(planned)**
- `compile_to_live` → live via the MT5 Python bridge (`order_send`) **(planned)**

---

## 6. Metrics produced (per experiment)

`raw_metrics` (from `runner.compute_metrics`, JJC METHODOLOGY definitions):
`pnl` (fractional return), `profit_factor`, `sharpe` (per-trade, unannualised),
`win_rate`, `max_drawdown`, `expectancy` (R), `recovery_factor`, `trades_n`,
`avg_win`, `avg_loss`, plus the full `trades[]` list. These feed the Scorecard
and the gauntlet.

---

## 7. Known gaps & deliberate simplifications (v0.2)

Be honest about these when reading a verdict — several bias results **optimistic**:

- **⚠ No transaction costs.** `context.cost_model` is declared (`systematic`/`hft`)
  but the backtester **never reads it** — no spread, slippage, or commission is
  applied, even though the US30 data carries a real `spread` column. Fills are at
  exact mid/open prices. On a many-trade M5 strategy this is a systematic upward
  bias on every metric; a marginal `PF ≈ 1.09` could go sub-1.0 with real costs.
  **This is the single most important thing to close before trusting a SURVIVE.**
- **`execution_realism` scorecard field is never populated** by the gauntlet
  (only `seed.py` sets demo values) — the one metric that would flag the cost
  gap is itself a stub.
- **`constraints.no_trade` is recorded but not enforced** (needs a news calendar).
- **`trail` supports only `none | atr`** (psar/chandelier deferred).
- **`htf_trend` and `bb_line` are proxies** (EMA-diff sign; SMA-20) — not true
  higher-timeframe resampling / full Bollinger bands.
- **Single position, no pyramiding; long/short mutually exclusive.**
- **Default params = middle of each grid** (a representative point; the falsifier
  probes the full grid).

These are tracked as `# DECISION:` / `# ponytail:` markers in the code.

---

## 8. Worked example

`examples/hypotheses/ny_open_momentum.yaml` — a complete, real idea:

```yaml
name: ny_open_momentum
alpha:
  features: [session_ny, opening_range, atr_14, ema_50]
  signal: "session_ny > 0 AND close > ema_50 AND opening_range > p[or_min] * atr_14"
wrapper:
  stop:   { type: atr, mult: "p[sl_atr]" }
  target: { type: rr,  ratio: "p[rr]" }
  sizing: { risk_pct: 0.5 }
  constraints: { max_trades_per_day: 3 }
context: { universe: [US30], timeframe: 5m }
param_space:
  or_min: [1.0, 1.5, 2.0]
  sl_atr: [1.0, 1.5, 2.0]
  rr:     [1.5, 2.0, 2.5, 3.0]
```

Reads as: *during the NY session, when price is above the 50-EMA and the day's
opening range was wide relative to ATR, join the drive long; stop at
`sl_atr×ATR`, target `rr×`risk, ≤3 trades/day.* The gauntlet sweeps the
3×3×4 = 36-combo grid across walk-forward windows and the full period.
