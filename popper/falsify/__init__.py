"""Falsification Gauntlet (spec/03): judge a spec -> Scorecard + Verdict."""

from popper.falsify.gauntlet import GauntletConfig, Thresholds, calculate_windows, judge
from popper.falsify.stats import (
    calculate_edge,
    deflated_sharpe,
    monte_carlo_ruin,
    pbo_cscv,
    purged_kfold,
)

__all__ = [
    "judge",
    "GauntletConfig",
    "Thresholds",
    "calculate_windows",
    "deflated_sharpe",
    "pbo_cscv",
    "monte_carlo_ruin",
    "calculate_edge",
    "purged_kfold",
]
