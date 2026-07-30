"""Inventory monthly MT5 `.tkc` caches without modifying or parsing source files.

Examples:
  python scripts/inventory_mt5_ticks.py
  python scripts/inventory_mt5_ticks.py --symbol US30.cash --hash
"""

import argparse
import json
import os
from pathlib import Path

from popper.data.tick_archive import inventory_tick_archive_zip, inventory_tick_caches


def default_terminal_root() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; pass --terminal-root explicitly")
    return Path(appdata) / "MetaQuotes" / "Terminal"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--terminal-root", type=Path)
    source.add_argument("--archive", type=Path, help="ZIP containing <symbol>/YYYYMM.tkc")
    parser.add_argument("--symbol", default="US30.cash")
    parser.add_argument("--server", default="FTMO-Demo", help="archive source label")
    parser.add_argument("--hash", action="store_true", help="SHA-256 every source file (slow)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.archive:
        inventory = inventory_tick_archive_zip(
            args.archive, symbol=args.symbol, server=args.server, hash_files=args.hash,
        )
        default_output = Path("data") / "_inventory" / "mt5_tick_archive_inventory.json"
    else:
        inventory = inventory_tick_caches(
            args.terminal_root or default_terminal_root(), args.symbol, args.hash,
        )
        default_output = Path("data") / "_inventory" / "mt5_tick_inventory.json"
    args.output = args.output or default_output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2), encoding="utf-8")

    print(f"inventory: {inventory['file_count']} monthly files in {inventory['group_count']} cache(s)")
    for group in inventory["groups"]:
        gib = group["total_size_bytes"] / 1024 ** 3
        print(
            f"  {group['server']} / {group['symbol']}: "
            f"{group['first_month']}..{group['last_month']} "
            f"({group['month_count']} files, {gib:.2f} GiB)"
        )
        if group["missing_months"]:
            print(f"    missing: {', '.join(group['missing_months'])}")
    print(f"wrote {args.output}")
    return 0 if inventory["file_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
