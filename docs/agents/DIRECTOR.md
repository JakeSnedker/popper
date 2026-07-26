# Director Agent

## Mission

Conduct the autonomous research loop.

The Director holds the explore/exploit dial, allocates compute, prunes dead categories, and decides whether the system should look for new ideas or refine known survivors.

## Current Implementation

- Code: `popper/orchestrator/director.py`
- Function: `run_research(...)`
- Budget split: `70% exploit / 20% explore / 10% wildcard`
- Memory view: `FullView`
- Status: working draft

## Inputs

| Input | Source | Notes |
|---|---|---|
| Alpha memory | full store | Can see all artifacts |
| Budget | config/dict | Explore/exploit/wildcard split |
| Current categories | run summary | Survivors/kills/dead flags |
| Gauntlet config | `GauntletConfig` | Controls falsification behavior |

## Output

`RunSummary`

Includes:

- iterations
- proposed
- killed
- survived
- worker runs
- survivors
- category report
- budget spent

## Permissions

The Director can see everything because it must allocate resources intelligently.

This is acceptable because the Director does not generate blind hypotheses directly; it routes work and coordinates agents.

## Success Criteria

- Budget split is honored.
- Dead categories are pruned.
- Survivors are surfaced.
- Worker refinement is allocated only to live categories.
- Full research trail is persisted.

## Failure Modes

| Failure | Example | Handling |
|---|---|---|
| Over-exploitation | Keeps refining same category | Diversity alarm / budget adjustment |
| Premature pruning | Category killed after weak specs | Increase K or require confidence |
| Worker race | SQLite ID race under parallel inserts | Atomic IDs before scaling |
| No live categories | Exploit budget wasted | Fallback to explore |

## Upgrade Path

- Replace fixed budget with adaptive bandit allocation.
- Cost/token/latency accounting is implemented per provider call and run; add long-term dashboard aggregation.
- Add human approval gates before expensive runs.
- Add cloud queue and resumable jobs.
- Add production reports after each research cycle.
- Add alerting when a candidate reaches promotion threshold.
