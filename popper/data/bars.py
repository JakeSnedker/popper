"""US30 bar loader with period slicing.

The parquet from scripts/export_mt5.py is already clean (UTC-tagged, sorted,
no dupes/nulls). This just reads it, validates the OHLCV contract the features
need, and slices by [start, end]. M5 only — M1 is thin (spec/06). The raw read
is cached so repeated backtests over the same symbol don't re-hit disk.
"""

from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_OHLCV = ["open", "high", "low", "close"]


@lru_cache(maxsize=4)
def _read(symbol: str, timeframe: str) -> pd.DataFrame:
    path = DATA_DIR / symbol.lower() / f"{symbol}_{timeframe}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"no bars at {path}")
    df = pd.read_parquet(path)
    missing = {"time", *_OHLCV} - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def load_bars(
    symbol: str = "US30",
    timeframe: str = "M5",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Clean OHLCV DataFrame (columns: time, open, high, low, close, tick_volume,
    ...), optionally sliced to [start, end] inclusive. A fresh copy each call so
    callers/features can't mutate the cached frame."""
    df = _read(symbol, timeframe.upper())
    if start is not None:
        df = df[df["time"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df["time"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)
