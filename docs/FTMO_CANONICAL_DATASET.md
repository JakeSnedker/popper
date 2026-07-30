# FTMO Canonical Dataset

Step 3 completed: 2026-07-30.

## Dataset Identity

| Field | Value |
|---|---|
| Version | `ftmo-us30-v1` |
| Dataset SHA-256 | `7dce1c0c4e6ff630b8a0835bee05183f7902b6bb5a1753a93a600e62f0278c13` |
| Broker server | `FTMO-Demo` |
| Symbol | `US30.cash` |
| Timezone | UTC |
| Approved period | `2021-10-04` through `2026-07-29` exclusive |
| Data files | 1,936 |
| Data bytes | 1,367,481,772 |

The local dataset lives at `data/ftmo-us30-v1/` and is excluded from Git and Supabase. The manifest contains only portable relative paths, row counts, byte sizes and file hashes. A copied dataset is valid only if `--verify-only` reproduces the aggregate hash above.

## Contents

| Series | Source | Partitions | Rows |
|---|---|---:|---:|
| Ticks | `MetaTrader5.copy_ticks_range` | 1,762 daily | 129,520,143 |
| M1 | `MetaTrader5.copy_rates_range` | 58 monthly | 1,686,064 |
| M5 | Deterministic aggregation of M1 | 58 monthly | 337,546 |
| M15 | Deterministic aggregation of M1 | 58 monthly | 113,733 |

Physical coverage:

- First archived tick: `2021-10-01 14:32:41.423 UTC`.
- Last archived tick: `2026-07-28 23:49:57.976 UTC`.
- First native M1 bar: `2021-10-01 01:05 UTC`.
- Last native M1 bar: `2026-07-28 23:49 UTC`.
- October 1 is retained for provenance but excluded from the approved common period because its tick archive begins mid-session.

Layout:

```text
data/ftmo-us30-v1/
  dataset-manifest.json
  tick-manifest.json
  m1-source-manifest.json
  bar-manifest.json
  m1-gap-report.json
  tick-m1-consistency.json
  native-validation.json
  ticks/YYYY/MM/YYYY-MM-DD.parquet
  bars/M1/YYYY/MM.parquet
  bars/M5/YYYY/MM.parquet
  bars/M15/YYYY/MM.parquet
```

## Canonical Rules

M1 is the official broker bar-research series. M5 and M15 are derived from M1 using UTC epoch-aligned, left-closed intervals:

- Open: first M1 open.
- High: maximum M1 high.
- Low: minimum M1 low.
- Close: last M1 close.
- Tick volume: sum.
- Spread: minimum, matching native MT5 higher-timeframe `MqlRates` behavior.
- Real volume: sum.
- Missing intervals are never forward-filled or synthesized.

The full M1 series has no duplicate timestamps. Its 2,629 measured gaps include routine daily sessions, weekends, exchange holidays and sparse no-quote minutes. The largest gap is 4,395 minutes across multi-day market closures. Every interval is retained in `m1-gap-report.json` rather than classified away.

## Native Validation

Derived M5/M15 are compared with native MT5 bars on dates spanning the archive, including dates where the tick and native-bar source snapshots differ. Validation requires identical timestamps, OHLC, tick volume, spread and real volume; price tolerance is `0.001`, one tenth of the symbol point.

The validation report passed on 1,825 sampled bars across five dates and both derived timeframes, with zero mismatches in any field. Detailed evidence is stored in `native-validation.json`.

## Tick Versus M1 Provenance

Ticks and native M1 are two official FTMO history sources, but they are not one byte-for-byte historical snapshot. A complete day-by-day comparison found:

- 137 dates with at least one source difference, 136 inside the approved period.
- 123 canonical M1 minutes without archived ticks inside the approved period.
- 1 tick-derived minute without a canonical M1 bar.
- Historical OHLC differences on some archived dates, with the largest observed absolute difference documented in `tick-m1-consistency.json`.

This is recorded evidence, not silently reconciled data. Bar research uses canonical native M1. Tick-level execution studies must use the tick series as their price source and consult the consistency report when combining tick execution with canonical bar signals. The build never substitutes one source into the other.

## Build And Verify

The complete tick export is restartable:

```powershell
.venv\Scripts\python.exe scripts\export_mt5_ticks.py `
  --symbol US30.cash --start 2021-10-01 --end 2026-07-29 `
  --dataset-dir data\ftmo-us30-v1
```

Build M1, derive M5/M15, run live validation, measure gaps and hash the dataset:

```powershell
.venv\Scripts\python.exe scripts\build_ftmo_dataset.py `
  --dataset-dir data\ftmo-us30-v1 `
  --terminal "C:\Program Files\MetaTrader 5\terminal64.exe"
```

Verify a received copy without MT5:

```powershell
.venv\Scripts\python.exe scripts\build_ftmo_dataset.py `
  --dataset-dir D:\popper-data\ftmo-us30-v1 --verify-only
```

The verifier checks all 1,936 Parquet files for expected path, byte size, row count and SHA-256, recomputes the aggregate dataset hash, and verifies each evidence-report hash. Workers receive this directory separately from Git and mount or mark it read-only after verification.
