"""Read-only MT5 tick-cache inventory and restartable tick export helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable
from zipfile import ZipFile

import pandas as pd
import pyarrow.parquet as pq

_MONTH = re.compile(r"^(20\d{2})(0[1-9]|1[0-2])$")
_TICK_COLUMNS = ["time", "time_msc", "bid", "ask", "last", "volume", "volume_real", "flags"]


@dataclass(frozen=True)
class TickCacheFile:
    terminal_id: str
    server: str
    symbol: str
    month: str
    path: str
    size_bytes: int
    modified_utc: str
    sha256: str | None = None


def _month_start(month: str) -> date:
    match = _MONTH.fullmatch(month)
    if not match:
        raise ValueError(f"invalid MT5 tick-cache month {month!r}")
    return date(int(match.group(1)), int(match.group(2)), 1)


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def _months_between(start: date, end: date) -> list[str]:
    months = []
    current = start
    while current < end:
        months.append(current.strftime("%Y%m"))
        current = _next_month(current)
    return months


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_identity(root: Path, path: Path) -> tuple[str, str, str] | None:
    """Extract terminal/server/symbol from .../<terminal>/bases/<server>/ticks/<symbol>."""
    parts = path.parts
    lower = [part.lower() for part in parts]
    try:
        bases_i = lower.index("bases")
        ticks_i = lower.index("ticks", bases_i + 1)
    except ValueError:
        return None
    if bases_i < 1 or ticks_i + 1 >= len(parts) or ticks_i != bases_i + 2:
        return None
    terminal_id = parts[bases_i - 1]
    server = parts[bases_i + 1]
    symbol = parts[ticks_i + 1]
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return terminal_id, server, symbol


def inventory_tick_caches(
    terminal_root: str | Path,
    symbol: str | None = None,
    hash_files: bool = False,
) -> dict:
    """Inventory monthly `.tkc` files without opening them unless hashing is requested."""
    root = Path(terminal_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"MT5 terminal root does not exist: {root}")
    wanted = symbol.lower() if symbol else None
    rows = []
    for path in sorted(root.rglob("*.tkc")):
        if not _MONTH.fullmatch(path.stem):
            continue
        identity = _cache_identity(root, path)
        if identity is None:
            continue
        terminal_id, server, cache_symbol = identity
        if wanted and cache_symbol.lower() != wanted:
            continue
        stat = path.stat()
        rows.append(TickCacheFile(
            terminal_id=terminal_id,
            server=server,
            symbol=cache_symbol,
            month=path.stem,
            path=str(path),
            size_bytes=stat.st_size,
            modified_utc=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            sha256=_sha256(path) if hash_files else None,
        ))

    groups = []
    grouped: dict[tuple[str, str, str], list[TickCacheFile]] = {}
    for row in rows:
        grouped.setdefault((row.terminal_id, row.server, row.symbol), []).append(row)
    for (terminal_id, server, cache_symbol), files in sorted(grouped.items()):
        months = sorted(file.month for file in files)
        start = _month_start(months[0])
        end = _next_month(_month_start(months[-1]))
        expected = _months_between(start, end)
        groups.append({
            "terminal_id": terminal_id,
            "server": server,
            "symbol": cache_symbol,
            "first_month": months[0],
            "last_month": months[-1],
            "month_count": len(months),
            "missing_months": [month for month in expected if month not in months],
            "total_size_bytes": sum(file.size_bytes for file in files),
            "files": [asdict(file) for file in files],
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_type": "mt5_terminal_cache",
        "terminal_root": str(root),
        "hashes_included": hash_files,
        "group_count": len(groups),
        "file_count": len(rows),
        "groups": groups,
    }


def inventory_tick_archive_zip(
    archive_path: str | Path,
    symbol: str = "US30.cash",
    server: str = "FTMO-Demo",
    hash_files: bool = False,
) -> dict:
    """Inventory a Google Drive/backup ZIP without extracting or changing it."""
    archive = Path(archive_path).expanduser().resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"tick archive does not exist: {archive}")
    rows = []
    with ZipFile(archive) as bundle:
        for info in bundle.infolist():
            member = PurePosixPath(info.filename)
            if info.is_dir() or member.is_absolute() or ".." in member.parts:
                continue
            if len(member.parts) != 2 or member.parts[0].lower() != symbol.lower():
                continue
            if not _MONTH.fullmatch(member.stem) or member.suffix.lower() != ".tkc":
                continue
            digest = None
            if hash_files:
                hasher = hashlib.sha256()
                with bundle.open(info, "r") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
            modified = datetime(*info.date_time, tzinfo=timezone.utc).isoformat()
            rows.append(TickCacheFile(
                terminal_id=archive.stem,
                server=server,
                symbol=member.parts[0],
                month=member.stem,
                path=f"{archive}!{info.filename}",
                size_bytes=info.file_size,
                modified_utc=modified,
                sha256=digest,
            ))

    rows.sort(key=lambda row: row.month)
    if rows:
        months = [row.month for row in rows]
        start = _month_start(months[0])
        end = _next_month(_month_start(months[-1]))
        expected = _months_between(start, end)
        groups = [{
            "terminal_id": archive.stem,
            "server": server,
            "symbol": rows[0].symbol,
            "first_month": months[0],
            "last_month": months[-1],
            "month_count": len(months),
            "missing_months": [month for month in expected if month not in months],
            "total_size_bytes": sum(row.size_bytes for row in rows),
            "files": [asdict(row) for row in rows],
        }]
    else:
        groups = []
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_type": "zip_tick_archive",
        "archive_path": str(archive),
        "archive_size_bytes": archive.stat().st_size,
        "archive_sha256": _sha256(archive) if hash_files else None,
        "hashes_included": hash_files,
        "group_count": len(groups),
        "file_count": len(rows),
        "groups": groups,
    }


def install_archive_months(
    archive_path: str | Path,
    cache_dir: str | Path,
    symbol: str = "US30.cash",
    before_month: str = "202401",
    apply: bool = False,
) -> dict:
    """Install only absent archive months; existing cache files are never replaced."""
    if not _MONTH.fullmatch(before_month):
        raise ValueError(f"invalid before-month {before_month!r}")
    archive = Path(archive_path).expanduser().resolve()
    destination_root = Path(cache_dir).expanduser().resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"tick archive does not exist: {archive}")
    if apply and not destination_root.is_dir():
        raise FileNotFoundError(f"MT5 symbol cache does not exist: {destination_root}")

    rows = []
    with ZipFile(archive) as bundle:
        members = []
        for info in bundle.infolist():
            member = PurePosixPath(info.filename)
            if (info.is_dir() or member.is_absolute() or ".." in member.parts
                    or len(member.parts) != 2
                    or member.parts[0].lower() != symbol.lower()
                    or member.suffix.lower() != ".tkc"
                    or not _MONTH.fullmatch(member.stem)
                    or member.stem >= before_month):
                continue
            members.append((member.stem, info))
        for month, info in sorted(members):
            destination = destination_root / f"{month}.tkc"
            if destination.exists():
                rows.append({
                    "month": month,
                    "status": "exists",
                    "destination": str(destination),
                    "size_bytes": destination.stat().st_size,
                })
                continue
            status = "would_install"
            digest = None
            if apply:
                temporary = destination.with_suffix(".tkc.importing")
                hasher = hashlib.sha256()
                try:
                    with bundle.open(info, "r") as source, temporary.open("wb") as output:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            output.write(chunk)
                            hasher.update(chunk)
                    if temporary.stat().st_size != info.file_size:
                        raise IOError(
                            f"size mismatch for {month}: {temporary.stat().st_size} != {info.file_size}"
                        )
                    # A hard link publishes the complete file atomically and fails
                    # rather than replacing a destination created concurrently.
                    os.link(temporary, destination)
                finally:
                    if temporary.exists():
                        temporary.unlink()
                status = "installed"
                digest = hasher.hexdigest()
            rows.append({
                "month": month,
                "status": status,
                "destination": str(destination),
                "size_bytes": info.file_size,
                "sha256": digest,
            })
    return {
        "archive": str(archive),
        "cache_dir": str(destination_root),
        "symbol": symbol,
        "before_month": before_month,
        "applied": apply,
        "installed": sum(row["status"] == "installed" for row in rows),
        "would_install": sum(row["status"] == "would_install" for row in rows),
        "existing": sum(row["status"] == "exists" for row in rows),
        "months": rows,
    }


def export_windows(start: date, end: date, days: int = 1) -> list[tuple[datetime, datetime]]:
    """UTC [start, end) windows. End is exclusive and must be after start."""
    if days < 1:
        raise ValueError("days per partition must be >= 1")
    if end <= start:
        raise ValueError("export end must be after start")
    current = datetime.combine(start, dt_time.min, tzinfo=timezone.utc)
    stop = datetime.combine(end, dt_time.min, tzinfo=timezone.utc)
    delta = timedelta(days=days)
    windows = []
    while current < stop:
        nxt = min(current + delta, stop)
        windows.append((current, nxt))
        current = nxt
    return windows


def normalize_ticks(ticks) -> pd.DataFrame:
    """MT5 structured tick array -> canonical, ordered UTC DataFrame."""
    if ticks is None:
        raise ValueError("MT5 returned None instead of a tick array")
    frame = pd.DataFrame(ticks)
    if frame.empty:
        return pd.DataFrame({
            "time": pd.Series([], dtype="datetime64[ns, UTC]"),
            "time_msc": pd.Series([], dtype="int64"),
            "bid": pd.Series([], dtype="float64"),
            "ask": pd.Series([], dtype="float64"),
            "last": pd.Series([], dtype="float64"),
            "volume": pd.Series([], dtype="uint64"),
            "volume_real": pd.Series([], dtype="float64"),
            "flags": pd.Series([], dtype="uint32"),
        })
    required = {"time_msc", "bid", "ask"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"MT5 tick array missing columns: {sorted(missing)}")
    defaults = {"last": 0.0, "volume": 0, "volume_real": 0.0, "flags": 0}
    for column, default in defaults.items():
        if column not in frame:
            frame[column] = default
    frame["time"] = pd.to_datetime(frame["time_msc"], unit="ms", utc=True)
    frame = frame[_TICK_COLUMNS]
    frame = frame.sort_values(["time_msc", "bid", "ask", "last", "flags"])
    return frame.drop_duplicates(
        subset=["time_msc", "bid", "ask", "last", "flags"], keep="last",
    ).reset_index(drop=True)


def partition_path(dataset_root: str | Path, start: datetime) -> Path:
    root = Path(dataset_root)
    return root / "ticks" / f"{start.year:04d}" / f"{start.month:02d}" / f"{start.date()}.parquet"


def validate_partition(path: str | Path, start: datetime, end: datetime) -> int:
    """Return row count for a complete partition, raising on schema/range corruption."""
    parquet_path = Path(path)
    metadata = pq.ParquetFile(parquet_path).metadata
    schema = set(pq.ParquetFile(parquet_path).schema.names)
    missing = set(_TICK_COLUMNS) - schema
    if missing:
        raise ValueError(f"{parquet_path} missing tick columns: {sorted(missing)}")
    rows = metadata.num_rows
    if rows:
        times = pd.read_parquet(parquet_path, columns=["time"])["time"]
        if times.min() < start or times.max() >= end:
            raise ValueError(f"{parquet_path} contains ticks outside [{start}, {end})")
    return rows


def write_partition(frame: pd.DataFrame, path: str | Path) -> None:
    """Write one partition atomically so interruption cannot leave a valid-looking partial file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, destination)


