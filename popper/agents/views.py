"""Scoped, role-based memory views (spec/02 — THE load-bearing rule).

Generation agents (Hypothesis, Gatekeeper, Wildcard, Synthesis) are
STRUCTURALLY performance-blind: the view they hold exposes ONLY the map —
claims, mechanisms, tags, embeddings, novelty, coverage/whitespace. It has no
method that returns a scorecard, verdict, or experiment metric, and even the
hypothesis projection it hands back omits status + behaviour_signature (both
leak post-test performance). So a generator *cannot* copy winners and tunnel.
Enforced by the method surface, not by trust — agents receive the view, never
the raw store.

Workers/Director (Phase 7) get FullView (everything). Only the hook exists now.
"""

from dataclasses import dataclass

from popper.artifacts import Hypothesis
from popper.memory.map import EDGE_TYPES, edge_type

BLIND_ROLES = {"hypothesis", "gatekeeper", "wildcard", "synthesis"}
FULL_ROLES = {"worker", "director"}


@dataclass(frozen=True)
class MapEntry:
    """Performance-blind projection of a Hypothesis. Deliberately has NO status
    and NO behaviour_signature field — there is nothing here to leak."""

    id: str | None
    claim: str
    mechanism: str
    required_features: tuple[str, ...]
    tags: dict
    novelty_score: float


def _entry(h: Hypothesis) -> MapEntry:
    return MapEntry(h.id, h.claim, h.mechanism, tuple(h.required_features),
                    dict(h.tags), h.novelty_score)


class BlindView:
    """Map-only. No results-returning method exists (structural blindness)."""

    def __init__(self, mem):
        self._mem = mem  # private: the agent is handed the view, never the store

    def hypotheses(self) -> list[MapEntry]:
        return [_entry(h) for h in self._mem.list_hypotheses()]

    def novelty(self, h) -> float:
        return self._mem.novelty(h)

    def is_stale(self, h) -> bool:
        return self._mem.is_stale(h)

    def assess(self, h) -> dict:
        """One embedding + one complete-map scan for gatekeeper decisions."""
        return self._mem.assess(h)

    def map_context(self, limit: int = 12) -> dict:
        """Prompt-ready blind context from one backend read, bounded for tokens."""
        hypotheses = self._mem.list_hypotheses()
        counts = {edge: 0 for edge in EDGE_TYPES}
        for hypothesis in hypotheses:
            etype = edge_type(hypothesis)
            counts[etype] = counts.get(etype, 0) + 1
        return {
            "claims": [h.claim for h in hypotheses[:limit]],
            "coverage": counts,
            "whitespace": [edge for edge in EDGE_TYPES if counts.get(edge, 0) == 0],
        }

    def coverage(self) -> dict:
        """edge_type -> count over the whole map (the exploration axis)."""
        counts = {e: 0 for e in EDGE_TYPES}
        for h in self._mem.list_hypotheses():
            et = edge_type(h)
            counts[et] = counts.get(et, 0) + 1
        return counts

    def whitespace(self) -> list[str]:
        """Edge types with zero coverage — where the Hypothesis agent should aim."""
        cov = self.coverage()
        return [e for e in EDGE_TYPES if cov.get(e, 0) == 0]


class FullView:
    """Everything — for Workers/Director (Phase 7). Delegates to the store."""

    def __init__(self, mem):
        self.mem = mem

    def __getattr__(self, name):
        return getattr(self.mem, name)


def view(role: str, mem):
    """Grant `role` its scoped view over `mem`. Blind roles get map-only."""
    if role in BLIND_ROLES:
        return BlindView(mem)
    if role in FULL_ROLES:
        return FullView(mem)
    raise ValueError(f"unknown agent role: {role!r}")
