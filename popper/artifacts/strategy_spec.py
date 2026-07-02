"""StrategySpec — an instance of the Strategy DSL.

Strategy = Alpha x Wrapper x Context (spec/01-dsl.md):
- Alpha   = the edge/signal ONLY (emits +1/-1/0). No execution, no sizing.
- Wrapper = execution: entry/stop/target/trail/break_even/sizing/constraints.
- Context = universe/timeframe/cost_model — assumed and tagged, not searched.
- param_space = the grid the runner/falsifier probes (wrapper + alpha params).

Numeric wrapper fields accept either a literal number or a param reference
string like "p[sl_atr]" resolved at compile time from param_space.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# a literal value or a "p[name]" reference into param_space
Param = float | str


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Alpha(BaseModel):
    """The signal. `signal` fires long (+1); optional `signal_short` fires -1."""

    features: list[str] = Field(default_factory=list)  # feature-library names only
    signal: str  # boolean expression, e.g. "close > ema_50 AND atr_14 > p[atr_min]"
    signal_short: str | None = None


class EntryRule(BaseModel):
    type: Literal["market"] = "market"  # fills at next bar open


class StopRule(BaseModel):
    type: Literal["atr"] = "atr"
    mult: Param = 2.0


class TargetRule(BaseModel):
    type: Literal["rr"] = "rr"
    ratio: Param = 2.0


class TrailRule(BaseModel):
    # DECISION: only none|atr implemented now; psar/chandelier when an alpha needs them
    type: Literal["none", "atr"] = "none"
    mult: Param = 2.0


class BreakEvenRule(BaseModel):
    type: Literal["none", "after_r"] = "none"
    trigger_r: Param = 1.0  # move stop to entry after this many R in favour


class Sizing(BaseModel):
    risk_pct: float = 0.5  # % of equity risked per trade


class Constraints(BaseModel):
    max_trades_per_day: int | None = None
    no_trade: list[str] = Field(default_factory=list)


class Wrapper(BaseModel):
    """Execution — turns an alpha into a strategy. Cannot create edge."""

    entry: EntryRule = Field(default_factory=EntryRule)
    stop: StopRule = Field(default_factory=StopRule)
    target: TargetRule = Field(default_factory=TargetRule)
    trail: TrailRule = Field(default_factory=TrailRule)
    break_even: BreakEvenRule = Field(default_factory=BreakEvenRule)
    sizing: Sizing = Field(default_factory=Sizing)
    constraints: Constraints = Field(default_factory=Constraints)


# The canonical baseline wrapper (spec/03 gate 0): SL = 2xATR, TP = 2R, held
# fixed so the alpha's raw edge is measurable and comparable across alphas.
BASELINE_WRAPPER = Wrapper()


class Context(BaseModel):
    universe: list[str] = Field(default_factory=lambda: ["US30"])
    timeframe: str = "5m"
    cost_model: Literal["systematic", "hft"] = "systematic"


class StrategySpec(BaseModel):
    id: str | None = None  # assigned by alpha memory as S-#### if absent
    hypothesis_id: str
    dsl_version: str = "0.2"
    name: str
    alpha: Alpha
    wrapper: Wrapper = Field(default_factory=Wrapper)
    context: Context = Field(default_factory=Context)
    param_space: dict[str, list] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
