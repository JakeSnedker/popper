"""The alpha-memory interface. Subsystems depend on this, never on a concrete store.

Novelty / staleness / enrichment are CONCRETE here (brute-force cosine over the
embeddings returned by list_hypotheses) so both SQLite and Supabase get them for
free — the map is small enough that O(n) scan is fine (spec/04: no pgvector yet).
"""

from abc import ABC, abstractmethod

from popper.artifacts import (
    ExperimentResult,
    Hypothesis,
    HypothesisStatus,
    Scorecard,
    StrategySpec,
    Verdict,
)
from popper.memory.embed import cosine, get_embedder
from popper.memory.map import STALE, categorize


def embed_text(h: Hypothesis) -> str:
    """Stable string rep the embedder sees: claim + sorted feature names."""
    return f"{h.claim.strip()} || features: {' '.join(sorted(h.required_features))}".strip()


class AlphaMemory(ABC):
    """Append-mostly store of the lab's entire reasoning history + the alpha map."""

    _embedder = None

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

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

    @abstractmethod
    def set_behaviour(self, hypothesis_id: str, signature: dict) -> None:
        """Attach the post-test behaviour_signature (surprise-relocation input)."""

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
    def validated_equity_curves(self) -> list[list[float]]:
        """Equity curves of SURVIVED/PROMOTED alphas — the correlation pool."""

    # --- feature-gap ledger (optional backend support) ---
    def add_feature_gap(self, name: str, kind: str, source: str, detail: str,
                        example: str | None = None) -> dict:
        return {"name": name, "kind": kind, "source": source, "detail": detail,
                "example": example, "count": 0, "status": "unsupported"}

    def list_feature_gaps(self, status: str | None = None, limit: int = 50) -> list[dict]:
        return []

    # --- research trace ledger (optional backend support) ---
    def add_research_event(self, event: dict) -> dict:
        return event

    def list_research_events(self, run_id: str | None = None, limit: int = 100) -> list[dict]:
        return []

    # --- the alpha map (concrete: shared by every backend) ---
    def _enrich(self, h: Hypothesis) -> Hypothesis:
        """Embed + tag + score novelty on insert. Preserves caller-set values."""
        updates: dict = {}
        if not h.embedding:
            updates["embedding"] = self.embedder.embed(embed_text(h))
        emb = updates.get("embedding", h.embedding)
        if not h.tags:
            updates["tags"] = categorize(h)
        if not h.novelty_score:
            _, sim = self._nearest_embedding(emb)
            updates["novelty_score"] = round(1.0 if sim < 0 else max(0.0, 1.0 - sim), 4)
        return h.model_copy(update=updates) if updates else h

    def _nearest_embedding(self, emb: list[float], exclude: str | None = None) -> tuple[str | None, float]:
        best_id, best_sim = None, -1.0
        for other in self.list_hypotheses():
            if other.id == exclude or not other.embedding:
                continue
            sim = cosine(emb, other.embedding)
            if sim > best_sim:
                best_id, best_sim = other.id, sim
        return best_id, best_sim

    def nearest(self, h: Hypothesis) -> tuple[str | None, float]:
        """(id, cosine similarity) of the closest prior hypothesis."""
        emb = h.embedding or self.embedder.embed(embed_text(h))
        return self._nearest_embedding(emb, exclude=h.id)

    def novelty(self, h: Hypothesis) -> float:
        """0..1 distance to the nearest prior alpha (1.0 = no neighbour / brand new)."""
        _, sim = self.nearest(h)
        return 1.0 if sim < 0 else max(0.0, 1.0 - sim)

    def is_stale(self, h: Hypothesis, threshold: float = STALE) -> bool:
        """True if a near-duplicate already exists (nearest cosine >= threshold)."""
        _, sim = self.nearest(h)
        return sim >= threshold
