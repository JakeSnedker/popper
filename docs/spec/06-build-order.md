# 06 — Build Order & Integration

**Target = the full design (`00`–`04`). Order = thin-loop-first**, then layer the multi-agent /
explore-exploit / portfolio machinery. The docs' own advice: *"Start narrow and real; deepen once the loop


## Phase map (reconciles repo `ROADMAP.md` with the full design → GSD phases)
| Phase | Goal | Adds vs shipped | Done-when |
|-------|------|-----------------|-----------|
| **0 ✅** | Contracts + thin scaffold | *(shipped)* | schemas round-trip; GUI pipeline map live |
| **1** | **DSL + compiler** with Alpha/Wrapper/Context split | `01` — split the monolithic StrategySpec; feature library; `compile_to_backtest` | 3 alphas (incl. JJC-style) validate + compile |
| **2** | Data layer + Experiment Runner | US30 Parquet ingest (`scripts/export_mt5.py`); `run(spec)` → ExperimentResult | real PnL on US30 bars |
| **3** | Falsification gauntlet v0 | `03` — port WF/MC/edge from validation-framework; add leakage + purged CV | overfit spec KILLED, robust one SURVIVES |
| **4** | Alpha memory as map + Scorecard | `04` — pgvector + embeddings + novelty + faceted tags; behaviour/correlation fields | re-submitted idea flagged stale; scorecard per experiment |
| **5** | **Manual full loop** *(tangibility milestone)* | wire human-hypothesis → DSL → runner → scorecard → gauntlet → map | `popper run --hypothesis x.yaml` → stored, scored, judged |
| **6** | Research agents | `02` — Hypothesis + Synthesis + Gatekeeper; performance-blind views | agents produce specs that pass DSL validation unaided |
| **7** | **Orchestrator + Director** *(autonomy milestone)* | `02` — two engines, explore/exploit dial, budget/prune, Workers, Wildcard | unattended run: proposes, kills most, surfaces ≥1 survivor |
| **8+** | Deepen | CPCV, regime discovery, capacity/execution-realism (`compile_to_sim` + ticks), **portfolio/correlation engine**, `compile_to_live`, Deflated Sharpe/PBO at scale | per-item |

## Where the GUI grows (`05`, built alongside)
Phase 4 adds the **whitespace map** (crowded/sparse/whitespace over the vector map); Phases 3–7 grow the
**analytics dashboard** (scorecards, verdicts, survivor rate, explore/exploit balance) — it should visibly
accrue as tests accumulate. The existing architecture-driven GUI (`popper/architecture.py` → pipeline map)
is the shell to extend.

## Infrastructure integration (Jake's existing stack)
- **Supabase** — the alpha memory map (pgvector). Already the intended store; wire at Phase 4.
- **VPS / Docker** — long autonomous runs (Phase 7) + promotion tail (Phase 8) off the laptop. Mirrors the
  "VPS task queue" backlog item.
- **n8n** — orchestration glue for scheduled runs / notifications (autonomous posting, alerts) at Phase 7+.
- **Mission Control** — Popper reports next-action/progress/bottleneck up to the central dashboard
  (`mission-control/backend/context/popper-platform.md` already stubs this).

## Running it under Fable 5 (GSD)
GSD phase-by-phase maps 1:1 to the table above (atomic commits + verification gate per phase — the check
that keeps a long Fable 5 run honest). **DECISION (resolved):** run **straight-through autonomously**
(`/gsd:autonomous`) so an overnight run doesn't stall waiting on Jake. Crucially, this is *not* a single
monolithic prompt — GSD autonomous still does discuss→plan→execute **per phase** with an **automated**
verification gate each phase (the gate checks the phase goal was met; it does not block on a human). So the
run proceeds unattended but each phase is still verified before the next. Review happens after the run (or
at the tangible-loop milestone), not as a live stop.

## Pre-Fable checklist
- [ ] `data/us30/*.parquet` exported + committed (`scripts/export_mt5.py`)
- [ ] `05-gui.md` written (needs Jake's aesthetic input + dataviz pass)
- [ ] Open DECISIONs in `README.md` resolved (DSL style, embedding model, model split, autonomy)
- [ ] `git init` ✅ done; work on `fable-build`
