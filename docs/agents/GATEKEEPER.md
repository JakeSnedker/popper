# Gatekeeper Agent

## Mission

Reject duplicates before they cost compute and route accepted hypotheses into a provisional category.

The Gatekeeper protects the research loop from re-testing the same idea with different words.

## Current Implementation

- Code: `popper/agents/gatekeeper.py`
- Function: `screen(hypothesis, view)`
- Current behavior: deterministic novelty and category routing
- Memory view: `BlindView`

## Inputs

| Input | Source | Notes |
|---|---|---|
| Hypothesis | artifact | Candidate idea |
| Novelty score | alpha memory | Embedding-distance based |
| Staleness check | alpha memory | Near-duplicate detection |
| Category | `popper.memory.map.categorize()` | Edge-type routing |

## Output

Dictionary:

- `accepted`
- `reason`
- `novelty`
- `category`

## Must Not See

- Profitability.
- Verdicts.
- Scorecards.
- Performance ranking.

## Success Criteria

- Near-duplicates are rejected.
- Novel ideas are routed to a useful category.
- Whitespace remains visible to Hypothesis/Wildcard generation.

## Failure Modes

| Failure | Example | Handling |
|---|---|---|
| Semantic duplicate missed | Different wording, same edge | Add LLM semantic dedup later |
| False duplicate | Similar words, different mechanism | Allow human override |
| Bad category | Liquidity idea routed as trend | Improve map taxonomy |

## Upgrade Path

- Add LLM-assisted semantic duplicate detection.
- Add category confidence score.
- Add human override for "test anyway".
- Normalize feature-gap requests into a separate queue.
- Track rejected ideas so the system remembers why they were not tested.
