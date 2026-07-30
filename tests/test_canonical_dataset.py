from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from popper.data.canonical_dataset import (
    analyze_m1_gaps,
    analyze_tick_m1_consistency,
    build_derived_partitions,
    compare_bar_frames,
    derive_bars,
    export_m1_bars,
    finalize_dataset_manifest,
    normalize_rates,
    verify_dataset,
)


def _rates(start: datetime, count: int = 6):
    dtype = [
        ("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
        ("close", "f8"), ("tick_volume", "u8"), ("spread", "u4"),
        ("real_volume", "u8"),
    ]
    rows = []
    for index in range(count):
        value = 38000.0 + index
        rows.append((
            int((start + timedelta(minutes=index)).timestamp()),
            value, value + 2.0, value - 1.0, value + 1.0,
            index + 1, 5 - min(index, 4), index,
        ))
    return np.array(rows, dtype=dtype)


def test_normalize_and_derive_bars_match_mt5_aggregation_rules():
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    m1 = normalize_rates(_rates(start)[::-1])

    m5 = derive_bars(m1, 5)

    assert len(m5) == 2
    assert m5.iloc[0].to_dict() == {
        "time": pd.Timestamp(start),
        "open": 38000.0,
        "high": 38006.0,
        "low": 37999.0,
        "close": 38005.0,
        "tick_volume": 15,
        "spread": 1,
        "real_volume": 10,
    }
    assert m5.iloc[1]["open"] == 38005.0
    assert m5.iloc[1]["close"] == 38006.0


def test_compare_bar_frames_requires_exact_rows_and_values():
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    bars = derive_bars(normalize_rates(_rates(start)), 5)

    assert compare_bar_frames(bars, bars.copy(), tolerance=0.001)["passed"] is True
    changed = bars.copy()
    changed.loc[0, "spread"] += 1
    result = compare_bar_frames(bars, changed, tolerance=0.001)
    assert result["passed"] is False
    assert result["fields"]["spread"]["mismatches"] == 1


class FakeMT5:
    def __init__(self):
        self.calls = []

    def copy_rates_range(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe, start, end))
        return _rates(start, 2)

    def last_error(self):
        return (1, "success")


def test_m1_export_resumes_and_derived_partitions_are_built(tmp_path):
    fake = FakeMT5()
    first = export_m1_bars(
        fake, 1, "US30.cash", tmp_path, date(2024, 1, 1), date(2024, 2, 1),
        retry_delay=0,
    )
    assert first["exported_partitions"] == 1
    assert first["total_rows"] == 2
    assert len(fake.calls) == 1

    fake.calls.clear()
    second = export_m1_bars(
        fake, 1, "US30.cash", tmp_path, date(2024, 1, 1), date(2024, 2, 1),
        retry_delay=0,
    )
    assert second["skipped_partitions"] == 1
    assert fake.calls == []

    derived = build_derived_partitions(tmp_path, second)
    assert derived["timeframes"]["M5"]["total_rows"] == 1
    assert derived["timeframes"]["M15"]["total_rows"] == 1


def test_tick_m1_consistency_proves_approved_period(tmp_path):
    start = pd.Timestamp("2024-01-02T00:00:00Z")
    ticks = pd.DataFrame({
        "time": [
            start, start + pd.Timedelta(seconds=10), start + pd.Timedelta(seconds=20),
            start + pd.Timedelta(seconds=50), start + pd.Timedelta(minutes=1),
            start + pd.Timedelta(minutes=1, seconds=10),
            start + pd.Timedelta(minutes=1, seconds=20),
            start + pd.Timedelta(minutes=1, seconds=50),
        ],
        "bid": [10.0, 12.0, 9.0, 11.0, 11.0, 14.0, 10.0, 13.0],
    })
    tick_path = tmp_path / "ticks" / "2024" / "01" / "2024-01-02.parquet"
    tick_path.parent.mkdir(parents=True)
    ticks.to_parquet(tick_path, index=False)
    m1 = pd.DataFrame({
        "time": [start, start + pd.Timedelta(minutes=1)],
        "open": [10.0, 11.0], "high": [12.0, 14.0], "low": [9.0, 10.0],
        "close": [11.0, 13.0], "tick_volume": [4, 4], "spread": [1, 1],
        "real_volume": [0, 0],
    })
    m1_path = tmp_path / "bars" / "M1" / "2024" / "01.parquet"
    m1_path.parent.mkdir(parents=True)
    m1.to_parquet(m1_path, index=False)
    tick_manifest = {"partitions": [{
        "start": "2024-01-02T00:00:00+00:00", "end": "2024-01-03T00:00:00+00:00",
        "path": "ticks/2024/01/2024-01-02.parquet", "rows": len(ticks),
    }]}
    m1_manifest = {"partitions": [{
        "start": "2024-01-01T00:00:00+00:00", "end": "2024-02-01T00:00:00+00:00",
        "path": "bars/M1/2024/01.parquet", "rows": len(m1),
    }]}

    result = analyze_tick_m1_consistency(
        tmp_path, tick_manifest, m1_manifest, date(2024, 1, 2), date(2024, 1, 3), 0.001,
    )

    assert result["exact_match"] is True
    assert result["common_rows"] == 2
    assert result["issue_day_count"] == 0


