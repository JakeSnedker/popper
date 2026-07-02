

A strategy is a *vector*, not a number. Produced by the Scorecard subsystem (Phase 4),
consumed by the Falsification Gauntlet to reach a Verdict.
All metrics normalized 0..1 where higher = better, except pbo (lower = better).
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Scorecard(BaseModel):
    id: str | None = None  # assigned by alpha memory as SC-#### if absent
    experiment_id: str
    robustness: float = 0.0  # consistency across CPCV folds
    parameter_stability: float = 0.0  # edge holds across the param neighbourhood (Phase 3)
    regime_stability: float = 0.0  # survives regime change?
    capacity: float = 0.0  # PnL retained as size scales
    execution_realism: float = 0.0  # 1 - (backtest vs modeled-fill gap)
    novelty: float = 0.0  # distance in alpha-memory embedding space
    correlation: float = 0.0  # max |corr| vs the validated pool (LOWER is better)
    deflated_sharpe: float = 0.0  # Sharpe corrected for # trials + non-normality
    pbo: float = 1.0  # probability of backtest overfitting (LOWER is better)
    behaviour_signature: dict = Field(default_factory=dict)  # post-test behaviour facets
    created_at: datetime = Field(default_factory=_now)
