"""Build and verify the versioned FTMO tick and bar dataset."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

_BAR_COLUMNS = [
    "time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume",
]
_DERIVED_TIMEFRAMES = {"M5": 5, "M15": 15}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(value: dict, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, destination)


def _write_parquet_atomic(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, destination)


def _month_windows(start: date, end: date) -> list[tuple[datetime, datetime]]:
    if end <= start:
        raise ValueError("dataset end must be after start")
    current = datetime.combine(start, dt_time.min, tzinfo=timezone.utc)
    stop = datetime.combine(end, dt_time.min, tzinfo=timezone.utc)
    windows = []
    while current < stop:
        if current.month == 12:
            next_month = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)
        window_end = min(next_month, stop)
        windows.append((current, window_end))
        current = window_end
    return windows


def bar_partition_path(dataset_root: str | Path, timeframe: str, start: datetime) -> Path:
    return Path(dataset_root) / "bars" / timeframe / f"{start.year:04d}" / f"{start.month:02d}.parquet"


def normalize_rates(rates) -> pd.DataFrame:
    """MT5 structured rates array to the canonical ordered bar schema."""
    if rates is None:
        raise ValueError("MT5 returned None instead of a rates array")
    frame = pd.DataFrame(rates)
    if frame.empty:
        return pd.DataFrame({
            "time": pd.Series([], dtype="datetime64[ns, UTC]"),
            "open": pd.Series([], dtype="float64"),
            "high": pd.Series([], dtype="float64"),
            "low": pd.Series([], dtype="float64"),
            "close": pd.Series([], dtype="float64"),
            "tick_volume": pd.Series([], dtype="uint64"),
            "spread": pd.Series([], dtype="uint32"),
            "real_volume": pd.Series([], dtype="uint64"),
        })
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"MT5 rates array missing columns: {sorted(missing)}")
    for column, default in (("tick_volume", 0), ("spread", 0), ("real_volume", 0)):
        if column not in frame:
            frame[column] = default
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    frame = frame[_BAR_COLUMNS].sort_values("time", kind="stable")
    frame = frame.drop_duplicates(subset="time", keep="last").reset_index(drop=True)
    invalid = (
        (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame["low"])
    )
    if invalid.any():
        raise ValueError(f"MT5 rates contain {int(invalid.sum())} invalid OHLC rows")
    return frame


def validate_bar_partition(path: str | Path, start: datetime, end: datetime) -> int:
    parquet_path = Path(path)
    parquet = pq.ParquetFile(parquet_path)
    missing = set(_BAR_COLUMNS) - set(parquet.schema.names)
    if missing:
        raise ValueError(f"{parquet_path} missing bar columns: {sorted(missing)}")
    rows = parquet.metadata.num_rows
    if rows:
        times = pd.read_parquet(parquet_path, columns=["time"])["time"]
        if times.min() < start or times.max() >= end:
            raise ValueError(f"{parquet_path} contains bars outside [{start}, {end})")
        if not times.is_monotonic_increasing or times.duplicated().any():
            raise ValueError(f"{parquet_path} bar times are not unique and ordered")
    return rows


def export_m1_bars(
    mt5,
    timeframe_m1: int,
    symbol: str,
    dataset_root: str | Path,
    start: date,
    end: date,
    retries: int = 4,
    retry_delay: float = 2.0,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    source_metadata: dict | None = None,
) -> dict:
    """Export official MT5 M1 rates into restartable monthly partitions."""
    root = Path(dataset_root)
    exported = skipped = total_rows = 0
    partitions = []
    for window_start, window_end in _month_windows(start, end):
        destination = bar_partition_path(root, "M1", window_start)
        if destination.exists() and not force:
            rows = validate_bar_partition(destination, window_start, window_end)
            skipped += 1
        else:
            rates = None
            for attempt in range(retries):
                rates = mt5.copy_rates_range(
                    symbol,
                    timeframe_m1,
                    window_start,
                    window_end - timedelta(seconds=1),
                )
                if rates is not None:
                    break
                if attempt + 1 < retries:
                    sleep(retry_delay)
            if rates is None:
                error = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
                raise RuntimeError(f"MT5 M1 export failed for {window_start:%Y-%m}: {error}")
            frame = normalize_rates(rates)
            frame = frame[(frame["time"] >= window_start) & (frame["time"] < window_end)]
            _write_parquet_atomic(frame, destination)
            rows = validate_bar_partition(destination, window_start, window_end)
            exported += 1
        total_rows += rows
        partitions.append({
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "path": destination.relative_to(root).as_posix(),
            "rows": rows,
        })

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "MetaTrader5.copy_rates_range",
        "source_metadata": source_metadata or {},
        "symbol": symbol,
        "timeframe": "M1",
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "partitions": partitions,
        "partition_count": len(partitions),
        "exported_partitions": exported,
        "skipped_partitions": skipped,
        "total_rows": total_rows,
    }
    _write_json_atomic(manifest, root / "m1-source-manifest.json")
    return manifest


def derive_bars(m1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate canonical M1 bars on UTC epoch-aligned boundaries."""
    if minutes not in _DERIVED_TIMEFRAMES.values():
        raise ValueError(f"unsupported derived timeframe: {minutes} minutes")
    missing = set(_BAR_COLUMNS) - set(m1.columns)
    if missing:
        raise ValueError(f"M1 frame missing columns: {sorted(missing)}")
    if m1.empty:
        return m1[_BAR_COLUMNS].copy()
    ordered = m1[_BAR_COLUMNS].sort_values("time", kind="stable").copy()
    ordered["bucket"] = ordered["time"].dt.floor(f"{minutes}min")
    grouped = ordered.groupby("bucket", sort=True)
    bars = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        tick_volume=("tick_volume", "sum"),
        spread=("spread", "min"),
        real_volume=("real_volume", "sum"),
    ).reset_index().rename(columns={"bucket": "time"})
    return bars[_BAR_COLUMNS]


