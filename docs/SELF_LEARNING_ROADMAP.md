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
- **B-GAP — rescue proven on real data.** `[DONE+TEST]` (commit + test_optimizer `_accept`). Real US30
  M5 probe found a genuine cost-killed edge (`ny_open_momentum`: gross +0.063R, net −0.017R,
  param-stability 0.965, positive in every vol regime). The rescue **failed** at first — acceptance
  required verdict∈survive on every step, so a cost-killed edge that can't reach survivor in one move
  never climbed. **Fix:** `_accept` decouples the hill-climb from the survivor gate — accept on net_E
  improving + overfitting guards; only require survivor status when refining an already-surviving seed.
  **Result: net −0.017R → +0.149R** via `sl_atr+`/`rr+`/`trail_on`/`or_min+` (the cost levers). Honest
  nuance: net-positive but still verdict=kill (thin gross edge) → it's a **portfolio candidate (D2)**,
  not a standalone promote.

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
- **D2 — Portfolio constructor:** `[DONE+TEST]` (test_portfolio, 2). `build_book`: **friction HARD-GATE
  lives here** (expectancy_net>0; no net metric = ineligible) + robustness (pbo/param-stability) +
  greedy signed-correlation de-dup (≤ max_corr) + inverse-vol weights. This is the *different* gate
  from single-edge promotion — a thin net-positive edge (the rescued ny_open) earns a book slot.
- **D3 — Output = the candidate bot:** `[DONE+TEST]` book = edges + weights + regime rules + combined
  stats + rejected-with-reasons. `book_from_mem(mem)` pulls candidates from the map so a run outputs a
  book. *Not yet a POPULATED real book — needs a real US30 run with cost metrics (step 4) or an LLM run.*

## Phase E — Point it at JJC and hunt

- **E1 — Encode existing JJC entries as DSL specs.** `[TODO / HUMAN]` needs Jake's JJC entry rules.
- **E2 — Long explore + heavy exploit run.** `[TODO / HUMAN]` = the "run live 1 month" step below.

---

## Forward plan (source of truth, set 2026-07-15)

Ordered. Each rung must hold before the next. Multi-instrument expansion is LAST — no point
replicating a shaky base across 3 machines.

1. **Strong US30 base (A / current).** ✅ *mostly done.* Real US30 M5 probe proved the loop end-to-end:
   B-GAP rescue works (net −0.017R → +0.149R), regime report reads sane per-bucket, optimizer shows
   real refine depth, D2/D3 build a book with the friction hard-gate. **Remaining:** one real
   *populated* book — a US30 run (LLM-driven or seeded) that yields ≥1 cost-surviving edge into
   `book_from_mem`; and optionally collect rescued net-positive variants into the candidate pool
   inline (today `book_from_mem` already finds them in the map post-run).
2. **Single shared DB + visualize.** Flip `store: supabase`, point everything at one Supabase project.
   **Audit done (2026-07-15):** `SupabaseMemory` was built to the OLD schema — missing
   add_research_event/list_research_events/add_feature_gap/list_feature_gaps AND the research_events +
   feature_gaps tables → **it crashed every real run**. Fixed: methods + migration + an offline parity
   test (test_memory_parity) so this drift can't recur. Known ceiling: COUNT+1 ids race under
   concurrent multi-machine writes (loud, low-severity at cron scale; upgrade = Postgres sequence or
   instrument-namespaced ids). **`[HUMAN]` remaining:** run `scripts/supabase_roundtrip.py` with creds
   to prove the live round-trip; then confirm the FastAPI dashboard (`/api/*`) renders the shared map.
3. **Expand the DSL (reasonably).** Bounded, additive, gauntlet stays green. Jake's steer: **alpha
   first, a handful; no fake signals for data we don't have.**
   - **Alpha (DONE 2026-07-15):** `fib_pos` (Fibonacci retracement position), `rel_volume`,
     `delta_proxy` + `body_pressure` (HONEST order-flow PROXIES — candle direction & body/range, NOT
     real L2; swap data source if a DOM feed is wired). All PIT-correct.
   - **Wrapper/exits — OVERLAP AUDIT (2026-07-15), deprioritized, driven by E1:** the DSL wrapper is a
     strict SUBSET of JJC trade-mgmt (1 BE vs ~11, 1 trail vs ~15) → **no overlap risk, only gaps**.
     Entry-time filtering already lives in the ALPHA (session_ny/opening_range) → do NOT add wrapper
     time-of-day entry rules (that overlaps). Real GAPS to port from JJC (into `Constraints`):
     **session-end flatten, end-of-day flatten** (derivable from bar timestamps — buildable) and
     **news avoidance / close-into-news** (needs an economic-calendar feed we DON'T have — FLAG, don't
     fake, same rule as L2). Port exactly the JJC rules a real E1 spec needs, no more.
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
