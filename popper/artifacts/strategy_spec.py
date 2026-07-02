"""StrategySpec — an instance of the Strategy DSL.

The safe, structured representation of a strategy that compiles to backtest / sim / live.
Phase 0 captures the shape; Phase 1 builds the grammar, validator, and compiler.
The block structure intentionally mirrors JJC BOT V13's parameter model so it's familiar.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StrategySpec(BaseModel):
    id: str | None = None  # assigned by alpha memory as S-#### if absent
    hypothesis_id: str
    dsl_version: str = "0.1"
    name: str
    universe: list[str]  # e.g. ["US30"]
    timeframe: str  # e.g. "5m"
    features: list[str] = Field(default_factory=list)  # named feature-library entries
    signal: dict = Field(default_factory=dict)  # entry/exit conditions
    entry: dict = Field(default_factory=dict)  # order type / timing
    exit: dict = Field(default_factory=dict)  # stop / target rules
    sizing: dict = Field(default_factory=dict)  # risk model
    constraints: dict = Field(default_factory=dict)  # max trades, no-trade windows...
    param_space: dict = Field(default_factory=dict)  # grid the runner/falsifier probes
    created_at: datetime = Field(default_factory=_now)
