# Feature Gap Agent / Queue

## Status

Not implemented yet.

This is a proposed control layer for expanding the DSL without losing safety.

## Why It Matters

The current synthesis repair loop rejects unsupported features. That is good for safety, but it can block valuable alpha ideas that require new primitives such as VWAP, volume profile, point of control, or new SMC concepts.

The system needs a way to say:

```text
This idea is valuable, but the DSL cannot express it yet.
```

Without this, the model either gives up or forces the idea into a bad proxy.

## Mission

Collect, rank, and prepare requests for new DSL features.

## Proposed Input

- Rejected synthesis attempts.
- Hypothesis ideas requiring unsupported features.
- Human notes.
- Repeated missing vocabulary from agent outputs.

## Proposed Output

`FeatureGapRequest`

Suggested fields:

- `name`
- `reason`
- `market_mechanism`
- `example_signal`
- `required_data`
- `python_semantics`
- `pine_feasibility`
- `mql5_feasibility`
- `causality_risk`
- `priority`
- `human_decision`

## Admission Workflow

1. Candidate feature is requested.
2. Human decides if it is a true primitive or composition of existing features.
3. If accepted, implement Python semantics.
4. Add prefix-invariance tests.
5. Add validator registration.
6. Add Forge Pine/MQL5 parity if deployment is required.
7. Mark approved.
8. Expose to Hypothesis/Synthesis prompts.

## First Candidate Features

- `vwap`
- `session_vwap`
- `rolling_vwap_N`
- `volume_profile_poc_N`
- `volume_profile_vah_N`
- `volume_profile_val_N`

## Guardrail

No feature should be available to autonomous synthesis until it has deterministic semantics and causality tests.
