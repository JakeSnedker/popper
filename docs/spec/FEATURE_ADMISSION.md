# Feature Admission — the standing rubric + ledger

The feature library (`popper/dsl/features/__init__.py`) is the ONLY vocabulary an
alpha may reference (spec/01). It is shared between Popper (research/falsification)
and Forge (Pine/MQL5 codegen), so every admitted feature is a permanent commitment
on both sides. Curate it like an API, not a scratchpad.

## The three tests (a feature is admitted only if it passes all three)

1. **Point-in-time correct (non-negotiable).** The value at bar *i* must be
   identical whether or not bars after *i* exist. Look-ahead = fake edge; it is
   the one thing we refuse. Every feature ships a prefix-invariance test
   (`tests/test_dsl.py::test_all_features_point_in_time_correct` covers `_EXACT`;
   `tests/test_smc.py` covers the SMC pack + tuned name-family variants).
2. **Atomic, not composite.** A primitive measures ONE thing. If it is expressible
   as a combination of existing features + the signal grammar (`+ - * /`, compares,
   AND/OR/NOT, `p[...]`), it is a signal, not a feature — do not admit it.
3. **Orthogonal, not redundant.** It must measure something no existing feature
   already measures. Same measurement in a new dress = fold into the existing one.

Tunable, edge-defining numbers are exposed as **knobs**, never hard-coded magic:
named module constants for defaults (`SWING_LOOKBACK`, `CISD_RUN`, `OB_DISP_MULT`),
and per-spec via name families (`bos_<n>`, `mss_<n>`, `equilibrium_<n>`,
`ob_bull_mid_<n>`, `ob_bear_mid_<n>`, `cisd_<n>`) that mirror `ema_<n>`.

## Ledger

### Admitted — SMC pack (dsl_version 0.3)

All build on ONE shared `_swing_pivots(bars, lookback)` fractal helper (pivot logic
is not duplicated). Defaults are ICT-standard; knobs in parentheses.

| Feature | Measures | Output | Knob(s) |
|---|---|---|---|
| `fvg` | Fair Value Gap presence (3-bar imbalance) | +1 bull / -1 bear / 0 | — (definitional 3-bar) |
| `bos` | Break of the most recent confirmed swing | +1 / -1 / 0 event | `swing_lookback` (2) |
| `mss` | First BOS opposing the previous BOS (structural flip) | +1 / -1 / 0 on flip | `swing_lookback` (2) |
| `cisd` | Change in state of delivery (close beyond first-of-run open) | +1 / -1 / 0 | `cisd_run` (1) |
| `equilibrium` | 50% of the current dealing range | price level | `swing_lookback` (2) |
| `ob_bull_mid` | 50% of the nearest unmitigated bullish order block | price level / NaN | `swing_lookback` (2), `ob_disp_mult` (1.0) |
| `ob_bear_mid` | 50% of the nearest unmitigated bearish order block | price level / NaN | `swing_lookback` (2), `ob_disp_mult` (1.0) |

Definitions (authoritative = the Python in `features/__init__.py`):
- **swing pivot (shared):** strict fractal high/low over `swing_lookback` bars each
  side; CONFIRMED `swing_lookback` bars after it forms, invisible before that.
- **fvg:** bullish 3-bar gap when `high[i-2] < low[i]` (bearish mirror). Exposed as
  PRESENCE (most useful for signal gating); gap magnitude/midpoint is a trivial
  derivation, not a separate primitive (see rejected `imbalance`).
- **bos:** +1 when close crosses above the most recent confirmed swing high, -1
  below the confirmed swing low, else 0.
- **mss:** the first BOS whose direction opposes the previous BOS; +1/-1 on the
  flip bar else 0. Built on bos internals.
- **cisd:** after a run of ≥ `cisd_run` consecutive same-direction closes, +1 when a
  candle closes beyond the OPEN of the first candle of a down-run (delivery flips
  buyside), -1 mirror.
- **order_block (`ob_bull_mid`/`ob_bear_mid`):** 50% (equilibrium) level of the
  nearest UNMITIGATED order block. Bullish OB = last down-close candle before an
  up-move that displaces (breaks the prior confirmed swing high OR body ≥
  `ob_disp_mult`·atr_14); bearish mirror. NaN when none active; cleared on full
  mitigation (price fully sweeps back through the zone).
  - **Supply/Demand zone = this feature at a larger structural lookback**
    (`ob_bull_mid_<bigger n>`) — a PARAMETERIZATION, not a new feature. True HTF
    resampling stays deferred, consistent with `htf_trend`'s DECISION comment.

### Rejected — folded, not admitted

| Rejected | Reason | Fold into |
|---|---|---|
| `imbalance` | Same measurement as `fvg` (a 3-bar gap). Fails test 3 (redundant). | `fvg` — presence is the primitive; gap magnitude is `low - high[2]` (bull) / `low[2] - high` (bear), a trivial derivation, not a feature. |
| `displacement` | Expressible with existing features + grammar. Fails test 2 (composite). | signal: `candle_size > k * atr_14` (with `k` a `p[...]` knob). |
| `supply_demand_zone` | Same measurement as an order block, only at a larger structural lookback. Fails test 3 (redundant). | `ob_bull_mid_<n>` / `ob_bear_mid_<n>` at a larger `n` (a parameterization). |

## Parity note (Forge codegen)

The stateful structural features (bos/mss/cisd/order_block/equilibrium) are hard to
port 1:1. Popper's Python is the semantic truth (falsification runs on it). Forge's
Pine (via `ta.pivothigh/pivotlow` + `var` state) and MQL5 (bounded `InpSmcScan`
rescan) are best-effort, with a parity caveat stamped into every generated file
header — same discipline as `psar`/`opening_range`. `fvg` ports 1:1.
