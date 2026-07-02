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
  signal: "close > ema_50 AND session == NY_open AND atr_14 > p[atr_min]"
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
  future bars), missing required blocks, param_space that references undefined params.
- **Feature library** (`popper/dsl/features/`) — the only names an alpha may reference. Seed set from the
  JJC feature vocabulary: `atr_14`, `ema_{n}`, `session_{ny,london,asia}`, `opening_range`, `rsi_fast`,
  `htf_trend`, `bb_line`, `psar`, `liquidity_sweep`, `candle_size`. Each feature is a pure function
  `bars -> series`, point-in-time correct (no future leak — enforced, see `03`).

## Three compile targets
- `compile_to_backtest(spec)` → vectorised/event-driven callable over the US30 Parquet bars (the Experiment Runner).
- `compile_to_sim(spec)` → realistic fills, latency, costs (execution realism — uses ticks later).
- `compile_to_live(spec)` → MT5 EA params / signal feed (NinjaTrader adapter behind the same interface later).

## Proof of expressiveness (done-when)
Hand-author 3 alphas in the DSL — including one **JJC-style** (e.g. TrendEng entry as the alpha, its Stage-1
SL/TP as the wrapper) — and confirm all 3 validate and compile to runnable objects. Source sketch:
`docs/ARCHITECTURE.md` §4; JJC feature/param vocabulary: the validation-framework `optimizer/walk_forward_config.py`.
