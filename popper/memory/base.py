"""The alpha-memory interface. Subsystems depend on this, never on a concrete store."""

from abc import ABC, abstractmethod

from popper.artifacts import (
    ExperimentResult,
    Hypothesis,
    HypothesisStatus,
    Scorecard,
    StrategySpec,
    Verdict,
)


class AlphaMemory(ABC):
    """Append-mostly store of the lab's entire reasoning history."""

    # --- writes (each assigns an id if absent and returns the stored artifact) ---
    @abstractmethod
    def add_hypothesis(self, h: Hypothesis) -> Hypothesis: ...

    @abstractmethod
    def add_spec(self, s: StrategySpec) -> StrategySpec: ...

    @abstractmethod
    def add_experiment(self, e: ExperimentResult) -> ExperimentResult: ...

    @abstractmethod
    def add_scorecard(self, sc: Scorecard) -> Scorecard: ...

    @abstractmethod
    def add_verdict(self, v: Verdict) -> Verdict: ...

    @abstractmethod
    def set_status(self, hypothesis_id: str, status: HypothesisStatus) -> None: ...

    # --- reads ---
    @abstractmethod
    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None: ...

    @abstractmethod
    def list_hypotheses(self, status: HypothesisStatus | None = None) -> list[Hypothesis]: ...

    @abstractmethod
    def record(self, hypothesis_id: str) -> dict:
        """Full artifact trail for one hypothesis: specs, experiments, scorecards, verdicts."""

    @abstractmethod
    def stats(self) -> dict: ...

    @abstractmethod
    def is_stale(self, h: Hypothesis) -> bool:
        """True if this idea has effectively been explored before (re-exploration guard)."""
