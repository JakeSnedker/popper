# Market Data

Market data is deliberately excluded from Git and Supabase. Each Popper node mounts or copies the same separately versioned, read-only dataset into this directory.

Current research layout:

```text
data/
  us30/
    US30_M1.parquet
    US30_M5.parquet
```

The Popper Node v0.1 target will replace this ad-hoc layout with a dataset manifest, immutable version/hash, partitioned FTMO ticks, canonical M1 bars, and derived M5/M15 bars. See `docs/POPPER_NODE_V01_MASTER_PLAN.md`.

Never commit broker data, `.tkc` files, CSV exports, account details, or credentials.
