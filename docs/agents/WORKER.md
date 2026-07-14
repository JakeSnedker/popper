# Worker Agent

## Mission

Exploit a known live category by refining survivor strategies.

Workers intentionally tunnel locally, but only after a category has earned that attention by producing survivors.

## Current Implementation

- Code: `popper/orchestrator/director.py`
- Function: `_worker_refine(...)`
- Current policy: simple numeric param grid nudge of +10%
- Memory view: `FullView`
- Status: early draft

## Inputs

| Input | Source | Notes |
|---|---|---|
| Survivor spec | alpha memory | Strategy to refine |
| Full category results | memory view | Allowed for workers |
| Gauntlet config | Director | Re-test after mutation |

## Output

A new experiment, scorecard, and verdict under the same hypothesis lineage.

## Why Workers Can See Results

Unlike Hypothesis/Synthesis, Workers are supposed to exploit what works. Their job is local refinement, not unbiased novelty generation.

This is controlled tunnelling.

## Success Criteria

- Refines only survivor/promote candidates.
- Produces a new tested variant.
- Preserves lineage back to parent hypothesis.
- Improves robustness, WFE, or ruin profile.

## Current Weakness

The current worker mutation is intentionally simple:

```text
numeric param grids -> +10%
```

This proves the loop but is not yet a serious optimizer.

## Failure Modes

| Failure | Example | Handling |
|---|---|---|
| Flat mutation | +10% misses useful region | Add structured optimizer |
| Parent status not updated | Worker improves but parent remains old state | Add lineage rollup |
| Parallel ID race | SQLite count-based IDs collide | Add atomic ID source |
| Overfit refinement | Worker finds knife-edge improvement | DSR/PBO and WFE gates must catch it |

## Upgrade Path

- Add wrapper-aware mutation.
- Add local Bayesian or evolutionary search.
- Add WFE/opportunity metric as optimization target.
- Add explicit parent-child lineage view in GUI.
- Add human approval before promoting worker-refined candidates.