def export_ticks(
    mt5,
    symbol: str,
    dataset_root: str | Path,
    start: date,
    end: date,
    days_per_partition: int = 1,
    retries: int = 4,
    retry_delay: float = 2.0,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    source_metadata: dict | None = None,
) -> dict:
    """Export MT5 ticks through the official bridge into restartable Parquet partitions."""
    windows = export_windows(start, end, days_per_partition)
    exported = skipped = total_rows = 0
    partitions = []
    for window_start, window_end in windows:
        destination = partition_path(dataset_root, window_start)
        if destination.exists() and not force:
            rows = validate_partition(destination, window_start, window_end)
            skipped += 1
        else:
            ticks = None
            for attempt in range(retries):
                ticks = mt5.copy_ticks_range(
                    symbol,
                    window_start,
                    window_end - timedelta(milliseconds=1),
                    mt5.COPY_TICKS_ALL,
                )
                if ticks is not None:
                    break
                if attempt + 1 < retries:
                    sleep(retry_delay)
            if ticks is None:
                error = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
                raise RuntimeError(
                    f"MT5 tick export failed for {window_start.date()}: {error}"
                )
            frame = normalize_ticks(ticks)
            if not frame.empty:
                frame = frame[(frame["time"] >= window_start) & (frame["time"] < window_end)]
            write_partition(frame, destination)
            rows = validate_partition(destination, window_start, window_end)
            exported += 1
        total_rows += rows
        partitions.append({
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "path": str(destination.relative_to(Path(dataset_root))),
            "rows": rows,
        })

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "MetaTrader5.copy_ticks_range",
        "source_metadata": source_metadata or {},
        "symbol": symbol,
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "days_per_partition": days_per_partition,
        "partitions": partitions,
        "partition_count": len(partitions),
        "exported_partitions": exported,
        "skipped_partitions": skipped,
        "total_rows": total_rows,
    }
    manifest_path = Path(dataset_root) / "tick-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(temporary, manifest_path)
    return manifest
