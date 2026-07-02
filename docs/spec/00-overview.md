# 00 — System Overview

## The problem Popper solves
A self-improving discovery system **tunnels**: as the memory of tried ideas grows, generation collapses
into a narrow, already-explored region and novelty decays. The goal is to keep producing genuinely novel,
*uncorrelated* edges while still exploiting what already works — under finite compute. This is an
**exploration-vs-exploitation + resource-allocation** problem. (Source: `design-source/42_System_Proposal.html`.)

## Design principles
1. **Falsification-first.** A strategy must survive honest attempts to *disprove* it (out-of-sample,
   robustness, leakage, multiple-testing correction) — not merely show a good backtest.
2. **Alpha × Wrapper × Context.** Decompose every strategy (see `01-dsl.md`). Discover **alphas**, then
   optimise **wrappers** per **context** — this tames the combinatorial explosion.
3. **Single source of truth.** One alpha memory holds every hypothesis, result, and behaviour signature;
   each agent gets a role-scoped *view*, not a separate DB.
4. **Generation is performance-blind.** The hypothesis agent never sees profitability — it cannot copy
   winners and tunnel. Performance enters only downstream (exploit + allocation).
5. **The map's empty space is the target.** Existing alphas are used not just to de-dup, but to reveal the
   **whitespace** worth exploring.

## Core decomposition — Alpha · Wrapper · Context
- **Alpha** = the edge/signal (the reason it makes money).
- **Wrapper** = execution (TP/SL/trailing/exits/sizing) — turns an alpha into a strategy; *not* an alpha.
- **Context** = market + timeframe (US30 MFT/systematic first).
- **Strategy = Alpha × Wrapper × Context.**

> **This is exactly the JJC split.** In JJC BOT V13 the *entry pattern* (magic number) is the **alpha**;
> the *5-stage optimizer* (SL/TP → filters → trade-management) is the **wrapper**. Popper generalizes it.

## The pipeline (an alpha's journey)
`1 Generate → 2 Gate & categorise → 3 Cheap screen → 4 Falsify → 5 Record (+behaviour) → 6 Re-categorise → 7 Exploit → ↺ feedback (map steers next Generate)`

Under every stage sits the **Strategy DSL** — one safe representation so an alpha means the same thing in
research, simulation, and live.

## Two engines, one dial (the explore/exploit solution)
- **Explore engine** — pumps novel alphas into the map (Hypothesis + Wildcard + Gatekeeper + gauntlet). Long loop.
- **Exploit engine** — mines paying categories (Workers). Short inner loop: refine → re-falsify → record.
- **The Director** allocates compute between them and holds the explore/exploit dial. See `02-agents.md`.

## Artifacts (immutable data contracts — Phase 0, already built)
`Hypothesis → StrategySpec(DSL) → ExperimentResult → Scorecard → Verdict`, flowing between stateless
subsystems. State lives in artifacts + alpha memory, never in the loop. (Built: `popper/artifacts/`.)
**Reconciliation note:** the shipped `StrategySpec` is monolithic — it must gain the Alpha/Wrapper/Context
split (`01`), and `Scorecard` must gain the behaviour-signature + correlation fields (`04`).

## Scope now
- **Context:** US30 only (NAS100 data empty). MFT/systematic emphasis.
- **Build target:** the full design. **Build order:** thin-loop-first → agents → explore/exploit → portfolio (`06`).
- **Models:** Fable 5 builds; runtime = Opus 4.8 (Director/Synthesis) + Sonnet (Hypothesis/Wildcard).


multi-agent pipeline (`02`) · Strategy DSL+compiler (`01`) · falsification-first eval (`03`) · alpha memory
(`04`) · metrics beyond PnL — robustness/regime/capacity/execution-realism/novelty (`03`,`04`) ·

