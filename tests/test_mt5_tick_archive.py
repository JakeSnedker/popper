from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from popper.data.tick_archive import (
    export_ticks,
    export_windows,
    inventory_tick_caches,
    normalize_ticks,
    partition_path,
)


def _tick_array(day: int):
    base = int(datetime(2024, 1, day, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    dtype = [
        ("time", "i8"), ("bid", "f8"), ("ask", "f8"), ("last", "f8"),
        ("volume", "u8"), ("time_msc", "i8"), ("flags", "u4"),
        ("volume_real", "f8"),
    ]
    return np.array([
        (base // 1000, 38000.0, 38001.0, 0.0, 2, base, 6, 2.0),
        (base // 1000, 38000.5, 38001.5, 0.0, 1, base + 250, 6, 1.0),
    ], dtype=dtype)


def test_inventory_groups_months_and_never_changes_sources(tmp_path):
    root = tmp_path / "Terminal"
    cache = root / "ABC123" / "bases" / "FTMO-Demo" / "ticks" / "US30.cash"
    cache.mkdir(parents=True)
    january = cache / "202401.tkc"
    march = cache / "202403.tkc"
    january.write_bytes(b"immutable-january")
    march.write_bytes(b"immutable-march")
    other = root / "ABC123" / "bases" / "FTMO-Demo" / "ticks" / "EURUSD"
    other.mkdir(parents=True)
    (other / "202401.tkc").write_bytes(b"other")

    before = {path: path.read_bytes() for path in (january, march)}
    result = inventory_tick_caches(root, symbol="US30.cash", hash_files=True)

    assert result["file_count"] == 2
    assert result["group_count"] == 1
    group = result["groups"][0]
    assert group["terminal_id"] == "ABC123"
    assert group["server"] == "FTMO-Demo"
    assert group["first_month"] == "202401"
    assert group["last_month"] == "202403"
    assert group["missing_months"] == ["202402"]
    assert all(row["sha256"] for row in group["files"])
    assert {path: path.read_bytes() for path in (january, march)} == before


def test_export_windows_are_utc_and_end_exclusive():
    windows = export_windows(date(2024, 1, 1), date(2024, 1, 6), days=2)
    assert [(start.date(), end.date()) for start, end in windows] == [
        (date(2024, 1, 1), date(2024, 1, 3)),
        (date(2024, 1, 3), date(2024, 1, 5)),
        (date(2024, 1, 5), date(2024, 1, 6)),
    ]
    assert all(start.tzinfo == timezone.utc for start, _ in windows)
    with pytest.raises(ValueError):
        export_windows(date(2024, 1, 1), date(2024, 1, 1))


def test_normalize_ticks_orders_and_deduplicates():
    ticks = _tick_array(2)
    duplicated = np.concatenate([ticks[::-1], ticks[:1]])
    frame = normalize_ticks(duplicated)

    assert list(frame.columns) == [
        "time", "time_msc", "bid", "ask", "last", "volume", "volume_real", "flags",
    ]
    assert len(frame) == 2
    assert frame["time"].dt.tz is not None
    assert frame["time_msc"].is_monotonic_increasing


class FakeMT5:
    COPY_TICKS_ALL = 0

    def __init__(self):
        self.calls = []

    def copy_ticks_range(self, symbol, start, end, mode):
        self.calls.append((symbol, start, end, mode))
        return _tick_array(start.day)

    def last_error(self):
        return (0, "ok")


def test_export_is_atomic_partitioned_and_restartable(tmp_path):
    fake = FakeMT5()
    dataset = tmp_path / "ftmo-us30-v1"

    first = export_ticks(
        fake, "US30.cash", dataset, date(2024, 1, 1), date(2024, 1, 3),
        retry_delay=0, sleep=lambda _: None,
        source_metadata={"server": "FTMO-Demo"},
    )
    assert first["exported_partitions"] == 2
    assert first["skipped_partitions"] == 0
    assert first["total_rows"] == 4
    assert len(fake.calls) == 2
    assert first["source_metadata"] == {"server": "FTMO-Demo"}

    first_path = partition_path(dataset, datetime(2024, 1, 1, tzinfo=timezone.utc))
    second_path = partition_path(dataset, datetime(2024, 1, 2, tzinfo=timezone.utc))
    assert first_path.exists() and second_path.exists()
    assert not list(dataset.rglob("*.tmp"))
    frame = pd.read_parquet(first_path)
    assert len(frame) == 2
    assert frame["time"].min().date() == date(2024, 1, 1)

    fake.calls.clear()
    second = export_ticks(
        fake, "US30.cash", dataset, date(2024, 1, 1), date(2024, 1, 3),
        retry_delay=0, sleep=lambda _: None,
    )
    assert second["exported_partitions"] == 0
    assert second["skipped_partitions"] == 2
    assert second["total_rows"] == 4
    assert fake.calls == []