def test_gap_analysis_manifest_hash_and_verification(tmp_path):
    root = tmp_path
    tick_path = root / "ticks" / "2024" / "01" / "2024-01-01.parquet"
    tick_path.parent.mkdir(parents=True)
    tick_frame = pd.DataFrame({
        "time": pd.to_datetime(["2024-01-01T00:00:00Z", "2024-01-01T00:04:00Z"]),
    })
    tick_frame.to_parquet(tick_path, index=False)

    m1 = normalize_rates(_rates(datetime(2024, 1, 1, tzinfo=timezone.utc), 5))
    m1 = m1.drop(index=[2, 3]).reset_index(drop=True)
    m1_path = root / "bars" / "M1" / "2024" / "01.parquet"
    m1_path.parent.mkdir(parents=True)
    m1.to_parquet(m1_path, index=False)
    m5 = derive_bars(m1, 5)
    m15 = derive_bars(m1, 15)
    for timeframe, frame in (("M5", m5), ("M15", m15)):
        path = root / "bars" / timeframe / "2024" / "01.parquet"
        path.parent.mkdir(parents=True)
        frame.to_parquet(path, index=False)

    partition = {
        "start": "2024-01-01T00:00:00+00:00",
        "end": "2024-02-01T00:00:00+00:00",
        "path": "bars/M1/2024/01.parquet",
        "rows": len(m1),
    }
    tick_manifest = {
        "source": "MetaTrader5.copy_ticks_range",
        "start": "2024-01-01",
        "end_exclusive": "2024-02-01",
        "partition_count": 1,
        "total_rows": len(tick_frame),
        "partitions": [{"path": "ticks\\2024\\01\\2024-01-01.parquet", "rows": 2}],
    }
    m1_manifest = {
        "source": "MetaTrader5.copy_rates_range",
        "partition_count": 1,
        "total_rows": len(m1),
        "partitions": [partition],
    }
    bar_manifest = {
        "source": "deterministic aggregation of canonical M1",
        "rules": {"timezone": "UTC"},
        "timeframes": {
            "M5": {
                "partition_count": 1, "total_rows": len(m5),
                "partitions": [{**partition, "path": "bars/M5/2024/01.parquet", "rows": len(m5)}],
            },
            "M15": {
                "partition_count": 1, "total_rows": len(m15),
                "partitions": [{**partition, "path": "bars/M15/2024/01.parquet", "rows": len(m15)}],
            },
        },
    }
    gap_report = analyze_m1_gaps(root, m1_manifest)
    assert gap_report["gap_count"] == 1
    assert gap_report["total_missing_minutes"] == 2
    tick_m1_consistency = {
        "approved_start": "2024-01-01",
        "approved_end_exclusive": "2024-02-01",
        "exact_match": True,
        "issue_day_count": 0,
        "approved_issue_day_count": 0,
    }
    (root / "tick-m1-consistency.json").write_text(
        '{"exact_match": true}', encoding="utf-8",
    )
    (root / "native-validation.json").write_text('{"passed": true}', encoding="utf-8")

    manifest = finalize_dataset_manifest(
        root, "test-v1", tick_manifest, m1_manifest, bar_manifest, gap_report,
        tick_m1_consistency,
        {"passed": True, "report": "native-validation.json"},
        {"server": "FTMO-Demo", "symbol": "US30.cash"},
    )
    assert manifest["file_count"] == 4
    assert verify_dataset(root)["valid"] is True

    (root / "native-validation.json").write_text('{"passed": false}', encoding="utf-8")
    with pytest.raises(ValueError, match="report hash mismatch"):
        verify_dataset(root)
