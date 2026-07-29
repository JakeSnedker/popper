"""Export MT5 ticks to restartable daily/weekly Parquet partitions.

The source `.tkc` cache is accessed only through the official MetaTrader5 bridge
and is never opened for writing.

Examples:
  python scripts/export_mt5_ticks.py --start 2024-01-01 --end 2024-02-01
  python scripts/export_mt5_ticks.py --start 2024-01-01 --end 2026-08-01 --dry-run
"""

import argparse
from datetime import date
from pathlib import Path

from popper.data.tick_archive import export_ticks, export_windows


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default="US30.cash")
    parser.add_argument("--start", type=iso_date, required=True)
    parser.add_argument("--end", type=iso_date, required=True, help="exclusive UTC end date")
    parser.add_argument("--days-per-partition", type=int, default=1)
    parser.add_argument("--dataset-dir", type=Path, default=Path("data") / "ftmo-us30-v1")
    parser.add_argument("--terminal", type=Path, help="explicit terminal64.exe path")
    parser.add_argument("--force", action="store_true", help="replace valid existing partitions")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    windows = export_windows(args.start, args.end, args.days_per_partition)
    if args.dry_run:
        print(
            f"dry run: {len(windows)} partition(s), {args.symbol}, "
            f"{args.start}..{args.end} exclusive -> {args.dataset_dir}"
        )
        return 0

    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise SystemExit(
            "MetaTrader5 package is required on Windows: pip install MetaTrader5"
        ) from exc

    initialized = mt5.initialize(str(args.terminal)) if args.terminal else mt5.initialize()
    if not initialized:
        raise SystemExit(
            f"MT5 initialization failed: {mt5.last_error()}. "
            "Open the FTMO terminal, log in, and retry."
        )
    try:
        if not mt5.symbol_select(args.symbol, True):
            raise SystemExit(f"MT5 cannot select {args.symbol}: {mt5.last_error()}")
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        metadata = {
            "server": getattr(account, "server", None),
            "company": getattr(account, "company", None),
            "terminal_build": getattr(terminal, "build", None),
        }
        manifest = export_ticks(
            mt5=mt5,
            symbol=args.symbol,
            dataset_root=args.dataset_dir,
            start=args.start,
            end=args.end,
            days_per_partition=args.days_per_partition,
            force=args.force,
            source_metadata=metadata,
        )
    finally:
        mt5.shutdown()

    print(
        f"export complete: {manifest['total_rows']:,} ticks, "
        f"{manifest['exported_partitions']} written, "
        f"{manifest['skipped_partitions']} resumed, "
        f"{manifest['partition_count']} total partitions"
    )
    print(f"manifest: {args.dataset_dir / 'tick-manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
