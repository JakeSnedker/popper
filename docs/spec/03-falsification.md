# 03 — The Falsification Gauntlet (the moat)


backbone already exists as the **JJC validation-framework** — we **port** its methodology + statistics,
**add** the López de Prado multiple-testing layer it lacks, and **drop** its MT5-tester coupling.

Source code (read before building): `C:\Users\Jakes\Projects\GitHub\quantitative-strategy-validation-framework\`
→ `METHODOLOGY.md`, `optimizer/walk_forward_config.py`, `optimizer/monte_carlo.py`,
`optimizer/analyze_edge.py`, `optimizer/docs/OPTIMIZATION_SYSTEM_V2.md`.

## Gate order (fail fast, cheapest first)
The JJC "gates between stages — stop if no edge" logic generalizes directly:

0. **Baseline-edge screen.** Test the alpha under a **fixed canonical baseline wrapper** — SL = 2×ATR,
   TP = 2R (JJC's Stage-1 baseline) — held constant so we isolate the *alpha's* contribution, not the
   wrapper's. The baseline wrapper is what makes the edge measurable: it bounds each trade so PnL/expectancy
   is defined and comparable across alphas. "Raw edge" means the wrapper is *fixed at baseline*, **not
   absent** — an unbounded signal has no tradeable edge to measure. Kill alphas with no edge under the
   baseline before spending compute; only survivors get **wrapper optimisation** (Stage 2+). *(Optional
   ultra-cheap pre-filter first: signal ↔ N-bar-forward-return correlation — pure predictive power, no
   trades.)* JJC: "trade management cannot create edge, only preserve/reduce it" — so a fixed baseline
   wrapper is the fair test bed for the alpha.
1. **Leakage checks.** Look-ahead, train/test contamination, survivorship, target leakage. Root fix =
   point-in-time-correct data + **purged & embargoed cross-validation**. *(Jake's known weak spot — own it
   as its own gate. NOT in JJC — add it.)*
2. **Walk-forward (out-of-sample).** Anchored WF windows; performance must generalize to unseen data.
3. **Robustness / parameter stability.** Edge holds across a *neighbourhood* of params (1.3 / 1.5 / 1.7),
   not one knife-edge point.
4. **Monte Carlo ruin.** Trade-order resampling → P(profit), P(ruin), drawdown distribution.
5. **Multiple-testing correction.** Large-scale search *manufactures* false alpha → correct for the number
   of trials: **Deflated Sharpe Ratio** + **PBO** (Probability of Backtest Overfitting, via CSCV). *(NOT in
   JJC — add it; this is what makes the moat real at large scale.)*

Routed by `context.cost_model`: **hft** → heavy slippage/spread/microstructure; **systematic** (US30 now)
→ regime + robustness emphasis.

## PORT from the validation-framework (reuse the Python ~as-is)
| Source | Reuse |
|--------|-------|
| `walk_forward_config.py` → `PassFailCriteria` | The Verdict gate thresholds (below) — lift into Popper's Scorecard→Verdict rules |
| `walk_forward_config.py` → `WalkForwardSettings.calculate_windows()` | The WF window scheduler — reuse directly |
| `monte_carlo.py` | MC ruin: P(profit)/P(ruin)/DD distribution |
| `analyze_edge.py` → `calculate_edge()` | **Expected Edge = (OOS_PF − 1) × trades** — opportunity-aware selection (a moderate PF with many trades can beat a high PF with few) |
| `METHODOLOGY.md` → Expectancy | `E = (Win% × AvgWin) − (Loss% × AvgLoss)`; min sample sizes (100+ trades / ~150 at 30% WR) |
| staged filter-vs-baseline ordering | The gauntlet's fail-fast ordering **and** the exploit-worker refinement loop (`02`) |

## Verdict gate thresholds (default, from `PassFailCriteria` — tune later)
min forward PF **1.0** (ideal 1.3) · max DD **10%** (ideal 5%) · min trades/window **20** (ideal 40) ·
min walk-forward efficiency **0.5** (ideal 0.7) · min win rate **25%** · max MC P(ruin) **15%** (ideal 5%) ·
min Sharpe **0.5** (ideal 1.0) · min recovery **1.0** (ideal 2.0) · **parameter stability:** adjacent step
profitable, max PF variance across steps **30%**, params win in **>50%** of windows.
WF windows default: **4mo optimise / 2mo forward / 2mo step**, min 12 months, min 4 windows.

## ADD (not in JJC — implement fresh)
Deflated Sharpe Ratio · PBO via CSCV · purged & embargoed CV / CPCV · explicit leakage detectors.
Reference camp: **Marcos López de Prado — *Advances in Financial Machine Learning***. Library: implement
from the definitions (small, well-documented formulas); no heavy dependency required.

## DROP (do NOT port — Popper runs its own backtest)
MT5 Strategy-Tester driving: `mt5_controller.py`, `set_file_generator.py`, `terminal_config.py`,
`mt5_optimization.py`. Popper's own `compile_to_backtest` (`01`) over the US30 Parquet bars replaces the MT5
tester. JJC's concrete filter clusters / EA params (Stage-2 phases 1–15) are **JJC-EA-specific** → they
become **DSL examples** (`01`), not hardcoded gates.

## Output
Emits the `Scorecard` (vector metrics: robustness, regime stability, capacity, execution realism, novelty,
deflated Sharpe, PBO) → `Verdict` (SURVIVE | KILL(reasons) | PROMOTE). Every result, pass or fail, is
recorded to the map (`04`).
