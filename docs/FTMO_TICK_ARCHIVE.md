# FTMO Tick Archive Inventory And Export

Inventory date: 2026-07-28.
Step: Popper Node v0.1 master plan, Step 2.

## Confirmed Local Cache

The standard MT5 terminal data directory contains one relevant cache:

| Field | Value |
|---|---|
| Terminal cache | `D0E8209F77C8CF37AD8BF550E51FF075` |
| Server | `FTMO-Demo` |
| Symbol | `US30.cash` |
| Monthly files | 31 |
| Nominal months | `202401` through `202607` |
| Missing monthly filenames | none |
| Total source size | approximately 0.25 GiB |
| Source type | MT5 monthly `.tkc` cache |

A SHA-256 inventory of all 31 source files was written locally to the ignored path `data/_inventory/mt5_tick_inventory.json`. Inventorying reads the source files but never changes them.

The nominal filename range does not prove every trading day is complete. Exact tick coverage and gaps are established only after the terminal exports and validates the records.

## Older Archive Search

No additional `.tkc` files were found in the standard MT5 Terminal tree, Desktop, Documents, Downloads, or project directories. The active machine therefore currently exposes January 2024 onward, not the expected five years. Pre-2024 archives may be on another machine, backup, external drive, or a different MT5 data directory.

## Current MT5 Gate

The installed terminal is open, but its saved FTMO demo account is no longer valid. The official Python bridge returns `Terminal: Authorization failed`, so a live `CopyTicksRange` proof cannot run until MT5 is logged into a valid FTMO account on the same server/symbol family.

Do not put account IDs, passwords, or investor credentials in this repository. Authentication stays inside MT5.

## Inventory Command

```powershell
.venv\Scripts\python.exe scripts\inventory_mt5_ticks.py --symbol US30.cash --hash
```

The command discovers monthly cache files, groups them by terminal/server/symbol, reports missing months and total size, and optionally hashes each source file.

## Restartable Export Command

The exporter reads ticks only through the official MetaTrader5 bridge and writes atomic UTC Parquet partitions. Existing valid partitions are validated and skipped on rerun.

```powershell
.venv\Scripts\python.exe scripts\export_mt5_ticks.py `
  --symbol US30.cash `
  --start 2024-01-01 `
  --end 2026-08-01 `
  --dataset-dir data\ftmo-us30-v1
```

The end date is exclusive. The current cache range produces 943 daily partitions in a dry run.

Dry run:

```powershell
.venv\Scripts\python.exe scripts\export_mt5_ticks.py `
  --start 2024-01-01 --end 2026-08-01 --dry-run
```

## Output Contract

```text
data/ftmo-us30-v1/
  tick-manifest.json
  ticks/
    2024/
      01/
        2024-01-01.parquet
        ...
```

Every tick partition contains:

```text
time, time_msc, bid, ask, last, volume, volume_real, flags
```

The manifest records the source method, non-secret server metadata, requested UTC coverage, partition paths, row counts, and resume counts. Market data and local inventory files are ignored by Git.

## Completion Proof Still Required

1. Log the terminal into a valid FTMO account.
2. Export and validate at least one known day from the 2024 cache.
3. Confirm the earliest and latest actual tick timestamps.
4. Locate or explicitly account for the expected pre-2024 archives.
5. Run the complete partitioned export once the full source set is assembled.

If an authenticated Python bridge cannot see cache files that MT5 can use in Strategy Tester, the fallback is a terminal-side MQL5 `CopyTicksRange` exporter. Direct binary `.tkc` parsing remains the last option.
