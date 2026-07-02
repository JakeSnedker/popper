# Popper — SPEC Pack

The authoritative build specification: the **target Fable 5 builds toward**. It reconciles the repo's

`docs/ARCHITECTURE.md` + `ROADMAP.md` are the *simplified thin-loop* version; this pack restores the
complete system).

Read order: `00-overview` → the numbered docs. Each doc is a spec, not a tutorial — it states the design,
points to source material, and flags open **DECISION**s with a recommended default.

## The spec docs
| # | Doc | Covers |
|---|-----|--------|
| 00 | `00-overview.md` | Whole system: the tunnelling problem, Alpha×Wrapper×Context, the pipeline, two engines + Director, build target vs order, model split |
| 01 | `01-dsl.md` | Strategy DSL + compiler; the Alpha/Wrapper/Context split; grammar, feature library, 3 compile targets |
| 02 | `02-agents.md` | The 5-agent roster, I/O contracts, performance-blind access control, the Director's explore/exploit dial |
| 03 | `03-falsification.md` | The gauntlet; what to **port** from the JJC validation-framework, what to **add** (López de Prado), what to **drop** (MT5 coupling) |
| 04 | `04-map-and-categories.md` | Alpha memory as a map: pgvector+HNSW, embeddings, faceted categories, whitespace, portfolio/correlation engine |
| 05 | `05-gui.md` | *(pending — needs Jake's aesthetic input + a dataviz pass)* whitespace map viz + growing analytics dashboard |
| 06 | `06-build-order.md` | Phased build reconciling repo roadmap + full design → GSD phases; infra integration points |

## Source documents (the spec consolidates these — all now in-repo)
| Source | Holds | Path |
|--------|-------|------|

| **Research Notes** | Fullest design: Alpha·Wrapper·Context, agents, categorisation, portfolio, exploration toolkit | `docs/design-source/42_Project_Research_Notes.html` |
| **System Proposal** | The proposal framed around explore-vs-exploit | `docs/design-source/42_System_Proposal.html` |
| **Loop Schematic** | The pipeline diagram + one-read explanation | `docs/design-source/42_Research_Loop_Schematic.md` |
| **Loop Diagram** | Visual of the loop | `docs/design-source/42_Research_Loop_Diagram.png` |

| **Repo architecture** | Phase-0 artifact contracts + thin loop (built) | `docs/ARCHITECTURE.md`, `docs/ROADMAP.md` |
| **JJC validation-framework** | Falsification backbone to port/generalize | `C:\Users\Jakes\Projects\GitHub\quantitative-strategy-validation-framework\` (`METHODOLOGY.md`, `optimizer/walk_forward_config.py`, `optimizer/monte_carlo.py`, `optimizer/analyze_edge.py`, `optimizer/staged_optimizer.py`, `optimizer/docs/OPTIMIZATION_SYSTEM_V2.md`) |
| **Market data** | US30 M1+M5 bars (export via `scripts/export_mt5.py`) | `data/us30/` |

## Status
- **Data:** US30 only (NAS100 cache empty). ~Jan 2024→May 2026. Export script ready; run against live FTMO-Demo terminal.
- **Build target:** the FULL design. **Build order:** thin-loop-first, then layer agents / explore-exploit / portfolio (see `06`).
- **Model split:** Fable 5 *builds* it; *runtime* agents run on Opus 4.8 (Director/Synthesis) + Sonnet (high-volume Hypothesis/Wildcard). Fable 5 at runtime would be ruinous in an inner loop.

## Open DECISIONs (need Jake before final push to Fable 5)
1. DSL grammar style (see `01`) — recommended: YAML-authored, Pydantic-validated, compiled to a typed IR.
2. Embedding model for the map (see `04`) — local (LM Studio) vs API.
3. GUI aesthetic for `05` (map viz + analytics).
4. Confirm runtime model split above.
5. How far Fable 5 runs autonomously per gate (per-phase review vs to-tangible-loop).
