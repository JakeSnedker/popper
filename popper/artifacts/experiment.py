"""ExperimentResult — raw output of running a StrategySpec through a backtest.

Produced by the Experiment Runner (Phase 2). Deliberately dumb: it records WHAT happened,
not whether it's good. Judgement lives in Scorecard + Verdict.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExperimentResult(BaseModel):
    id: str | None = None  # assigned by alpha memory as E-#### if absent
    spec_id: str
    dataset: str  # e.g. "US30 5m 2022-2025"
    period_start: str  # ISO date
    period_end: str
    trades_n: int = 0
    raw_metrics: dict = Field(default_factory=dict)  # pnl, sharpe, win_rate, max_dd, expectancy
    equity_curve: list[float] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
