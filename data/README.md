# Market Data

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
