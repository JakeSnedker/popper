"""Backend-neutral alpha memory and novelty assessment."""

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


class EmbeddingSpaceMismatch(RuntimeError):
    """Existing and configured embeddings cannot be compared honestly."""


def embed_text(h: Hypothesis) -> str:
    return f"{h.claim.strip()} || features: {' '.join(sorted(h.required_features))}".strip()


class AlphaMemory(ABC):
    """Append-mostly record of the lab plus a performance-blind alpha map."""

    _embedder = None

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

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
    def set_behaviour(self, hypothesis_id: str, signature: dict) -> None: ...

    @abstractmethod
    def set_embedding(self, hypothesis_id: str, embedding: list[float], tags: dict) -> None:
        """Maintenance hook used by the explicit re-embedding command."""

    @abstractmethod
    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None: ...

    @abstractmethod
    def list_hypotheses(self, status: HypothesisStatus | None = None) -> list[Hypothesis]: ...

    @abstractmethod
    def record(self, hypothesis_id: str) -> dict: ...

    def analysis_records(self, hypothesis_ids: list[str]) -> dict[str, dict]:
        """Bulk read used by aggregate reporting; network stores should override."""
        return {hypothesis_id: self.record(hypothesis_id) for hypothesis_id in hypothesis_ids}

    @abstractmethod
    def stats(self) -> dict: ...

    @abstractmethod
    def validated_equity_curves(self) -> list[list[float]]: ...

    def add_feature_gap(self, name: str, kind: str, source: str, detail: str,
                        example: str | None = None) -> dict:
        return {"name": name, "kind": kind, "source": source, "detail": detail,
                "example": example, "count": 0, "status": "unsupported"}

    def list_feature_gaps(self, status: str | None = None, limit: int = 50) -> list[dict]:
        return []

    def add_research_event(self, event: dict) -> dict:
        return event

    def list_research_events(self, run_id: str | None = None, limit: int = 100) -> list[dict]:
        return []

    def latest_verdict_types(self) -> dict[str, str]:
        """Latest verdict per hypothesis; network stores should override with a bulk query."""
        latest = {}
        for hypothesis in self.list_hypotheses():
            verdicts = self.record(hypothesis.id).get("verdicts", [])
            if verdicts:
                latest[hypothesis.id] = max(verdicts, key=lambda row: row["created_at"])["type"]
        return latest

    def recent_verdicts(self, limit: int = 20) -> list[dict]:
        """Dashboard verdict feed; network stores should override with bulk queries."""
        rows = []
        for hypothesis in self.list_hypotheses():
            record = self.record(hypothesis.id)
            experiments = {row["id"]: row for row in record.get("experiments", [])}
            scorecards = {row["experiment_id"]: row for row in record.get("scorecards", [])}
            for verdict in record.get("verdicts", []):
                experiment = experiments.get(verdict["experiment_id"], {})
                scorecard = scorecards.get(verdict["experiment_id"], {})
                rows.append({
                    "hypothesis_id": hypothesis.id,
                    "claim": hypothesis.claim,
                    "type": verdict["type"],
                    "reasons": verdict["reasons"],
                    "created_at": verdict["created_at"],
                    "profit_factor": experiment.get("raw_metrics", {}).get("profit_factor"),
                    "expectancy_net": experiment.get("raw_metrics", {}).get("expectancy_net"),
                    "deflated_sharpe": scorecard.get("deflated_sharpe"),
                })
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows[:limit]

    def prepare(self, h: Hypothesis) -> Hypothesis:
        """Embed and tag once, before novelty and evaluation reuse the candidate."""
        updates: dict = {}
        if not h.embedding:
            updates["embedding"] = self.embedder.embed(embed_text(h))
        if not h.tags:
            tags = categorize(h)
            tags["embedding_backend"] = getattr(self.embedder, "name", "unknown")
            updates["tags"] = tags
        return h.model_copy(update=updates) if updates else h

    def _nearest_embedding(self, emb: list[float], exclude: str | None = None,
                           hypotheses: list[Hypothesis] | None = None) -> tuple[str | None, float]:
        best_id, best_sim = None, -1.0
        mismatched = set()
        for other in hypotheses if hypotheses is not None else self.list_hypotheses():
            if other.id == exclude or not other.embedding:
                continue
            if len(emb) != len(other.embedding):
                mismatched.add(len(other.embedding))
                continue
            sim = cosine(emb, other.embedding)
            if sim > best_sim:
                best_id, best_sim = other.id, sim
        if mismatched:
            raise EmbeddingSpaceMismatch(
                f"configured embedding dimension {len(emb)} conflicts with stored dimensions "
                f"{sorted(mismatched)}; run scripts/reembed_hypotheses.py before research"
            )
        return best_id, best_sim

    def assess(self, h: Hypothesis) -> dict:
        """Prepare + nearest + novelty in one embedding and one map scan."""
        prepared = self.prepare(h)
        nearest_id, similarity = self._nearest_embedding(
            prepared.embedding, exclude=prepared.id,
        )
        novelty = 1.0 if similarity < 0 else max(0.0, 1.0 - similarity)
        return {
            "hypothesis": prepared,
            "nearest_id": nearest_id,
            "similarity": similarity,
            "novelty": novelty,
            "stale": similarity >= STALE,
        }

    def _enrich(self, h: Hypothesis) -> Hypothesis:
        prepared = self.prepare(h)
        if prepared.novelty_score:
            return prepared
        _, similarity = self._nearest_embedding(prepared.embedding, exclude=prepared.id)
        novelty = 1.0 if similarity < 0 else max(0.0, 1.0 - similarity)
        return prepared.model_copy(update={"novelty_score": round(novelty, 4)})

    def nearest(self, h: Hypothesis) -> tuple[str | None, float]:
        prepared = self.prepare(h)
        return self._nearest_embedding(prepared.embedding, exclude=prepared.id)

    def novelty(self, h: Hypothesis) -> float:
        return self.assess(h)["novelty"]

    def is_stale(self, h: Hypothesis, threshold: float = STALE) -> bool:
        return self.nearest(h)[1] >= threshold
