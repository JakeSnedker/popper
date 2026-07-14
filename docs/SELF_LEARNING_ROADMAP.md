# Popper — Self-Learning Roadmap (v2)

**Goal (priority 1):** find a *real, cost-surviving, tradeable* strategy — a bot Jake can make
money with. **Priority 2:** a portfolio showcase. The two reinforce; build for #1.

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

- **A1 — MT5 fidelity matched-pair.** A dead-simple EMA-cross strategy written *identically* as a
  Popper DSL spec **and** a hand-written ~40-line MQL5 EA (replicating Popper's exact ATR/EMA
  formulas, not MT5 built-ins). Run both on the same M1 data; a diff harness separates *logic
  mismatch* (should be ~zero) from *fill slippage* (the thing we measure). Calibrates `point_value`.
  Hand-written EA on purpose — Strategy Forge is deferred so the benchmark stays trustworthy; the
  harness later doubles as Forge's codegen validator.
- **A2 — Cost as a NON-GATING recorded metric.**  *(revised — see decision below)*
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

- **B1 — Real guided local optimizer.** Replace `_mutate` with a proper neighborhood search
  (coordinate / random / Bayesian) around a near-miss edge's params. Each candidate re-runs the
  **full gauntlet incl. friction**; keep an improvement **only if parameter-stability also holds**
  (no overfitting-to-a-win).
- **B2 — Near-miss cluster selection.** The Director pulls not just survivors but *marginal /
  slightly-negative* edges close to profitable — **explicitly including "positive gross / negative
  net" (cost-killed) edges**, which a wrapper/regime change most often rescues — and iterates the
  whole cluster.
- **B3 — Meta-learning signal.** Track which mutation directions historically improved edges; bias
  future search toward them. This is the "learns from the tests" loop — kept on the exploit side
  only, never leaking into the blind explorer.
- **B4 — Convergence / stop logic.** An edge is *done* when improvement flattens → promote or shelve;
  a cluster *dies* when no member improves. (Jake: "this is how it should be.")

## Phase C — Regime awareness

- **C1 — PIT-correct regime labeling.** Causal `vol_regime` (ATR-percentile) and `trend_regime`
  (EMA-separation) labels, exposed as features.
- **C2 — Per-regime scorecard.** Score each edge's stability/expectancy per regime; an edge that
  only works in high-vol is *tagged*, not discarded.
- **C3 — Regime-conditioning.** Exploit can specialize a marginal all-conditions edge into a solid
  *in-regime* one — often the difference between −0.05R and +0.15R.

## Phase D — Portfolio / correlation engine  *(the actual deliverable: a book)*

- **D1 — Correlation as a first-class metric** across survivors' return streams.
- **D2 — Portfolio constructor:** pick the uncorrelated, regime-diversified subset that maximizes
  *combined* risk-adjusted return. **Friction becomes a hard requirement here** (promotion gate).
- **D3 — Output = the candidate bot:** edges + weights + regime rules, deployable.

## Phase E — Point it at JJC and hunt

- **E1 — Encode existing JJC entries as DSL specs**; feed as *seed edges* for the exploit engine to
  meta-improve.
- **E2 — Long explore + heavy exploit run** → whatever survives friction + regime + portfolio is the
  real candidate.

---

## Sequencing rule
Phase A is non-negotiable before B–E (self-learning on untrustworthy numbers just optimizes fantasy
faster). Within reason, A1 / A2 / C1 are independent and parallelizable; B depends on A2 + benefits
from C; D depends on B + C.

## Reality check (honest)
Strong *applied rigor*, not novel theory. Won't out-alpha real funds (their edge is data / execution /
capacity / headcount). The winnable game: small, robust, low-capacity single-instrument edges that
survive real costs. Popper's value = telling you honestly and fast when there's no edge, and dragging
a marginal-but-real edge over the line via exploit + regime + portfolio.
