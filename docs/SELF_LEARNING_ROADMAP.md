# Popper — Self-Learning Roadmap (v2)

**Goal (priority 1):** find a *real, cost-surviving, tradeable* strategy — a bot Jake can make
money with. **Priority 2:** a portfolio showcase. The two reinforce; build for #1.

> **THIS DOC IS THE SOURCE OF TRUTH FOR THE PLAN.** Status legend per step:
> **[DONE+TEST]** built and covered by the test suite · **[DONE]** built, proven once
> (calibration/smoke), not in the suite · **[TODO]** not built · **[HUMAN]** needs Jake.
> Forward sequence Jake set (2026-07-15): **strong US30 base → single shared DB + visualize →
> expand the DSL (reasonably) → run live 1 month → containerize → only then multi-instrument.**
> Rationale: don't multiply a shaky base across 3 machines × 4 instruments. See "Forward plan" below.

**North-star principle:** the falsification gauntlet is a *bullshit detector*, not an alpha
generator. Every self-improvement the system finds must re-clear the full gauntlet (incl.
parameter-stability) or it just curve-fits the in-sample window faster. Overfitting guards are
built in from the first line, never bolted on.

**Architecture split (already enforced structurally, keep it):**
- **Explore = clean & performance-blind.** The Hypothesis agent sees the map (coverage / whitespace /
  prior claims) via `BlindView`, never results. This is the whole point of novelty — do not leak
  performance into generation.
- **Exploit = the self-learner.** Workers hold `FullView` and iteratively improve near-miss edges.
  This is the half that's currently a stub (`_mutate` = flat +10% param nudge) and the main build.

---

## Phase A — Trust the engine  *(foundation; nothing downstream is real without it)*

- **A1 — MT5 fidelity matched-pair.** `[DONE]` — diff = 92.9% match; entry delta +2.53pts = spread
  (systematic, not a bug); `point_value` calibrated to 0.012. Every-tick real-data re-run deferred to
  pre-deploy (changes no verdict). *Proven once via the diff harness, not a suite test.*
  A dead-simple EMA-cross strategy written *identically* as a
  Popper DSL spec **and** a hand-written ~40-line MQL5 EA (replicating Popper's exact ATR/EMA
  formulas, not MT5 built-ins). Run both on the same M1 data; a diff harness separates *logic
  mismatch* (should be ~zero) from *fill slippage* (the thing we measure). Calibrates `point_value`.
  Hand-written EA on purpose — Strategy Forge is deferred so the benchmark stays trustworthy; the
  harness later doubles as Forge's codegen validator.
- **A2 — Cost as a NON-GATING recorded metric.** `[DONE+TEST]` (test_costs, 5). *(revised — see decision below)*
  Compute net-of-cost numbers (`expectancy_net`, `profit_factor_net`, `avg_cost_r`,
  `pct_trades_cost_flip`) alongside the gross ones. The gauntlet keeps passing/failing on **gross**
  (frictionless) edge. Cost is informational at the explore stage — **no gate depends on it.**

> **DECISION (2026-07-13): cost is not a hard gate at explore scale.**
> At hundreds-of-alphas throughput (not millions), auto-killing on cost would discard exactly the
> raw material the exploit engine exists to rescue — a marginal edge that a wrapper swap, param push,
> or regime filter turns cost-positive. Cost hits strategies very differently (a 200-trade/day
> scalper vs a 2-trade/day swing feel the same spread completely differently). So: **cost is recorded
> everywhere, gates nothing until promotion.** The hard cost requirement lives only at Phase D/E
> (final candidate book). B2 then *targets* cost-killed edges as prime rescue candidates.

## Phase B — The self-learning exploit engine  *(the heart of it)*

- **B1 — Real guided local optimizer.** `[DONE+TEST]` (test_optimizer, 6). Greedy coordinate search;
  every candidate re-clears the full gauntlet incl. friction + parameter-stability.
- **B2 — Near-miss cluster selection.** `[DONE+TEST]` (test_selection, 4). cost-killed (gross+/net−)
  ranks top tier; refine objective is `expectancy_net`.
- **B3 — Deepen the refine operator.** `[DONE+TEST]` (in test_optimizer). *Reinterpreted from the
  original "meta-learning bias" — Jake's critique was about DEPTH, not search order.* Adds
  wrapper-structural moves (trail on/off, break-even on/off, mult/trigger steps) alongside param
  shifts; alpha never touched; ≤24 evals; done-entry carries move_log depth instrumentation.
  The original **meta-learning bias** (weight moves by what historically worked) is deferred `[TODO]`.
- **B4 — Convergence / stop logic.** `[DONE+TEST]` (test_convergence, 8; test_orchestrator, 3).
  update_cluster/cluster_status → promote/shelve/dead/active. **Refine-exhaustion fix:** a category
  is dismissed only after every found alpha is refined (`refined_ids`); a new near-miss revives it.
  Honest boundary: refine bounds the *alpha space* to what explore proposes + near-misses rescued —
  right shape at hundreds-of-alphas scale, not millions.
- **B-GAP — rescue unexercised on real data.** `[TODO]` The B2 rescue is unit-tested and smoke-proven
  on a *hand-built* cost-killed near-miss only. **Not yet shown that a real US30 hunt produces a real
  cost-killed edge that the exploit engine actually recovers end-to-end.** This is the core of "strong
  US30 base" below.

