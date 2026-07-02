"""The five immutable artifacts that flow between Popper's subsystems.

Hypothesis -> StrategySpec -> ExperimentResult -> Scorecard -> Verdict

These schemas ARE the interfaces. Subsystems are stateless functions over them.
"""

from popper.artifacts.enums import HypothesisStatus, Regime, VerdictType
from popper.artifacts.experiment import ExperimentResult
from popper.artifacts.hypothesis import Hypothesis
from popper.artifacts.scorecard import Scorecard
from popper.artifacts.strategy_spec import (
    BASELINE_WRAPPER,
    Alpha,
    Context,
    StrategySpec,
    Wrapper,
)
from popper.artifacts.verdict import Verdict

__all__ = [
    "Hypothesis",
    "StrategySpec",
    "Alpha",
    "Wrapper",
    "Context",
    "BASELINE_WRAPPER",
    "ExperimentResult",
    "Scorecard",
    "Verdict",
    "HypothesisStatus",
    "VerdictType",
    "Regime",
]
