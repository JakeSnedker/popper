# Popper Agent Cards

These files define the human-readable operating contract for each AI agent in Popper.

The code currently lives in `popper/agents/` and `popper/orchestrator/director.py`. These docs make the roles explicit for walkthroughs, operators, investors, and future production hardening.

## Load-Bearing Principle

Generation agents must be performance-blind.

A hypothesis generator that can see profits will copy winners and tunnel into already-explored regions. Popper prevents this with scoped memory views:

| Role group | Memory view | Can see performance? | Purpose |
|---|---|---:|---|
| Hypothesis / Wildcard / Gatekeeper / Synthesis | `BlindView` | No | Generate and validate ideas without copying results |
| Director / Worker | `FullView` | Yes | Allocate compute and refine known survivor categories |

## Agent Roster

| Agent | File | Status | Job |
|---|---|---|---|
| Hypothesis | `HYPOTHESIS.md` | Working draft | Propose novel falsifiable claims |
| Wildcard | `WILDCARD.md` | Working draft | Inject off-map ideas |
| Gatekeeper | `GATEKEEPER.md` | Working draft | Reject duplicates and route category |
| Synthesis | `SYNTHESIS.md` | Working draft | Convert hypothesis into valid DSL |
| Director | `DIRECTOR.md` | Working draft | Allocate compute and conduct the loop |
| Worker | `WORKER.md` | Early draft | Refine survivors within a category |
| Access Control | `ACCESS_CONTROL.md` | Important | Defines blind vs full memory boundaries |

## Production Upgrade Target

Each agent should eventually have:

- A stable prompt contract.
- Explicit input and output artifacts.
- A scoped memory view.
- Tool permissions.
- Model choice and token budget.
- Unit tests with stubbed LLM responses.
- Human override points.
- Failure-mode telemetry.

## Current Honest Status

The agent loop works, but it is still a research-grade draft.

The strongest parts are the artifact contracts, validator repair loop, and performance-blind access model. The weakest parts are domain-specific prompt tuning, Worker refinement policy, and adaptive budget allocation.
