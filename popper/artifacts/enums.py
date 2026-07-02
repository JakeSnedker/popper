"""Controlled vocabularies shared across artifacts."""

from enum import Enum


class HypothesisStatus(str, Enum):
    """Lifecycle of a hypothesis as it moves through the research loop."""

    PROPOSED = "proposed"
    TESTING = "testing"
    KILLED = "killed"
    SURVIVED = "survived"
    PROMOTED = "promoted"


class VerdictType(str, Enum):
    """Outcome of the falsification gauntlet."""

    SURVIVE = "survive"
    KILL = "kill"
    PROMOTE = "promote"


class Regime(str, Enum):
    """Market regime a hypothesis expects to exploit."""

    TRENDING = "trending"
    RANGING = "ranging"
    HIGH_VOL = "high_vol"
    LOW_VOL = "low_vol"
    UNKNOWN = "unknown"
