"""Inventory monthly MT5 `.tkc` caches without modifying or parsing source files.

Examples:
  python scripts/inventory_mt5_ticks.py
  python scripts/inventory_mt5_ticks.py --symbol US30.cash --hash
"""

import argparse
import json
import os
from pathlib import Path

from popper.data.tick_archive import inventory_tick_caches


def default_terminal_root() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; pass --terminal-root explicitly")
    return Path(appdata) / "MetaQuotes" / "Terminal"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--terminal-root", type=Path, default=default_terminal_root())
    parser.add_argument("--symbol", default="US30.cash")
    parser.add_argument("--hash", action="store_true", help="SHA-256 every source file (slow)")
    parser.add_argument(
        "--output", type=Path,
        default=Path("data") / "_inventory" / "mt5_tick_inventory.json",
    )
    args = parser.parse_args()

    inventory = inventory_tick_caches(args.terminal_root, args.symbol, args.hash)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2), encoding="utf-8")

    print(f"inventory: {inventory['file_count']} monthly files in {inventory['group_count']} cache(s)")
    for group in inventory["groups"]:
        gib = group["total_size_bytes"] / 1024 ** 3
        print(
            f"  {group['server']} / {group['symbol']}: "
            f"{group['first_month']}..{group['last_month']} "
            f"({group['month_count']} months, {gib:.2f} GiB)"
        )
        if group["missing_months"]:
            print(f"    missing: {', '.join(group['missing_months'])}")
    print(f"wrote {args.output}")
    return 0 if inventory["file_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
