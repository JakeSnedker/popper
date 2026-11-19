# Market Data

Popper ships without market data. Bring your own OHLC bars.

## Use your own data

Save one Parquet file per symbol and timeframe:

```text
data/<symbol lowercase>/<SYMBOL>_<TIMEFRAME>.parquet     e.g. data/us30/US30_M5.parquet
```

Required columns: `time` (UTC), `open`, `high`, `low`, `close`. Optional: `tick_volume` (needed by
volume features) and `spread` in points (used for spread costs). Timeframes are `M1`, `M5`, `M15`, `H1`
and so on. The example hypotheses in `examples/hypotheses/` use US30 on M1 and M5.

From a CSV with those columns:

```python
import pandas as pd
df = pd.read_csv("US30_M5.csv", parse_dates=["time"])
df.to_parquet("data/us30/US30_M5.parquet", index=False)
```

From a MetaTrader 5 terminal on Windows: `scripts/export_mt5.py`.

Never commit market data. Parquet and CSV files under `data/` are gitignored.

## Canonical dataset (the author's setup)

Market data is deliberately excluded from Git and Supabase. Each Popper node receives the same separately versioned dataset, verifies its aggregate hash, and uses it read-only.

Canonical Popper Node v0.1 layout:

```text
data/
  ftmo-us30-v1/
    dataset-manifest.json
    ticks/YYYY/MM/YYYY-MM-DD.parquet
    bars/M1/YYYY/MM.parquet
    bars/M5/YYYY/MM.parquet
    bars/M15/YYYY/MM.parquet
```

Current dataset identity:

```text
version: ftmo-us30-v1
sha256: 7dce1c0c4e6ff630b8a0835bee05183f7902b6bb5a1753a93a600e62f0278c13
approved UTC period: 2021-10-04 through 2026-07-29 exclusive
```

Verify a local or received copy:

```powershell
.venv\Scripts\python.exe scripts\build_ftmo_dataset.py `
  --dataset-dir data\ftmo-us30-v1 --verify-only
```

The runner reads canonical M1/M5/M15 monthly partitions and caches each timeframe once. The legacy `data/us30/US30_M1.parquet` and `US30_M5.parquet` layout remains a fallback when canonical partitions are unavailable.

See `docs/FTMO_CANONICAL_DATASET.md` for source provenance, derivation rules, gap evidence and the documented tick/native-M1 snapshot differences.

Never commit broker data, `.tkc` files, CSV exports, account details, credentials, or local dataset manifests containing broker data inventory.
