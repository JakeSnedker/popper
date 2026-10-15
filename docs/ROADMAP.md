# Popper — Roadmap

**Strategy:** Build the DSL first (highest learning), drive fast to a *tangible thin full loop*, then
deepen the technical pieces (backtesting, CPCV, execution realism) you already understand well.

**Tangibility milestone:** end of Phase 5 = a human-seeded research loop you can watch run.
**Autonomy milestone:** end of Phase 7 = agents close the loop unattended.

---

## Phase 0 — Foundations & contracts  *(scaffold)*
**Goal:** the interfaces exist before any logic.
- Repo skeleton (`data/ dsl/ research/ experiments/ scorecard/ falsify/ memory/ orchestrator/ promote/`).
- Define the five artifacts as typed schemas (Pydantic): Hypothesis, StrategySpec, ExperimentResult,
  Scorecard, Verdict.
- Supabase schema for alpha memory (tables: hypotheses, specs, experiments, scorecards, verdicts).
- Config + env (MT5 creds, Supabase keys, Anthropic key) — no hardcoded secrets.
**Done when:** schemas import, a dummy artifact round-trips to Supabase and back.

## Phase 1 — Strategy DSL + Compiler  *(your requested start)*
**Goal:** a safe, structured strategy representation that validates and compiles.
- DSL schema (the `strategy:` spec) + validator (reject unsafe/ill-formed specs).
- Feature reference library (atr, ema, session, opening_range, …) the DSL can name.
- `compile_to_backtest(spec)` → an executable callable (against a stub data frame first).
- Hand-author 3 known strategies in the DSL (incl. a JJC-style one) to prove expressiveness.
**Done when:** 3 specs validate and compile to runnable objects.

## Phase 2 — Data layer + Experiment Runner
**Goal:** a DSL strategy produces real PnL on real MT5 data.
- MT5 ingest for US30 / NAS100 → clean OHLCV + session/regime tags → local feature store.
- Experiment Runner: spec → backtest over a period → ExperimentResult (trades, equity, raw metrics).
- Reuse JJC backtesting concepts; keep it behind the runner interface.
**Done when:** `run(spec)` returns a populated ExperimentResult on US30.

## Phase 3 — Falsification Gauntlet v0
**Goal:** automatically kill false alpha.
- Gates: purged/embargoed CV, **Deflated Sharpe**, **PBO**, parameter-stability surface.
- Leakage checks (look-ahead, train/test contamination).
- Emits Verdict = SURVIVE | KILL(reasons).
- Structure the gate ordering on JJC's staged-gate "fail fast" logic.
**Done when:** a deliberately overfit spec is correctly KILLED; a robust one SURVIVES.

## Phase 4 — Alpha Memory + Scorecard
**Goal:** nothing is forgotten; strategies are scored as vectors.
- Persist every Hypothesis/Spec/Result/Verdict to Supabase.
- Embedding fingerprint per hypothesis → novelty score + `is_stale()` re-exploration guard.
- Scorecard: robustness, regime stability, capacity (v0), novelty, deflated Sharpe, PBO.
**Done when:** re-submitting a known idea is flagged stale; each experiment has a scorecard row.

## Phase 5 — Manual full loop  *(TANGIBILITY MILESTONE)*
**Goal:** you hand-write a hypothesis, the system does the rest end-to-end.
- Wire: Hypothesis (human) → Synthesis (template/stub) → DSL → Runner → Scorecard → Gauntlet → Memory.
- A CLI: `popper run --hypothesis hypo.yaml` prints the verdict + scorecard.
**Done when:** one command takes a hypothesis to a stored, scored, judged result.

## Phase 6 — Research agents
**Goal:** Claude generates the hypotheses and the DSL.
- Hypothesis Engine agent (falsifiable claim + mechanism + features), novelty-gated.
- Synthesis Agent (Hypothesis → valid DSL), with compiler-feedback repair loop.
**Done when:** agents produce specs that pass DSL validation without human edits.

## Phase 7 — Orchestrator  *(AUTONOMY MILESTONE — thin full loop ALIVE)*
**Goal:** the loop runs itself.
- Cycle scheduler, artifact routing, budget/stop logic, failure→feedback wiring.
- Run N autonomous cycles on US30; inspect the alpha-memory trail.
**Done when:** unattended run produces hypotheses, kills most, surfaces ≥1 survivor candidate.

---

## Phase 8+ — Deepen (post-thin-loop)
Prioritize after the loop is alive:
- **CPCV** (combinatorial purged CV) + full robustness distribution.
- **Regime discovery** — auto-label regimes, score per-regime stability (use JJC staged ordering).
- **Capacity & execution realism** — slippage curves, latency/fill modeling, `compile_to_sim`.
- **Promotion pipeline** — survivor → paper → live, drift monitor (live vs backtest), kill switch.
- **Live adapter** — `compile_to_live` to MT5 EA; later NinjaTrader/Futures behind same interface.
- **Multi-agent debate** — critic agent challenges hypotheses pre-test to save compute.

---

## Sequencing logic
- Contracts (P0) first so every later phase plugs in without rework.
- DSL (P1) before runner so the runner has something canonical to execute.
- Falsification (P3) before agents (P6) — never let agents promote junk; the gauntlet must exist first.
- Memory (P4) before autonomy (P7) — autonomy without memory just re-explores forever.
- Manual loop (P5) before agentic loop (P6/7) — prove the pipe with a human in the seat, then automate.

## Portfolio value at each checkpoint
- After **P1**: "I designed a Strategy DSL + compiler for safe research→live representation." (systems design)
- After **P3**: "I built a falsification framework that kills overfit/leaked alpha automatically." (the moat)
- After **P5**: a live demo of a research pipeline producing scored, judged results.
- After **P7**: an autonomous discovery agent, end to end.
