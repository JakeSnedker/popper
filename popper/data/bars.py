"""Cached bar loader for canonical partitioned and legacy Parquet data."""

from functools import lru_cache
from pathlib import Path

import pandas as pd

from popper.config import settings
from popper.data.timeframes import normalize_timeframe

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
_configured_dataset = Path(settings.dataset_dir).expanduser()
CANONICAL_DATASET_DIR = (
    _configured_dataset if _configured_dataset.is_absolute()
    else REPO_ROOT / _configured_dataset
)
_OHLCV = ["open", "high", "low", "close"]


def _validate(frame: pd.DataFrame, source: Path) -> pd.DataFrame:
    missing = {"time", *_OHLCV} - set(frame.columns)
    if missing:
        raise ValueError(f"{source} missing columns: {sorted(missing)}")
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame.sort_values("time", kind="stable").drop_duplicates("time", keep="last").reset_index(drop=True)


@lru_cache(maxsize=8)
def _read(symbol: str, timeframe: str) -> pd.DataFrame:
    canonical = normalize_timeframe(timeframe)
    canonical_root = CANONICAL_DATASET_DIR / "bars" / canonical
    if symbol.upper() == "US30" and canonical_root.is_dir():
        paths = sorted(canonical_root.glob("*/*.parquet"))
        if paths:
            frame = pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True)
            return _validate(frame, canonical_root)

    path = DATA_DIR / symbol.lower() / f"{symbol}_{canonical}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"No {symbol} {canonical} market data found. Popper ships without market data; "
            f"put your own bars at {path} (columns: time, open, high, low, close). "
            "See data/README.md for the format."
        )
    return _validate(pd.read_parquet(path), path)


def load_bars(
    symbol: str = "US30",
    timeframe: str = "M5",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Return canonical OHLCV bars, optionally sliced to inclusive UTC bounds."""
    frame = _read(symbol, normalize_timeframe(timeframe))
    if start is not None:
        frame = frame[frame["time"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        frame = frame[frame["time"] <= pd.Timestamp(end, tz="UTC")]
    return frame.reset_index(drop=True)


def load_higher_timeframe_bars(
    symbol: str,
    higher_timeframe_features: dict[str, list[str]],
    end: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Load each declared context once; retain pre-period history for feature warmup."""
    return {
        normalize_timeframe(timeframe): load_bars(symbol, timeframe, end=end)
        for timeframe in higher_timeframe_features
    }
