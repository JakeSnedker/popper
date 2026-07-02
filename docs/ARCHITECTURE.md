# Popper — Architecture

## 1. Design principle: contracts before code

and research pipelines."* So the spine of Popper is a set of **immutable artifacts** that flow between
**stateless subsystems**. Each subsystem reads one artifact type and writes another. Swap any subsystem's
internals without touching the others. State lives in artifacts + alpha memory, never in the loop.

```
Hypothesis ──> StrategySpec ──> ExperimentResult ──> Scorecard ──> Verdict
   (agent)        (DSL)          (runner)          (metrics)     (gauntlet)
      ^                                                              |
      └───────────────── feedback (learned constraints) ────────────┘
```

## 2. The artifacts (data contracts)
These are the interfaces. Define them in Phase 0; everything else plugs in.

### Hypothesis
```
id              H-0001
claim           "NY-open momentum on US30 persists for 15-30m after a >0.8 ATR opening drive"
mechanism       liquidity / order-flow rationale (why this SHOULD exist)
required_features  [atr_14, session_ny, opening_range]
expected_regime    trending / high-vol
novelty_score   0.0-1.0  (distance from nearest prior hypothesis in alpha memory)
status          proposed | testing | killed | survived | promoted
parent_id       lineage — which prior verdict spawned this
```

### StrategySpec  (an instance of the DSL — see §4)
```
id, hypothesis_id, dsl_version
universe, timeframe, features[]
signal{}, entry{}, exit{}, sizing{}, constraints{}
param_space{}   # the grid the runner/falsifier will probe
```

### ExperimentResult
```
id, spec_id, dataset_id, period
trades[], equity_curve, raw_metrics{pnl, sharpe, win_rate, trades_n, ...}
```


```
experiment_id
robustness          # spread of performance across CPCV folds
regime_stability    # per-regime Sharpe (does it survive regime change?)
capacity            # PnL decay vs position size (slippage curve)
execution_realism   # backtest vs modeled-fill gap
novelty             # from alpha memory
deflated_sharpe     # Sharpe corrected for # of trials + non-normality
pbo                 # probability of backtest overfitting
```

### Verdict
```
SURVIVE        passed every gate
KILL(reasons)  e.g. [pbo>0.5, regime_stability_fail, leakage_detected]
PROMOTE        survived + cleared promotion criteria
```

## 3. The subsystems

| # | Subsystem | Reads → Writes | Notes |
|---|-----------|----------------|-------|
| 1 | **Data/Feature layer** (`data/`) | raw MT5 → feature store | OHLCV/tick ingest, session tagging, regime labels. Mostly known territory. |
| 2 | **Hypothesis Engine** (`research/hypothesis/`) | memory → Hypothesis | Claude proposes a *falsifiable* mechanism. Novelty-gated against alpha memory. |
| 3 | **Synthesis Agent** (`research/synthesis/`) | Hypothesis → StrategySpec | Claude expresses the claim in the DSL. |
| 4 | **DSL + Compiler** (`dsl/`) | StrategySpec → executable | Validates + compiles to 3 targets (backtest/sim/live). **The safety boundary.** |
| 5 | **Experiment Runner** (`experiments/`) | StrategySpec → ExperimentResult | Compiles + backtests. Reuses JJC backtest concepts. |
| 6 | **Scorecard** (`scorecard/`) | ExperimentResult → Scorecard | Vector metrics, not a number. |
| 7 | **Falsification Gauntlet** (`falsify/`) | Result+Scorecard → Verdict | The moat. Tries to *disprove* alpha. Mirrors JJC staged gates. |
| 8 | **Alpha Memory** (`memory/`) | everything | Supabase + embeddings. Prevents re-exploration; powers novelty. |
| 9 | **Orchestrator** (`orchestrator/`) | conducts the loop | Schedules cycles, routes artifacts, manages budget/stop, feedback. |
| 10 | **Promotion / Production** (`promote/`) | Verdict(PROMOTE) → live | Paper → live, drift monitor, kill switch. VPS/Docker. |

## 4. The Strategy DSL (sketch)
Declarative, safe, human-readable, and intentionally close to JJC's param mental model so it's tangible.
One artifact runs in research, simulation, and live.

```yaml
strategy:
  name: ny_open_momentum
  hypothesis_id: H-0012
  universe: [US30]
  timeframe: 5m
  features:
    - atr_14
    - ema_50
    - session: NY
  signal:
    entry_long: "close > ema_50 AND session == NY_open AND atr_14 > p[atr_min]"
  entry:  { type: market }
  exit:
    stop:   { type: atr, mult: p[sl_atr] }
    target: { type: rr,  ratio: p[rr] }
  sizing: { risk_pct: 0.5 }
  constraints:
    max_trades_per_day: 3
    no_trade: [news_high_impact]
  param_space:
    atr_min: [0.5, 1.0, 1.5]
    sl_atr:  [1.0, 1.5, 2.0]
    rr:      [1.5, 2.0, 2.5, 3.0]
```

**Compiler targets**
- `compile_to_backtest(spec)` → vectorized/event-driven backtest
- `compile_to_sim(spec)`      → realistic fills, latency, costs
- `compile_to_live(spec)`     → MT5 EA params / signal feed (NinjaTrader adapter later)

**Why a DSL and not just Python functions:** safety (no arbitrary code in the loop), one canonical
representation across all three runtimes, machine-generable by the synthesis agent, and diff-able /
versionable in alpha memory.

## 5. Orchestration — the autonomous loop
```python
while budget.remaining() and not converged():
    h = hypothesis_engine.propose(memory)         # novel, falsifiable claim
    if memory.is_stale(h):                         # already explored / killed
        continue
    spec = synthesis_agent.compile(h)              # Hypothesis -> DSL
    if not dsl.validate(spec):
        memory.record_invalid(h, spec); continue
    result  = experiment_runner.run(spec)          # backtest
    score   = scorecard.evaluate(result)
    verdict = falsify.judge(result, score)         # the gauntlet
    memory.record(h, spec, result, score, verdict) # everything, always
    if verdict.is_promote():
        promote.queue(spec)
    else:
        hypothesis_engine.learn(verdict)           # feed failure back
```

Key properties:
- **Closed loop:** failures sharpen the next hypothesis (the "self-improving" claim).
- **No re-exploration:** novelty gate + alpha memory enforce it.
- **Auditable:** every cycle is a row; you can replay the lab's entire reasoning history.
- **Pluggable:** each subsystem is swappable behind its artifact contract.

## 6. The JJC → Popper mapping (your existing edge)
| JJC BOT V13 concept | Becomes in Popper |
|---|---|
| Per-magic `Predifenedsettings()` params | DSL `param_space` + sizing/exit blocks |
| 5-stage staged optimizer | Ordering logic for the falsification gauntlet & regime discovery |
| Gates between stages ("stop if no edge") | KILL verdicts — fail fast, cheaply |
| Param stability at 1.3/1.5/1.7 | Automated parameter-surface robustness test |
| "Expectancy > win rate" heuristic | A scorecard rule, not a human judgment call |
| Walk-forward windows | Purged/embargoed CV + CPCV in `falsify/` |
