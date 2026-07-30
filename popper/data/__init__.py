"""Data layer — load clean OHLCV bars the DSL features can consume (spec/06 Phase 2)."""

from popper.data.bars import DATA_DIR, load_bars, load_higher_timeframe_bars

__all__ = ["load_bars", "load_higher_timeframe_bars", "DATA_DIR"]
