# FTMO Tick Archive Inventory And Export

Step 2 completed: 2026-07-29.

## Proven Source Set

Two immutable sources were inventoried and SHA-256 hashed:

| Source | Nominal months | Files | Size | Notes |
|---|---:|---:|---:|---|
| Active MT5 `FTMO-Demo/US30.cash` cache | `202401` through `202607` | 31 | approximately 0.25 GiB before refresh | No missing monthly filenames |
| Google Drive ZIP backup | `202110` through `202502` | 39 | 347.73 MiB ZIP / approximately 0.36 GiB contents | Missing `202412` and `202501`, both present in active cache |

Backup archive:

```text
US30.cash-20260729T063254Z-1-001.zip
SHA-256 EC21D05E80D877911817981885229E155CC3ED39C96F926BD3466D36CDE762B2
```

The backup overlapped the active cache in 12 months. Every overlap differed, so none was overwritten. The installer imported only the 27 absent months from October 2021 through December 2023.

After import, the active terminal cache contains 58 continuous monthly filenames from `202110` through `202607`. All 27 installed files match their archived SHA-256 values. The ZIP was never modified, and no pre-existing cache file was overwritten.

Local detailed inventories and install reports live under the Git-ignored `data/_inventory/` directory.

## Official MT5 Proof

The terminal is authenticated against `FTMO-Demo`, the symbol is `US30.cash`, and the official `MetaTrader5.copy_ticks_range` bridge reads both imported historical files and current broker history.

| Proof window | Rows | First tick UTC | Last tick UTC | Duplicate canonical ticks |
|---|---:|---|---|---:|
| 2021-10-01 | 49,737 | `2021-10-01 14:32:41.423` | `2021-10-01 23:49:57.276` | 0 |
| 2021-10-04 | 95,911 | `2021-10-04 01:05:00.107` | `2021-10-04 23:49:58.866` | 0 |
| 2024-01-02 | 84,031 | `2024-01-02 01:05:11.889` | `2024-01-02 23:49:57.732` | 0 |
| 2026-07-28 | 178,280 | `2026-07-28 01:05:00.078` | `2026-07-28 23:49:57.976` | 0 |

Proven usable coverage is therefore `2021-10-01 14:32:41.423 UTC` through at least `2026-07-28 23:49:57.976 UTC`. Day/session gaps across the full range are measured in Step 3 when the canonical dataset is built.

## Inventory Commands

Active terminal cache:

```powershell
.venv\Scripts\python.exe scripts\inventory_mt5_ticks.py --symbol US30.cash --hash
```

Backup ZIP without extraction:

```powershell
.venv\Scripts\python.exe scripts\inventory_mt5_ticks.py `
  --archive C:\path\to\US30.cash-backup.zip `
  --symbol US30.cash --server FTMO-Demo --hash
```

The inventory code rejects path traversal, filters to `<symbol>/YYYYMM.tkc`, reports missing months and hashes every source when requested.

## Missing-Only Archive Install

Dry run is the default:

```powershell
.venv\Scripts\python.exe scripts\install_mt5_tick_archive.py `
  --archive C:\path\to\US30.cash-backup.zip `
  --cache-dir C:\path\to\FTMO-Demo\ticks\US30.cash `
  --before-month 202401
```

Close MT5, review the report, then add `--apply`. Each absent month is streamed to a temporary file, size-checked, and atomically linked into place. Existing files are never replaced.

## Restartable Tick Export

```powershell
.venv\Scripts\python.exe scripts\export_mt5_ticks.py `
  --symbol US30.cash `
  --start 2021-10-01 `
  --end 2026-07-29 `
  --dataset-dir data\ftmo-us30-v1
```

The end date is exclusive. The exporter writes daily UTC Parquet partitions atomically. Existing valid partitions are schema/range validated and skipped, making an interrupted multi-year export safe to resume.

Output contract:

```text
data/ftmo-us30-v1/
  tick-manifest.json
  ticks/
    2021/
      10/
        2021-10-01.parquet
        ...
```

Every partition contains:

```text
time, time_msc, bid, ask, last, volume, volume_real, flags
```

Market data, source inventories, local paths, and account credentials are excluded from Git and Supabase.

## Step 2 Completion Evidence

- Monthly source files, server and symbol are known.
- Backup ZIP and every `.tkc` source are fingerprinted.
- Historical files are imported without overwriting pre-existing cache months.
- Earliest and latest usable ticks are proven through the official terminal API.
- Export is atomic, partitioned, validated and restartable.
- Offline tests cover inventory, ZIP safety, missing-only install, normalization and resume behavior.

Next: Step 3 builds the complete canonical tick/M1 dataset, derives M5/M15, measures all gaps, and assigns a distributable dataset version/hash.