def build_derived_partitions(dataset_root: str | Path, m1_manifest: dict) -> dict:
    """Derive M5 and M15 from each M1 month and atomically replace outputs."""
    root = Path(dataset_root)
    derived = {timeframe: [] for timeframe in _DERIVED_TIMEFRAMES}
    totals = {timeframe: 0 for timeframe in _DERIVED_TIMEFRAMES}
    for source in m1_manifest["partitions"]:
        window_start = datetime.fromisoformat(source["start"])
        window_end = datetime.fromisoformat(source["end"])
        m1 = pd.read_parquet(root / source["path"])
        for timeframe, minutes in _DERIVED_TIMEFRAMES.items():
            frame = derive_bars(m1, minutes)
            destination = bar_partition_path(root, timeframe, window_start)
            _write_parquet_atomic(frame, destination)
            rows = validate_bar_partition(destination, window_start, window_end)
            totals[timeframe] += rows
            derived[timeframe].append({
                "start": source["start"],
                "end": source["end"],
                "path": destination.relative_to(root).as_posix(),
                "rows": rows,
            })
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "deterministic aggregation of canonical M1",
        "rules": {
            "timezone": "UTC",
            "alignment": "epoch-aligned left-closed intervals",
            "ohlc": "first open, maximum high, minimum low, last close",
            "tick_volume": "sum",
            "spread": "minimum, matching MT5 higher-timeframe MqlRates semantics",
            "real_volume": "sum",
        },
        "timeframes": {
            timeframe: {
                "minutes": _DERIVED_TIMEFRAMES[timeframe],
                "partition_count": len(derived[timeframe]),
                "total_rows": totals[timeframe],
                "partitions": derived[timeframe],
            }
            for timeframe in _DERIVED_TIMEFRAMES
        },
    }
    _write_json_atomic(manifest, root / "bar-manifest.json")
    return manifest


def analyze_m1_gaps(dataset_root: str | Path, m1_manifest: dict) -> dict:
    root = Path(dataset_root)
    time_frames = [
        pd.read_parquet(root / part["path"], columns=["time"])
        for part in m1_manifest["partitions"]
        if part["rows"]
    ]
    if not time_frames:
        raise ValueError("canonical M1 dataset has no bars")
    times = pd.concat(time_frames, ignore_index=True)["time"].sort_values().reset_index(drop=True)
    duplicates = int(times.duplicated().sum())
    if duplicates:
        raise ValueError(f"canonical M1 dataset has {duplicates} duplicate timestamps")
    deltas = times.diff()
    gap_indexes = np.flatnonzero((deltas > pd.Timedelta(minutes=1)).to_numpy())
    gaps = []
    for index in gap_indexes:
        previous = times.iloc[index - 1]
        current = times.iloc[index]
        missing_minutes = int((current - previous) / pd.Timedelta(minutes=1)) - 1
        gaps.append({
            "start": (previous + pd.Timedelta(minutes=1)).isoformat(),
            "end_exclusive": current.isoformat(),
            "missing_minutes": missing_minutes,
        })
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframe": "M1",
        "first_bar": times.iloc[0].isoformat(),
        "last_bar": times.iloc[-1].isoformat(),
        "bar_count": len(times),
        "duplicate_timestamps": 0,
        "gap_count": len(gaps),
        "total_missing_minutes": sum(gap["missing_minutes"] for gap in gaps),
        "largest_gap_minutes": max((gap["missing_minutes"] for gap in gaps), default=0),
        "gaps": gaps,
    }
    _write_json_atomic(result, root / "m1-gap-report.json")
    return result


