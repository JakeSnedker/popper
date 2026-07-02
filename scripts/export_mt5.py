"""One-time MT5 -> Parquet export for Popper.

Run with the FTMO-Demo terminal OPEN and logged in (the bridge reads from the
running terminal, not the .tkc cache directly).

Setup once:   pip install MetaTrader5 pandas pyarrow
Run:          python scripts/export_mt5.py

Exports US30.cash M1 + M5 bars (2024-01 -> now) to data/us30/*.parquet.
Commit them so the build is reproducible without a live terminal.

Notes on this MT5 build (5.0.5735):
- copy_rates_range() returns "Invalid params" (broken) — avoided.
- copy_rates_from_pos() with a big count also 400s (>=100k) and is capped by the
  terminal's "Max bars in chart" setting.
- So we page FORWARD with copy_rates_from(date, count): requesting a past date
  makes the terminal download that history from the server on demand (no manual
  chart-scrolling needed). Retry per chunk covers the async download latency.

# ponytail: bars only. Raw-tick export (execution-realism / sim fidelity, a later
#           phase) is added when that phase lands, chunked by month to avoid OOM.
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import MetaTrader5 as mt5

SYMBOL = "US30.cash"
START = pd.Timestamp(datetime(2024, 1, 1, tzinfo=timezone.utc))
OUT = Path(__file__).resolve().parents[1] / "data" / "us30"
TIMEFRAMES = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5}
CHUNK = 50_000    # bars per call (this build 400s at >=100k; 50k works)
MAX_CHUNKS = 120  # safety stop
RETRIES = 6       # per-chunk retries while the terminal downloads history async


def fetch(tf):
    """Page forward from START with copy_rates_from; triggers on-demand download."""
    frames = []
    cur = START
    last_seen = None
    for _ in range(MAX_CHUNKS):
        rates = None
        for _attempt in range(RETRIES):
            rates = mt5.copy_rates_from(SYMBOL, tf, cur.to_pydatetime(), CHUNK)
            if rates is not None and len(rates) > 0:
                break
            time.sleep(2)  # history download in progress — wait and retry
        if rates is None or len(rates) == 0:
            break
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        frames.append(df)
        last = df["time"].iloc[-1]
        if last_seen is not None and last <= last_seen:  # no forward progress
            break
        last_seen = last
        if len(rates) < CHUNK:  # reached the present
            break
        cur = last  # next window; overlap dropped below
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
            print(f"[warn] {name}: no bars >= {START.date()} ({mt5.last_error()})")
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
