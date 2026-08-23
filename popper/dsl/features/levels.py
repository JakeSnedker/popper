"""Level-features (DSL v0.3) — point-in-time PRICE LEVELS that stops and targets
anchor to (docs/DSL_v0.3_EXIT_SPEC.md §4a).

Unlike signal-features (which a signal expression references), these are exit
anchors: a structural price a stop sits under or a target reaches for. Same
non-negotiable rule as every feature — POINT-IN-TIME CORRECT: the value at bar i
uses only bars 0..i. Structural levels lag by construction (a swing/fractal is
only known once the bars that define it have printed), which is exactly what
keeps them causal.

Wave 1 (first-pass menu). Deferred to wave 2: stdev_band, fib levels, FVG edges,
and the multi-timeframe swing lookback (N as a swept axis).
"""

import pandas as pd

from popper.dsl.features import bb_line  # already PIT-correct (SMA 20)
from popper.dsl.features import _session, _time

DEFAULT_SWING_N = 20  # placeholder lookback (spec §4a — becomes a swept axis in wave 2)


def _body_hi(bars: pd.DataFrame) -> pd.Series:
    return bars[["open", "close"]].max(axis=1)


def _body_lo(bars: pd.DataFrame) -> pd.Series:
    return bars[["open", "close"]].min(axis=1)


# --- swing wick / body (rolling extreme of the PRIOR n bars) ---
def prior_swing_wick_hi(bars: pd.DataFrame, n: int = DEFAULT_SWING_N) -> pd.Series:
    return bars["high"].rolling(n, min_periods=n).max().shift(1)


def prior_swing_wick_lo(bars: pd.DataFrame, n: int = DEFAULT_SWING_N) -> pd.Series:
    return bars["low"].rolling(n, min_periods=n).min().shift(1)


def prior_swing_body_hi(bars: pd.DataFrame, n: int = DEFAULT_SWING_N) -> pd.Series:
    return _body_hi(bars).rolling(n, min_periods=n).max().shift(1)


def prior_swing_body_lo(bars: pd.DataFrame, n: int = DEFAULT_SWING_N) -> pd.Series:
    return _body_lo(bars).rolling(n, min_periods=n).min().shift(1)


# --- Bill Williams fractal (5-bar: 2 lower/higher on each side) ---
def _fractal(series: pd.Series, high_side: bool) -> pd.Series:
    """Most recent CONFIRMED BW fractal level, forward-filled. A fractal at bar i
    is confirmed at i+2 (needs 2 bars each side), so we publish its value at i+2 —
    that lag is what makes it point-in-time safe."""
    s = series
    if high_side:
        is_frac = (s > s.shift(1)) & (s > s.shift(2)) & (s > s.shift(-1)) & (s > s.shift(-2))
    else:
        is_frac = (s < s.shift(1)) & (s < s.shift(2)) & (s < s.shift(-1)) & (s < s.shift(-2))
    known = s.where(is_frac).shift(2)  # value becomes known 2 bars after the fractal bar
    return known.ffill()


def prior_fractal_hi(bars: pd.DataFrame) -> pd.Series:
    return _fractal(bars["high"], high_side=True)


def prior_fractal_lo(bars: pd.DataFrame) -> pd.Series:
    return _fractal(bars["low"], high_side=False)


# --- prior completed session high / low ---
def _prior_session_level(bars: pd.DataFrame, session: str, high: bool) -> pd.Series:
    """High/low of the PREVIOUS day's `session` (Asia/London/NY), mapped onto every
    bar. Previous-day is unambiguously complete, so it's causal; the intraday
    'most recent completed session' refinement is wave 2."""
    day = pd.Index(_time(bars).date)
    in_s = _session(bars, session).astype(bool).to_numpy()
    src = bars["high"] if high else bars["low"]
    vals = pd.Series(src.where(in_s).to_numpy(), index=day)
    per_day = vals.groupby(level=0).max() if high else vals.groupby(level=0).min()
    prior = per_day.shift(1)  # previous day's session extreme
    return pd.Series(prior.reindex(day).to_numpy(), index=bars.index)


def session_ny_hi(bars): return _prior_session_level(bars, "ny", True)
def session_ny_lo(bars): return _prior_session_level(bars, "ny", False)
def session_london_hi(bars): return _prior_session_level(bars, "london", True)
def session_london_lo(bars): return _prior_session_level(bars, "london", False)
def session_asia_hi(bars): return _prior_session_level(bars, "asia", True)
def session_asia_lo(bars): return _prior_session_level(bars, "asia", False)


_LEVELS = {
    "prior_fractal_hi": prior_fractal_hi,
    "prior_fractal_lo": prior_fractal_lo,
    "prior_swing_wick_hi": prior_swing_wick_hi,
    "prior_swing_wick_lo": prior_swing_wick_lo,
    "prior_swing_body_hi": prior_swing_body_hi,
    "prior_swing_body_lo": prior_swing_body_lo,
    "session_ny_hi": session_ny_hi,
    "session_ny_lo": session_ny_lo,
    "session_london_hi": session_london_hi,
    "session_london_lo": session_london_lo,
    "session_asia_hi": session_asia_hi,
    "session_asia_lo": session_asia_lo,
    "bb_line": bb_line,
}

# the exit-anchor vocabulary (spec §4a). atr distance is risk-relative and lives
# in the `atr` stop type (compiler), not here — this set is price levels only.
LEVEL_FEATURES = frozenset(_LEVELS)

# which side of entry each level may sit on (compiler enforces SL=loss side,
# TP=profit side). 'either' = usable both ways (e.g. bb_line).
# Levels whose value at bar i reads bar i's OWN high/low/close, so the value is
# NOT knowable at that bar's open -- which is exactly when a fill happens.
#   bb_line          averages in bar i's close
#   prior_fractal_*  confirms an i-2 fractal using bar i's high/low
# Safe as a SIGNAL (evaluated at bar i's close, entry next bar) and safe as a
# resting-order price (placed from bar i's close). NOT safe as an exit anchor
# resolved at the fill bar -- see EXIT_ANCHOR_LAG in popper/dsl/compiler.py.
INTRA_BAR_LEVELS = {"bb_line", "prior_fractal_hi", "prior_fractal_lo"}

LOSS_SIDE = {"prior_fractal_lo", "prior_swing_wick_lo", "prior_swing_body_lo",
             "session_ny_lo", "session_london_lo", "session_asia_lo"}
PROFIT_SIDE = {"prior_fractal_hi", "prior_swing_wick_hi", "prior_swing_body_hi",
               "session_ny_hi", "session_london_hi", "session_asia_hi"}


def resolve_level(name: str):
    """Level-feature name -> callable(bars) -> Series, or None if unknown."""
    return _LEVELS.get(name)


def compute_level(name: str, bars: pd.DataFrame) -> pd.Series:
    fn = resolve_level(name)
    if fn is None:
        raise KeyError(f"unknown level-feature: {name}")
    return fn(bars)