## Phase C — Regime awareness

- **C1 — PIT-correct regime labeling.** `[DONE+TEST]` (test_regime, 4). Causal `vol_regime`
  (ATR-percentile) + `trend_regime` (EMA-separation) as features.
- **C2 — Per-regime scorecard.** `[DONE+TEST]` (test_regime_report, 3). Per-bucket
  n/expectancy/win-rate/PF + `regime_concentration` (1.0 = all edge in one regime = fragile).
- **C3 — Regime-conditioning.** `[DONE+TEST]` (test_regime_conditioning, 3; test_map, 6).
  `regime_stability = 1 − regime_concentration`, non-gating. **Threaded into live runs
  (commit d385164):** `evaluate()` now loads the anchor's exact bars (lru-cached, no extra read) so
  real Director runs get the true vol/trend score, not the crude temporal-chunk fallback.

## Phase D — Portfolio / correlation engine  *(the actual deliverable: a book)*

- **D1 — Correlation as a first-class metric.** `[DONE+TEST]` (test_correlation, 5). Signed-Pearson
  `diversification_score = (1 − max signed corr)/2`.
- **D2 — Portfolio constructor:** `[TODO]` pick the uncorrelated, regime-diversified subset that
  maximizes *combined* risk-adjusted return. **Friction becomes a hard requirement here** (promotion gate).
- **D3 — Output = the candidate bot:** `[TODO]` edges + weights + regime rules, deployable.

## Phase E — Point it at JJC and hunt

- **E1 — Encode existing JJC entries as DSL specs.** `[TODO / HUMAN]` needs Jake's JJC entry rules.
- **E2 — Long explore + heavy exploit run.** `[TODO / HUMAN]` = the "run live 1 month" step below.

---

## Forward plan (source of truth, set 2026-07-15)

Ordered. Each rung must hold before the next. Multi-instrument expansion is LAST — no point
replicating a shaky base across 3 machines.

1. **Strong US30 base (A / current).** Real US30 Director hunt that proves the loop end-to-end on real
   data — specifically **B-GAP**: a real cost-killed near-miss gets rescued into a survivor, regime
   report reads sane per-bucket, optimizer shows real refine depth. Close D2+D3 so a run outputs an
   actual candidate book, not just survivors.
2. **Single shared DB + visualize.** Flip `store: supabase`, point everything at one Supabase project
   so all runs/verdicts/scorecards land in one place. **First prove `SupabaseMemory` with one real
   round-trip** (it has 15 methods, `[TODO]` zero tests). Confirm the FastAPI dashboard (`/api/*`,
   already reads `get_memory()`) renders the shared map adequately; fill gaps if not.
3. **Expand the DSL (reasonably).** `[TODO]` More alpha primitives / wrapper types / features — enough
   to give the hunt real room, without blowing scope. Bounded, additive, each addition keeps the
   gauntlet green. (Refine still never mutates the alpha; a richer alpha vocabulary is explore's gain.)
4. **Run live ~1 month.** `[HUMAN]` Cron the US30 hunt, let it accumulate, read what it spits out.
   This is E2 in practice and the real "is there an edge" test.
5. **Containerize.** `[TODO]` `Dockerfile` + `POPPER_MEMORY=supabase` + `RUNNING.md` + cron wrapper.
   Freezes a base we *know* works. Update model = rebuild tag + `docker pull` (no auto-update infra
   for 3 people).
6. **Multi-instrument expansion.** `[TODO / HUMAN]` Friends source NAS / DAX / GOLD parquet **in the
   exact US30 schema** (`time, open, high, low, close, tick_volume, spread, real_volume`), set
   `context.universe`, run their container against the shared DB. Same engine, same DSL, different
   symbol — instrument axis is data-only, already validated by `validate_local_data_context`.

## Tasks that need Jake (human-only)
- **US30 real data:** ✅ present (`data/us30/US30_M1.parquet`, `US30_M5.parquet`; M5 spans 2025-01→2026-07).
- **Drive the US30 review** — eyeball the real-data hunt output and confirm it behaves ("I can drive a test after we know it's working").
- **Supabase project + creds** for the shared DB (owns the stack) → set `supabase_url` / `supabase_key`.
- **JJC entry rules** for E1 (encode as seed specs).
- **Kick off + monitor the ~1-month live run** (step 4).
- **Friends:** source NAS/DAX/GOLD data *in schema* + get their container running (step 6).
- *(optional, pre-deploy)* every-tick real-data A1 re-run for final fidelity sign-off.

## Sequencing rule (original, still holds)
Phase A is non-negotiable before B–E (self-learning on untrustworthy numbers just optimizes fantasy
faster). A1 / A2 / C1 were independent + parallelizable; B depends on A2 + benefits from C; D depends on B + C.

## Reality check (honest)
Strong *applied rigor*, not novel theory. Won't out-alpha real funds (their edge is data / execution /
capacity / headcount). The winnable game: small, robust, low-capacity single-instrument edges that
survive real costs. Popper's value = telling you honestly and fast when there's no edge, and dragging
a marginal-but-real edge over the line via exploit + regime + portfolio.
