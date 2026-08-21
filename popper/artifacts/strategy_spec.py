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

from pydantic import BaseModel, Field, field_validator

# a literal value or a "p[name]" reference into param_space
Param = float | str


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Alpha(BaseModel):
    """The signal. `signal` fires long (+1); optional `signal_short` fires -1."""

    features: list[str] = Field(default_factory=list)  # base-timeframe features
    higher_timeframe_features: dict[str, list[str]] = Field(default_factory=dict)
    signal: str  # boolean expression, e.g. "close > ema_50 AND atr_14 > p[atr_min]"
    signal_short: str | None = None
    # Direction is DECLARED, never inferred. A missing signal_short used to mean
    # "long only" silently, which is how a whole corpus drifts one-directional.
    direction: Literal["both", "long_only", "short_only"] = "both"
    direction_rationale: str | None = None  # required when direction != "both"


class EntryRule(BaseModel):
    """How the position is opened once the signal fires.

    market -- fills at the next bar open, the v0.3 behaviour.
    limit  -- rests at a BETTER price than the signal close (buy below, sell
              above) and fills only if price comes back to it.
    stop   -- rests at a WORSE price (buy above, sell below): a breakout entry
              that fills only if price runs on.

    A resting order needs a price and a lifetime. Price is either `offset` ATR
    multiples away from the signal bar's close, or an `anchor` level feature,
    mirroring how stops and targets already work. `expiry_bars` cancels an
    unfilled order -- without it an order rests forever and the fill is no
    longer the one the hypothesis described.
    """

    type: Literal["market", "limit", "stop"] = "market"
    offset: Param = 0.5             # ATR multiples from the signal close
    anchor: str | None = None       # or rest at a LEVEL_FEATURE instead
    expiry_bars: int = 5            # cancel an unfilled order after N bars


class StopRule(BaseModel):
    """v0.3 (docs/DSL_v0.3_EXIT_SPEC.md §8): `atr` distance (v0.2 behaviour) or a
    structural `level` — anchor names a LOSS-SIDE level-feature, ± offset points."""

    type: Literal["atr", "level"] = "atr"
    mult: Param = 2.0  # type=atr
    anchor: str | None = None  # type=level -> a loss-side LEVEL_FEATURE
    offset: Param = 0.0  # ± points/spread applied to the anchor


class TargetRule(BaseModel):
    """v0.3: `ratio` (R-multiple — RR is the swept family) or a structural `level`
    (profit-side anchor — RR is emergent, recorded per trade, not swept)."""

    type: Literal["ratio", "level"] = "ratio"
    ratio: Param = 2.0  # type=ratio -> the RR sweep family
    anchor: str | None = None  # type=level -> a profit-side LEVEL_FEATURE
    offset: Param = 0.0

    @field_validator("type", mode="before")
    @classmethod
    def _v02_rr_alias(cls, v):
        return "ratio" if v == "rr" else v  # v0.2 specs stay valid


class TrailRule(BaseModel):
    # DECISION: only none|atr implemented now; psar/chandelier when an alpha needs them
    type: Literal["none", "atr"] = "none"
    mult: Param = 2.0


class BreakEvenRule(BaseModel):
    type: Literal["none", "after_r"] = "none"
    trigger_r: Param = 1.0  # move stop to entry after this many R in favour


class TimeStop(BaseModel):
    """Close on elapsed bars, regardless of price.

    Without this every trade must resolve at a price level, which silently
    excludes any edge that simply decays -- the strategy is right for an hour
    and then is not. Those hypotheses could not previously be written down.
    """

    type: Literal["none", "bars"] = "none"
    max_bars: Param = 20.0


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
    time_stop: TimeStop = Field(default_factory=TimeStop)
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
    # 0.4 added alpha.direction, limit/stop entries and wrapper.time_stop. The
    # emitted Pine and MQL5 headers carry this, so a stale value mislabels a
    # deployed artifact as something the tooling never produced.
    dsl_version: str = "0.4"
    name: str
    alpha: Alpha
    wrapper: Wrapper = Field(default_factory=Wrapper)
    context: Context = Field(default_factory=Context)
    param_space: dict[str, list] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
