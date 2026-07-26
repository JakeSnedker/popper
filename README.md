# Popper — Autonomous Research Platform

> *"A theory that is not refutable by any conceivable event is non-scientific."* — Karl Popper

Popper is a self-improving system that **discovers, falsifies, and promotes** trading strategies
with minimal human intervention. The moat is not any single strategy — it's the **discovery engine**
that generates them, kills the false ones before they cost money, and never re-explores a dead idea.


research-to-production stack — hypothesis generation → strategy DSL → falsification → alpha memory →
multi-metric evaluation → live promotion.

## Why "Popper"
The core engineering bet is **falsification-first**: a strategy must *survive* a gauntlet of attempts
to disprove it, not merely show a pretty backtest. This is Popperian science applied to alpha discovery,


## Current target
- **Data/execution:** MetaTrader 5 — US30, NAS100 (indices). Portable to Futures (NinjaTrader) later.
- **Agents:** role-routed official Claude.ai and ChatGPT/Codex subscriptions.
- **Store:** SQLite locally or shared Supabase alpha memory.
- **Status:** working autonomous research system. See `docs/NORTH_STAR.md`,
  `docs/LLM_ROUTING.md`, and `docs/SELF_LEARNING_ROADMAP.md`.

## The loop, in one breath
Propose a falsifiable hypothesis → express it in the Strategy DSL → backtest → run the falsification
gauntlet → score on a vector of metrics (not just PnL) → record everything to alpha memory → promote
survivors, feed verdicts back to the hypothesis engine. Repeat autonomously.

## Reuse from prior work (JJC BOT V13)
- JJC's per-magic parameter structure → seeds the **DSL schema**.
- JJC's 5-stage staged optimizer with gates → structural blueprint for the **falsification gauntlet**.
- JJC's overfitting heuristics (parameter stability at 1.3/1.5/1.7, expectancy > win-rate) → encoded as
  **automated falsification tests**.
