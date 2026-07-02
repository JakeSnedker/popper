"""One-time MT5 -> Parquet export for Popper.

Run with the FTMO-Demo terminal OPEN and logged in (the bridge reads from the
running terminal, not the .tkc cache directly).

Setup once:   pip install MetaTrader5 pandas pyarrow
Run:          python scripts/export_mt5.py

Exports US30.cash M1 + M5 bars (2024-01 -> now) to data/us30/*.parquet.
Commit them so the build is reproducible without a live terminal.

Notes on this MT5 build (5.0.5735):
- copy_rates_range() returns "Invalid params" (broken) — avoided.
- copy_rates_from_pos() with a huge count also returns "Invalid params" — so we
  page it in fixed CHUNK-sized calls, walking backward until we pass START.

# ponytail: bars only. Raw-tick export (execution-realism / sim fidelity, a later
#           phase) is added when that phase lands, chunked by month to avoid OOM.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import MetaTrader5 as mt5

SYMBOL = "US30.cash"
START = pd.Timestamp(datetime(2024, 1, 1, tzinfo=timezone.utc))
OUT = Path(__file__).resolve().parents[1] / "data" / "us30"
TIMEFRAMES = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5}
CHUNK = 50_000    # bars per copy_rates_from_pos call (this build 400s at >=100k; 50k works)
MAX_CHUNKS = 120  # safety stop (120 * 50k = 6M bars)


def fetch(tf):
    """Page copy_rates_from_pos backward until the batch reaches START."""
    frames = []
    for i in range(MAX_CHUNKS):
        rates = mt5.copy_rates_from_pos(SYMBOL, tf, i * CHUNK, CHUNK)
        if rates is None or len(rates) == 0:
            break
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        frames.append(df)
        if df["time"].iloc[0] <= START or len(rates) < CHUNK:
            break
    if not frames:
        return None
    out = (pd.concat(frames, ignore_index=True)
             .drop_duplicates(subset="time")
             .sort_values("time")
             .reset_index(drop=True))
    return out[out["time"] >= START].reset_index(drop=True)


def main() -> None:
    if not mt5.initialize():
        sys.exit(f"MT5 init failed: {mt5.last_error()} — is the terminal open and logged in?")
    if not mt5.symbol_select(SYMBOL, True):
        mt5.shutdown()
        sys.exit(f"symbol_select failed for {SYMBOL}: {mt5.last_error()}")

    OUT.mkdir(parents=True, exist_ok=True)
    wrote = 0
    for name, tf in TIMEFRAMES.items():
        df = fetch(tf)
        if df is None or df.empty:
            print(f"[warn] {name}: no bars >= {START.date()} ({mt5.last_error()}). "
                  f"If only recent data returns, set Tools>Options>Charts 'Max bars' to Unlimited "
                  f"and scroll US30 back to 2024.")
            continue
        path = OUT / f"US30_{name}.parquet"
        df.to_parquet(path, index=False)
        wrote += 1
        print(f"[ok] {name}: {len(df):,} bars  {df.time.min()} -> {df.time.max()}  -> {path}")

    mt5.shutdown()
    if wrote == 0:
        sys.exit("No data written.")
    print(f"\nDone. {wrote} timeframe(s) written to {OUT}")


if __name__ == "__main__":
    main()
