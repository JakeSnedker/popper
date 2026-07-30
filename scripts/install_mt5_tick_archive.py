"""Safely install only missing monthly `.tkc` files from a backup ZIP.

Dry-run is the default. Close MT5 before using --apply, then restart it so the
terminal can discover the newly installed months.
"""

import argparse
import json
from pathlib import Path

from popper.data.tick_archive import install_archive_months


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--symbol", default="US30.cash")
    parser.add_argument("--before-month", default="202401")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--report", type=Path,
        default=Path("data") / "_inventory" / "mt5_tick_archive_install.json",
    )
    args = parser.parse_args()

    result = install_archive_months(
        archive_path=args.archive,
        cache_dir=args.cache_dir,
        symbol=args.symbol,
        before_month=args.before_month,
        apply=args.apply,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")

    action = "installed" if args.apply else "would install"
    count = result["installed"] if args.apply else result["would_install"]
    print(
        f"{action}: {count} month(s); existing: {result['existing']}; "
        f"cutoff: < {result['before_month']}"
    )
    print(f"report: {args.report}")
    if not args.apply and count:
        print("dry run only: close MT5 and rerun with --apply to install")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
