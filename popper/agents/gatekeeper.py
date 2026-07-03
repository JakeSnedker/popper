"""Gatekeeper agent (spec/02): novelty gate + provisional category routing.

Deterministic map math — reuses the alpha memory's novelty/staleness (via the
blind view) and the map's categorize(). Rejects near-duplicates before they
cost a gauntlet run.

ponytail: v0 is a hard novelty threshold; the semantic LLM dedup (Haiku, spec
§12) slots in here when token-overlap novelty starts missing paraphrases.
"""

from popper.memory.map import categorize


def screen(hypothesis, view) -> dict:
    """{accepted, reason, novelty, category}. category is None when rejected."""
    novelty = view.novelty(hypothesis)
    if view.is_stale(hypothesis):
        return {"accepted": False, "reason": "near-duplicate of an existing hypothesis",
                "novelty": novelty, "category": None}
    return {"accepted": True, "reason": "novel", "novelty": novelty,
            "category": categorize(hypothesis)}
