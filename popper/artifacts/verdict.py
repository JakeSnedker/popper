"""Verdict — the falsification gauntlet's ruling on a hypothesis.

The moat. A hypothesis must SURVIVE attempts to disprove it. KILL verdicts carry reasons
so the Hypothesis Engine can learn from failure (closing the self-improving loop).
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from popper.artifacts.enums import VerdictType


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Verdict(BaseModel):
    id: str | None = None  # assigned by alpha memory as V-#### if absent
    hypothesis_id: str
    experiment_id: str
    type: VerdictType
    reasons: list[str] = Field(default_factory=list)  # e.g. ["pbo>0.5", "regime_fail"]
    created_at: datetime = Field(default_factory=_now)
