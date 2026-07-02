"""Hypothesis — a falsifiable claim about a market mechanism.

Produced by the Hypothesis Engine (Phase 6). Until then, hand-authored.
A good hypothesis names a *mechanism* (why the edge should exist), not just a pattern.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from popper.artifacts.enums import HypothesisStatus, Regime


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Hypothesis(BaseModel):
    id: str | None = None  # assigned by alpha memory as H-#### if absent
    claim: str  # the falsifiable statement
    mechanism: str  # WHY this edge should exist (liquidity, order-flow, behavioral...)
    rationale: str = ""  # supporting reasoning / references
    required_features: list[str] = Field(default_factory=list)
    expected_regime: Regime = Regime.UNKNOWN
    novelty_score: float = 0.0  # 0..1, distance from nearest prior hypothesis
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    parent_id: str | None = None  # lineage: which prior verdict spawned this
    # --- alpha-map fields (Phase 4), populated by the memory on insert/post-test ---
    embedding: list[float] = Field(default_factory=list)  # claim+features vector
    tags: dict = Field(default_factory=dict)  # faceted: edge_type, context
    behaviour_signature: dict = Field(default_factory=dict)  # post-test behaviour
    created_at: datetime = Field(default_factory=_now)