def analyze_tick_m1_consistency(
    dataset_root: str | Path,
    tick_manifest: dict,
    m1_manifest: dict,
    approved_start: date,
    approved_end: date,
    tolerance: float,
) -> dict:
    """Prove daily tick-derived bid OHLC against canonical native M1 bars."""
    root = Path(dataset_root)
    m1_by_month = {
        datetime.fromisoformat(part["start"]).strftime("%Y-%m"): part
        for part in m1_manifest["partitions"]
    }
    current_month = None
    monthly_m1 = None
    totals = {
        "canonical_m1_rows": 0,
        "tick_derived_m1_rows": 0,
        "common_rows": 0,
        "canonical_only_rows": 0,
        "tick_only_rows": 0,
        "ohlc_mismatches": {column: 0 for column in ["open", "high", "low", "close"]},
        "max_abs_difference": {column: 0.0 for column in ["open", "high", "low", "close"]},
    }
    approved_totals = {
        "canonical_only_rows": 0,
        "tick_only_rows": 0,
        "ohlc_mismatches": {column: 0 for column in ["open", "high", "low", "close"]},
    }
    issues = []
    approved_issues = []
    for part in tick_manifest["partitions"]:
        day_start = datetime.fromisoformat(part["start"])
        day_end = datetime.fromisoformat(part["end"])
        month = day_start.strftime("%Y-%m")
        if month != current_month:
            monthly_m1 = pd.read_parquet(root / m1_by_month[month]["path"])
            current_month = month
        canonical = monthly_m1[
            (monthly_m1["time"] >= day_start) & (monthly_m1["time"] < day_end)
        ][["time", "open", "high", "low", "close"]]
        tick_path = _safe_file(root, part["path"].replace("\\", "/"))
        ticks = pd.read_parquet(tick_path, columns=["time", "bid"])
        if ticks.empty:
            derived = canonical.iloc[:0].copy()
        else:
            ticks = ticks.sort_values("time", kind="stable")
            ticks["minute"] = ticks["time"].dt.floor("min")
            derived = ticks.groupby("minute", sort=True)["bid"].agg(
                open="first", high="max", low="min", close="last",
            ).reset_index().rename(columns={"minute": "time"})
        joined = canonical.merge(
            derived, on="time", how="outer", suffixes=("_canonical", "_tick"), indicator=True,
        )
        common = joined[joined["_merge"] == "both"]
        day_mismatches = {}
        day_maximums = {}
        for column in ["open", "high", "low", "close"]:
            difference = (
                common[f"{column}_canonical"] - common[f"{column}_tick"]
            ).abs()
            count = int((difference > tolerance).sum())
            maximum = float(difference.max()) if len(difference) else 0.0
            day_mismatches[column] = count
            day_maximums[column] = maximum
            totals["ohlc_mismatches"][column] += count
            totals["max_abs_difference"][column] = max(
                totals["max_abs_difference"][column], maximum,
            )
        canonical_only = int((joined["_merge"] == "left_only").sum())
        tick_only = int((joined["_merge"] == "right_only").sum())
        totals["canonical_m1_rows"] += len(canonical)
        totals["tick_derived_m1_rows"] += len(derived)
        totals["common_rows"] += len(common)
        totals["canonical_only_rows"] += canonical_only
        totals["tick_only_rows"] += tick_only
        in_approved_period = approved_start <= day_start.date() < approved_end
        if in_approved_period:
            approved_totals["canonical_only_rows"] += canonical_only
            approved_totals["tick_only_rows"] += tick_only
            for column, count in day_mismatches.items():
                approved_totals["ohlc_mismatches"][column] += count
        if canonical_only or tick_only or any(day_mismatches.values()):
            issue = {
                "date": day_start.date().isoformat(),
                "canonical_m1_rows": len(canonical),
                "tick_derived_m1_rows": len(derived),
                "canonical_only_rows": canonical_only,
                "tick_only_rows": tick_only,
                "ohlc_mismatches": day_mismatches,
                "max_abs_difference": day_maximums,
            }
            issues.append(issue)
            if in_approved_period:
                approved_issues.append(issue)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rule": "stable tick sequence grouped to UTC M1 bid OHLC",
        "price_tolerance": tolerance,
        "approved_start": approved_start.isoformat(),
        "approved_end_exclusive": approved_end.isoformat(),
        "exact_match": not approved_issues,
        **totals,
        "approved_period_differences": approved_totals,
        "issue_day_count": len(issues),
        "approved_issue_day_count": len(approved_issues),
        "issue_days": issues,
    }
    _write_json_atomic(result, root / "tick-m1-consistency.json")
    return result


