# Causal Multi-Timeframe DSL

Step 4 completed: 2026-07-30.

## Contract

A strategy has one base execution timeframe in `context.timeframe`. Optional higher-timeframe features live inside the same alpha and hypothesis:

```yaml
name: m5_momentum_with_m15_trend
alpha:
  features: [ema_10, atr_14]
  higher_timeframe_features:
    15m: [ema_50]
  signal: "close > ema_10 AND htf_m15_ema_50 > close"
context:
  universe: [US30]
  timeframe: 5m
```

Higher feature identifiers are deterministic Python-safe aliases:

```text
htf_<canonical timeframe lowercase>_<feature>
htf_m5_ema_20
htf_m15_atr_14
```

The validator rejects undeclared aliases, unknown features, duplicate normalized timeframes, empty higher feature sets, and any higher timeframe that is not a strict integer multiple of the base. The local canonical contexts are M1, M5 and M15.

## Completion Boundary

Bar timestamps are interval opens. A feature computed from a higher bar at `T` becomes available only at:

```text
T + higher_timeframe_duration
```

Signals evaluate at the base bar close. Alignment uses a backward as-of join from each base close to higher-bar completion times, allowing an exact completion-time match.

Example for an M1 base and M5 context:

- Base bar `10:03` closes at `10:04`; the active `10:00` M5 bar is invisible, so context still comes from `09:55`.
- Base bar `10:04` closes at `10:05`; the `10:00` M5 bar has completed and becomes visible.
- Entries still fill at the next base bar open.

No forward fill is performed before the first completed higher bar. Missing context remains `NaN`, and bounded signal comparisons evaluate false until features form.

## Data And Caching

The runner reads canonical monthly partitions from `data/ftmo-us30-v1/bars/{M1,M5,M15}` and falls back to the legacy single-file layout only when canonical partitions are unavailable.

Each raw timeframe is cached once by symbol/timeframe. A higher timeframe is loaded once even when several features use it, and every unique feature binding is computed once per compiled evaluation. Higher context retains pre-period history for indicator warmup, then causal completion alignment decides what each base bar can see.

## Falsification

Multi-timeframe context flows through:

- The normal experiment runner and cost path.
- Layer 1 signal-edge evaluation.
- Walk-forward, robustness, Monte Carlo and multiple-testing gates through the standard runner.
- Gate 0 future-truncation leakage detection with independently truncated higher context.

Prefix-invariance tests prove that appending or perturbing future higher bars cannot change earlier aligned features or completed trades.

Additional context search is counted rather than treated as free. The Deflated Sharpe trial count is:

```text
parameter trials x exit-menu trials x (1 + higher-timeframe feature bindings)
```

One hypothesis remains one memory artifact; it is not cloned across M1/M5/M15 to inflate discovery volume. Its context tag records the base plus declared higher timeframes.

## Agent And Export Safety

Hypothesis and Synthesis prompts permit M5/M15 confluence only inside one mechanism. Fidelity accepts a required primitive bound either to the base or to a declared higher alias, while still requiring that the resulting identifier appears in a signal.

Pine and MQL5 Strategy Forge targets currently fail closed for multi-timeframe specs. They will not silently emit single-timeframe code with different semantics. Export support must implement the same completed-bar boundary before that guard can be removed.

## Verification

The acceptance suite covers:

- Timeframe normalization and hierarchy validation.
- Exact M1/M5 completion boundaries.
- In-progress higher-bar invisibility.
- Feature and completed-trade prefix invariance.
- Required-feature fidelity through a higher binding.
- One-load-per-timeframe behavior.
- Context-aware multiple-testing counts.
- A real canonical M5 run using completed M15 context.
- Fail-closed Pine/MQL5 export behavior.
