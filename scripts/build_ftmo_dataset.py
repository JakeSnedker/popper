"""Build and verify the canonical FTMO US30 tick/M1/M5/M15 dataset."""

import argparse
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

from popper.data.canonical_dataset import (
    analyze_m1_gaps,
    analyze_tick_m1_consistency,
    bar_partition_path,
    build_derived_partitions,
    compare_bar_frames,
    export_m1_bars,
    finalize_dataset_manifest,
    normalize_rates,
    verify_dataset,
)


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def write_json_atomic(value: dict, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, destination)


def native_validation(mt5, root: Path, symbol: str, samples: list[date], point: float) -> dict:
    results = []
    timeframe_constants = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15}
    for sample in samples:
        start = datetime.combine(sample, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        for timeframe, constant in timeframe_constants.items():
            rates = mt5.copy_rates_range(symbol, constant, start, end - timedelta(seconds=1))
            if rates is None:
                raise RuntimeError(
                    f"native {timeframe} validation failed for {sample}: {mt5.last_error()}"
                )
            native = normalize_rates(rates)
            partition = bar_partition_path(root, timeframe, start)
            derived = pd.read_parquet(partition)
            derived = derived[(derived["time"] >= start) & (derived["time"] < end)]
            comparison = compare_bar_frames(derived, native, tolerance=point / 10)
            comparison.update({"date": sample.isoformat(), "timeframe": timeframe})
            results.append(comparison)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rule": "M5/M15 derived from official M1 must equal native MT5 bars on sampled days",
        "price_tolerance": point / 10,
        "passed": all(result["passed"] for result in results),
        "samples": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset-dir", type=Path, default=Path("data") / "ftmo-us30-v1")
    parser.add_argument("--dataset-version", default="ftmo-us30-v1")
    parser.add_argument("--symbol", default="US30.cash")
    parser.add_argument("--start", type=iso_date, default=date(2021, 10, 1))
    parser.add_argument("--end", type=iso_date, default=date(2026, 7, 29), help="exclusive")
    parser.add_argument(
        "--approved-start", type=iso_date, default=date(2021, 10, 4),
        help="first complete common tick/bar research date",
    )
    parser.add_argument(
        "--samples", type=iso_date, nargs="+",
        default=[
            date(2021, 10, 4), date(2022, 1, 10), date(2024, 1, 2),
            date(2025, 3, 21), date(2026, 7, 28),
        ],
    )
    parser.add_argument("--terminal", type=Path)
    parser.add_argument("--force-m1", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    root = args.dataset_dir.resolve()
    if not args.start <= args.approved_start < args.end:
        raise SystemExit("approved start must fall within requested dataset coverage")
    if any(sample < args.approved_start or sample >= args.end for sample in args.samples):
        raise SystemExit("native validation samples must fall within the approved period")
    if args.verify_only:
        result = verify_dataset(root)
        print(
            f"valid: {result['dataset_version']} {result['dataset_sha256']} "
            f"({result['file_count']} files, {result['total_size_bytes']:,} bytes)"
        )
        return 0

    tick_manifest_path = root / "tick-manifest.json"
    if not tick_manifest_path.is_file():
        raise SystemExit(f"tick manifest missing: {tick_manifest_path}")
    tick_manifest = json.loads(tick_manifest_path.read_text(encoding="utf-8"))
    if tick_manifest["start"] != args.start.isoformat() or tick_manifest["end_exclusive"] != args.end.isoformat():
        raise SystemExit(
            "tick manifest coverage does not match requested canonical coverage: "
            f"{tick_manifest['start']}..{tick_manifest['end_exclusive']}"
        )

    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise SystemExit(
            "MetaTrader5 package is required on Windows: pip install MetaTrader5"
        ) from exc

    initialized = mt5.initialize(str(args.terminal)) if args.terminal else mt5.initialize()
    if not initialized:
        raise SystemExit(f"MT5 initialization failed: {mt5.last_error()}")
    try:
        if not mt5.symbol_select(args.symbol, True):
            raise SystemExit(f"MT5 cannot select {args.symbol}: {mt5.last_error()}")
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        symbol_info = mt5.symbol_info(args.symbol)
        metadata = {
            "server": getattr(account, "server", None),
            "company": getattr(account, "company", None),
            "symbol": args.symbol,
            "digits": getattr(symbol_info, "digits", None),
            "point": getattr(symbol_info, "point", None),
            "trade_tick_size": getattr(symbol_info, "trade_tick_size", None),
            "terminal_build": getattr(terminal, "build", None),
        }
        m1_manifest = export_m1_bars(
            mt5=mt5,
            timeframe_m1=mt5.TIMEFRAME_M1,
            symbol=args.symbol,
            dataset_root=root,
            start=args.start,
            end=args.end,
            force=args.force_m1,
            source_metadata=metadata,
        )
        bar_manifest = build_derived_partitions(root, m1_manifest)
        validation = native_validation(
            mt5, root, args.symbol, args.samples, float(metadata["point"]),
        )
    finally:
        mt5.shutdown()

    gap_report = analyze_m1_gaps(root, m1_manifest)
    tick_m1_consistency = analyze_tick_m1_consistency(
        root, tick_manifest, m1_manifest, args.approved_start, args.end,
        tolerance=float(metadata["point"]) / 10,
    )
    write_json_atomic(validation, root / "native-validation.json")
    manifest = finalize_dataset_manifest(
        dataset_root=root,
        dataset_version=args.dataset_version,
        tick_manifest=tick_manifest,
        m1_manifest=m1_manifest,
        bar_manifest=bar_manifest,
        gap_report=gap_report,
        tick_m1_consistency=tick_m1_consistency,
        native_validation={
            "passed": validation["passed"],
            "sample_count": len(validation["samples"]),
            "report": "native-validation.json",
        },
        metadata=metadata,
    )
    verified = verify_dataset(root)
    print(
        f"dataset built: {manifest['dataset_version']} {manifest['dataset_sha256']}\n"
        f"ticks: {manifest['ticks']['total_rows']:,}; "
        f"M1: {manifest['bars']['M1']['total_rows']:,}; "
        f"M5: {manifest['bars']['M5']['total_rows']:,}; "
        f"M15: {manifest['bars']['M15']['total_rows']:,}\n"
        f"files: {verified['file_count']}; bytes: {verified['total_size_bytes']:,}; "
        f"native validation: {'passed' if validation['passed'] else 'FAILED'}; "
        f"tick/M1 source-difference days: "
        f"{tick_m1_consistency['approved_issue_day_count']}"
    )
    if not validation["passed"]:
        raise SystemExit("native M5/M15 validation failed; inspect native-validation.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