def compare_bar_frames(derived: pd.DataFrame, native: pd.DataFrame, tolerance: float) -> dict:
    """Compare derived bars with native MT5 bars on their exact timestamps."""
    left = derived[_BAR_COLUMNS].copy()
    right = native[_BAR_COLUMNS].copy()
    joined = left.merge(right, on="time", how="outer", suffixes=("_derived", "_native"), indicator=True)
    common = joined[joined["_merge"] == "both"]
    fields = {}
    for column in ["open", "high", "low", "close"]:
        difference = (common[f"{column}_derived"] - common[f"{column}_native"]).abs()
        fields[column] = {
            "mismatches": int((difference > tolerance).sum()),
            "max_abs_difference": float(difference.max()) if len(difference) else None,
        }
    for column in ["tick_volume", "spread", "real_volume"]:
        difference = (
            common[f"{column}_derived"].astype("int64")
            - common[f"{column}_native"].astype("int64")
        ).abs()
        fields[column] = {
            "mismatches": int((difference != 0).sum()),
            "max_abs_difference": float(difference.max()) if len(difference) else None,
        }
    passed = (
        len(common) > 0
        and int((joined["_merge"] == "left_only").sum()) == 0
        and int((joined["_merge"] == "right_only").sum()) == 0
        and all(field["mismatches"] == 0 for field in fields.values())
    )
    return {
        "passed": passed,
        "derived_rows": len(left),
        "native_rows": len(right),
        "common_rows": len(common),
        "derived_only_rows": int((joined["_merge"] == "left_only").sum()),
        "native_only_rows": int((joined["_merge"] == "right_only").sum()),
        "fields": fields,
    }


def _safe_file(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"dataset manifest path escapes root: {relative}") from exc
    return candidate


