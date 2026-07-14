# Wildcard Agent

## Mission

Inject off-map ideas that deliberately avoid current research clusters.

Wildcard prevents the system from becoming too conservative or trapped in existing categories.

## Current Implementation

- Code: `popper/agents/hypothesis.py`
- Function: `propose(..., wildcard=True)`
- Current behavior: Hypothesis agent with different prompt steering
- Memory view: `BlindView`

## Inputs

| Input | Source | Notes |
|---|---|---|
| Map coverage | BlindView | Knows what is crowded |
| Whitespace | BlindView | Knows what is unexplored |
| Prior claims | BlindView | Avoids duplicates |
| Feature list | DSL | Approved vocabulary |

## Output Artifact

`Hypothesis`

Same artifact as Hypothesis agent, but expected to target off-map categories.

## Must Not See

- Results.
- Scorecards.
- Verdicts.
- Profitability.

## Success Criteria

- Proposes ideas outside crowded map regions.
- Uses approved features or raises feature gaps.
- Produces genuinely different mechanisms.

## Failure Modes

| Failure | Example | Handling |
|---|---|---|
| Random nonsense | No plausible mechanism | Gatekeeper/human rejects |
| Unsupported feature chain | Needs unavailable data | Feature-gap queue |
| Fake novelty | Same mechanism with exotic wording | Semantic dedup |

## Upgrade Path

- Add explicit off-map taxonomies.
- Add controlled inspiration sources: market microstructure, session behavior, volatility regimes, SMC, volume/auction theory.
- Add human-curated theme packs.
- Add feature-gap mode for valuable ideas outside current DSL.
