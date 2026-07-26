# Hypothesis Agent

## Mission

Generate one novel, falsifiable trading hypothesis for US30.

The hypothesis must name a market mechanism, not just a chart pattern. Good hypotheses explain why the edge should exist: liquidity behavior, order-flow imbalance, behavioral crowding, session effects, volatility expansion, mean reversion, or structural positioning.

## Current Implementation

- Code: `popper/agents/hypothesis.py`
- Functions: `propose(...)` and batched `propose_batch(...)`
- Default route: logical `hypothesis`; provider/model resolved by `LLMRouter`
- Runtime batch: up to `POPPER_LLM_BATCH_SIZE` claims per creative call
- Memory view: `BlindView`

## Inputs

| Input | Source | Notes |
|---|---|---|
| Map coverage | `BlindView.coverage()` | Edge-type counts only |
| Whitespace | `BlindView.whitespace()` | Under-explored categories |
| Prior claims | `BlindView.hypotheses()` | Claims/mechanisms only, no results |
| Feature list | `popper.dsl.features.FEATURES` | Approved DSL vocabulary |

## Output Artifact

`Hypothesis`

Required fields:

- `claim`
- `mechanism`
- `required_features`
- `expected_regime`

## Must Not See

- Profit factor.
- PnL.
- Scorecards.
- Verdicts.
- Which ideas survived or died.
- Behaviour signatures.

## Success Criteria

- Claim is falsifiable.
- Mechanism is plausible.
- Required features exist in the approved DSL, or the gap is clearly a feature request.
- Idea is not a near-duplicate of prior claims.
- Expected regime is stated.

## Failure Modes

| Failure | Example | Handling |
|---|---|---|
| Pattern without mechanism | "Buy EMA crossovers" | Reject or ask for mechanism |
| Duplicate idea | Reworded NY-open momentum | Gatekeeper rejects as stale |
| Unavailable feature | "Use VWAP bands" before VWAP exists | Record feature gap, do not fake it |
| Too broad | "Trade trend continuation" | Ask for falsifiable condition |

## Upgrade Path

- Add few-shot examples of good vs bad hypotheses.
- Add a `feature_gap_request` path instead of forcing all ideas into current DSL.
- Add novelty pressure by edge type, session, regime, and feature family.
- Add a critic pass that asks: "What would prove this false?"
- Add human-review flags for potentially valuable ideas requiring new DSL primitives.