def finalize_dataset_manifest(
    dataset_root: str | Path,
    dataset_version: str,
    tick_manifest: dict,
    m1_manifest: dict,
    bar_manifest: dict,
    gap_report: dict,
    tick_m1_consistency: dict,
    native_validation: dict,
    metadata: dict,
) -> dict:
    """Hash every Parquet file and write the distributable dataset manifest."""
    root = Path(dataset_root).resolve()
    row_counts = {
        part["path"].replace("\\", "/"): part["rows"]
        for part in tick_manifest["partitions"]
    }
    row_counts.update({part["path"]: part["rows"] for part in m1_manifest["partitions"]})
    for timeframe in _DERIVED_TIMEFRAMES:
        row_counts.update({
            part["path"]: part["rows"]
            for part in bar_manifest["timeframes"][timeframe]["partitions"]
        })

    files = []
    for relative, rows in sorted(row_counts.items()):
        path = _safe_file(root, relative)
        if not path.is_file():
            raise FileNotFoundError(f"dataset file missing: {path}")
        files.append({
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "rows": rows,
            "sha256": _sha256(path),
        })
    digest = hashlib.sha256()
    for file in files:
        digest.update(
            f"{file['path']}\0{file['size_bytes']}\0{file['sha256']}\n".encode("utf-8")
        )
    dataset_sha256 = digest.hexdigest()

    nonempty_ticks = [part for part in tick_manifest["partitions"] if part["rows"]]
    first_ticks = pd.read_parquet(
        _safe_file(root, nonempty_ticks[0]["path"].replace("\\", "/")), columns=["time"],
    )["time"]
    last_ticks = pd.read_parquet(
        _safe_file(root, nonempty_ticks[-1]["path"].replace("\\", "/")), columns=["time"],
    )["time"]
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": dataset_version,
        "dataset_sha256": dataset_sha256,
        "broker": metadata,
        "timezone": "UTC",
        "coverage": {
            "requested_start": tick_manifest["start"],
            "requested_end_exclusive": tick_manifest["end_exclusive"],
            "approved_start": tick_m1_consistency["approved_start"],
            "approved_end_exclusive": tick_m1_consistency["approved_end_exclusive"],
            "first_tick": first_ticks.min().isoformat(),
            "last_tick": last_ticks.max().isoformat(),
            "first_m1_bar": gap_report["first_bar"],
            "last_m1_bar": gap_report["last_bar"],
        },
        "ticks": {
            "source": tick_manifest["source"],
            "partition_count": tick_manifest["partition_count"],
            "total_rows": tick_manifest["total_rows"],
        },
        "bars": {
            "derivation_rules": bar_manifest["rules"],
            "M1": {
                "source": m1_manifest["source"],
                "partition_count": m1_manifest["partition_count"],
                "total_rows": m1_manifest["total_rows"],
            },
            **{
                timeframe: {
                    "source": bar_manifest["source"],
                    "partition_count": bar_manifest["timeframes"][timeframe]["partition_count"],
                    "total_rows": bar_manifest["timeframes"][timeframe]["total_rows"],
                }
                for timeframe in _DERIVED_TIMEFRAMES
            },
        },
        "m1_gaps": {
            "gap_count": gap_report["gap_count"],
            "total_missing_minutes": gap_report["total_missing_minutes"],
            "largest_gap_minutes": gap_report["largest_gap_minutes"],
            "report": "m1-gap-report.json",
            "report_sha256": _sha256(root / "m1-gap-report.json"),
        },
        "tick_m1_consistency": {
            "exact_match": tick_m1_consistency["exact_match"],
            "issue_day_count": tick_m1_consistency["issue_day_count"],
            "approved_issue_day_count": tick_m1_consistency["approved_issue_day_count"],
            "report": "tick-m1-consistency.json",
            "report_sha256": _sha256(root / "tick-m1-consistency.json"),
        },
        "native_validation": {
            **native_validation,
            "report_sha256": _sha256(root / native_validation["report"]),
        },
        "file_count": len(files),
        "total_size_bytes": sum(file["size_bytes"] for file in files),
        "files": files,
    }
    _write_json_atomic(manifest, root / "dataset-manifest.json")
    return manifest


def verify_dataset(dataset_root: str | Path) -> dict:
    root = Path(dataset_root).resolve()
    manifest_path = root / "dataset-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    checked_bytes = 0
    for expected in manifest["files"]:
        path = _safe_file(root, expected["path"])
        if not path.is_file():
            raise FileNotFoundError(f"dataset file missing: {path}")
        size = path.stat().st_size
        if size != expected["size_bytes"]:
            raise ValueError(f"dataset size mismatch for {expected['path']}: {size}")
        rows = pq.ParquetFile(path).metadata.num_rows
        if rows != expected["rows"]:
            raise ValueError(f"dataset row-count mismatch for {expected['path']}: {rows}")
        file_hash = _sha256(path)
        if file_hash != expected["sha256"]:
            raise ValueError(f"dataset hash mismatch for {expected['path']}")
        checked_bytes += size
        digest.update(f"{expected['path']}\0{size}\0{file_hash}\n".encode("utf-8"))
    actual = digest.hexdigest()
    if actual != manifest["dataset_sha256"]:
        raise ValueError(f"dataset aggregate hash mismatch: {actual}")
    for section in (
        manifest["m1_gaps"], manifest["tick_m1_consistency"], manifest["native_validation"],
    ):
        report = _safe_file(root, section["report"])
        if _sha256(report) != section["report_sha256"]:
            raise ValueError(f"dataset report hash mismatch for {section['report']}")
    return {
        "valid": True,
        "dataset_version": manifest["dataset_version"],
        "dataset_sha256": actual,
        "file_count": len(manifest["files"]),
        "total_size_bytes": checked_bytes,
    }
