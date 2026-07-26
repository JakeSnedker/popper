# Synthesis Agent

## Mission

Convert one `Hypothesis` into a valid Popper `StrategySpec` YAML.

This agent is the bridge between natural-language research ideas and the controlled strategy DSL.

## Current Implementation

- Code: `popper/agents/synthesis.py`
- Function: `synthesize(hypothesis, llm, model, max_repairs=1)`
- Default route: cheap logical `synthesis`, then `synthesis_strong` on deterministic failure
- Fidelity gate: required features must be declared and used before evaluation
- Memory view: no performance view; it sees one hypothesis plus DSL rules

## Inputs

| Input | Source | Notes |
|---|---|---|
| Hypothesis | artifact | Claim, mechanism, suggested features |
| DSL feature list | `FEATURES` | Approved vocabulary only |
| Validator errors | `validate_spec()` | Used for repair loop |

## Output Artifact

`StrategySpec`

Required blocks:

- `name`
- `hypothesis_id`
- `alpha`
- `wrapper`
- `context`
- `param_space`

## Repair Loop

Flow:

```text
Generate YAML -> validate_spec -> if invalid, feed exact errors back -> retry -> valid spec or fail
```

The repair loop is important because it turns the DSL validator into an agent training boundary. The model does not need to be trusted; the validator decides what is allowed.

## Must Not Do

- Invent unsupported features.
- Use arbitrary code.
- Use function calls in signals.
- Use hidden look-ahead constructs.
- Add unused params.
- Reference params not present in `param_space`.

## Success Criteria

- Spec validates without human edits.
- Signal expresses the hypothesis mechanism as directly as possible.
- Wrapper is simple unless the hypothesis specifically depends on exit structure.
- Param grid is broad enough to falsify knife-edge behavior but not so large it wastes compute.

## Failure Modes

| Failure | Example | Handling |
|---|---|---|
| Feature not in DSL | `vwap` before approved | Return feature gap request, not fake proxy |
| Overfit param grid | 20 tiny thresholds | Gate/critic should reject or simplify |
| Unused param | `rr` in grid but not signal/wrapper | Validator rejects |
| Unsupported expression | `close.shift(-1)` | Validator rejects |

## Upgrade Path

- Update prompt to DSL v0.3 exits.
- Add examples for SMC, regime, VWAP, and volume profile once approved.
- Add explicit feature-gap output mode.
- Add wrapper-selection policy: baseline, declared thesis, or exit menu.
- Add human approval step for specs that use new or high-risk features.
