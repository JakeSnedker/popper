"""One-time MT5 -> Parquet export for Popper.

Run with the FTMO-Demo terminal OPEN and logged in (the bridge reads from the
running terminal, not the .tkc cache directly).

Setup once:   pip install MetaTrader5 pandas pyarrow
Run:          python scripts/export_mt5.py

Exports US30.cash M1 + M5 bars (2024-01 -> now) to data/us30/*.parquet.
These bars are the data layer for the Experiment Runner; commit them so the
build is reproducible without a live terminal.

# ponytail: bars only. Raw-tick export (for execution-realism / sim fidelity,

#           that phase lands, chunked by month to avoid OOM on ~2yr of index ticks.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import MetaTrader5 as mt5

SYMBOL = "US30.cash"
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime.now(timezone.utc)
OUT = Path(__file__).resolve().parents[1] / "data" / "us30"
TIMEFRAMES = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5}


def main() -> None:
    if not mt5.initialize():
        sys.exit(f"MT5 init failed: {mt5.last_error()} — is the terminal open and logged in?")
    if not mt5.symbol_select(SYMBOL, True):
        mt5.shutdown()
        sys.exit(f"symbol_select failed for {SYMBOL}: {mt5.last_error()}")

    OUT.mkdir(parents=True, exist_ok=True)
    wrote = 0
    for name, tf in TIMEFRAMES.items():
        rates = mt5.copy_rates_range(SYMBOL, tf, START, END)
        if rates is None or len(rates) == 0:
            print(f"[warn] no {name} bars: {mt5.last_error()}")
            continue
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        path = OUT / f"US30_{name}.parquet"
        df.to_parquet(path, index=False)
        wrote += 1
        print(f"[ok] {name}: {len(df):,} bars  {df.time.min()} -> {df.time.max()}  -> {path}")

    mt5.shutdown()
    if wrote == 0:
        sys.exit("No data written — check the symbol name and that history is loaded in the terminal.")
    print(f"\nDone. {wrote} timeframe(s) written to {OUT}")


if __name__ == "__main__":
    main()
