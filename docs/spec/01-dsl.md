# 01 — Strategy DSL + Compiler

The spine. One safe, composable representation so an alpha means the same thing in research, simulation,
and live. Built first (highest learning, and everything else compiles through it).

## The Alpha / Wrapper / Context split (the key change vs the shipped repo)
The shipped `StrategySpec` is monolithic. It must become three composable parts:

```yaml
alpha:                      # the edge/signal ONLY — no execution, no sizing
  id: A-0007
  hypothesis_id: H-0012
  features: [atr_14, ema_50, session_ny, opening_range]
  higher_timeframe_features: {15m: [ema_50]}
  signal: "close > ema_50 AND htf_m15_ema_50 > close AND atr_14 > p[atr_min]"
  # an alpha emits a directional signal (+1/-1/0). It does NOT decide TP/SL/size.

wrapper:                    # execution — turns an alpha into a strategy
  entry:  { type: market }
  stop:   { type: atr, mult: p[sl_atr] }
  target: { type: rr,  ratio: p[rr] }
  trail:  { type: none }           # atr | psar | chandelier | none
  break_even: { type: none }
  sizing: { risk_pct: 0.5 }
  constraints: { max_trades_per_day: 3, no_trade: [news_high_impact] }

context:                    # assumed + tagged, NOT searched
  universe: [US30]
  timeframe: 5m
  cost_model: systematic    # systematic (light) | hft (heavy slippage/spread)

param_space:                # the grid the runner/falsifier probes (wrapper params live here)
  atr_min: [0.5, 1.0, 1.5]
  sl_atr:  [1.0, 1.5, 2.0]
  rr:      [1.5, 2.0, 2.5, 3.0]
```

**Why the split:** an alpha is judged raw first (does the signal predict forward returns? — `03` cheap
screen); only survivors get wrapper optimisation; wrappers transfer within a behavioural category (`04`).
This mirrors the JJC insight *"trade management cannot create edge, only preserve or reduce it."*

## Grammar & validation
- **DECISION (recommended):** authored in **YAML**, validated by **Pydantic** models (extends the existing
  `popper/artifacts/strategy_spec.py`), compiled to a typed intermediate representation (IR). Bounded,
  composable primitives — **not** full program synthesis. Grammar grows deliberately and is versioned
  (`dsl_version`). Rationale: safe (no arbitrary code in the loop), machine-generable by the Synthesis
  agent, diff-able/versionable in alpha memory.
- **Validator rejects:** unknown features, unsafe expressions, look-ahead references (a feature that reads
  future bars), missing required blocks, param_space that references undefined params, and higher contexts
  that are not strict integer multiples of the base timeframe.
- **Feature library** (`popper/dsl/features/`) — the only names an alpha may reference. Seed set from the
  JJC feature vocabulary: `atr_14`, `ema_{n}`, `session_{ny,london,asia}`, `opening_range`, `rsi_fast`,
  `htf_trend`, `bb_line`, `psar`, `liquidity_sweep`, `candle_size`. Each feature is a pure function
  `bars -> series`, point-in-time correct (no future leak — enforced, see `03`).

## Multi-timeframe context

`context.timeframe` is the one base execution timeframe. An alpha may bind feature-library primitives to
M5/M15 through `higher_timeframe_features`; signals reference aliases such as `htf_m15_ema_50`. A higher
bar is aligned only after its close (`bar open + timeframe duration <= base bar close`). This keeps
confluence inside one hypothesis and prevents an in-progress higher bar from leaking into a base signal.
See `docs/MULTI_TIMEFRAME_DSL.md` for the executable contract and trial accounting.

## Three compile targets — all Python; **Popper never generates MQL5**
- `compile_to_backtest(spec)` → a **pure-Python** vectorised/event-driven backtester over the US30 Parquet
  bars (the Experiment Runner). **No MT5 / Strategy Tester** — deliberate: the loop must run thousands of
  backtests fast, in parallel, cloud-runnable. Popper computes features, fires the DSL signal, applies the
  wrapper, and simulates fills on the bars itself.
- `compile_to_sim(spec)` → realistic fills, latency, costs (execution realism — resamples the `.tkc` ticks later).
- `compile_to_live(spec)` → DSL signal computed in Python; orders placed via the **MT5 Python bridge**
  (`order_send`). US30 MFT/systematic is bar-close, so bridge latency is fine. NinjaTrader adapter later.

> **Why no MQL5 (the code-generation concern).** Generating correct MQL5 per alpha would be huge, error-prone,
> and would bottleneck discovery on the GUI Strategy Tester (one slow run at a time, Windows-bound). So
> research backtests run in **Python** (any alpha the DSL expresses) and live runs via the **Python bridge** —
> no per-alpha codegen, ever. A promoted survivor *may optionally* be mapped onto JJC's existing **fixed,
> parameterised EA** via a `.set` file (params only) where it fits — reuse, not a requirement.
> **Phase-2 validation (done-when):** confirm the Python backtester reproduces a known JJC MT5 result for the
> same alpha + wrapper + period within tolerance, so the engine is trusted before the loop relies on it.

## Proof of expressiveness (done-when)
Hand-author 3 alphas in the DSL — including one **JJC-style** (e.g. TrendEng entry as the alpha, its Stage-1
SL/TP as the wrapper) — and confirm all 3 validate and compile to runnable objects. Source sketch:
`docs/ARCHITECTURE.md` §4; JJC feature/param vocabulary: the validation-framework `optimizer/walk_forward_config.py`.
