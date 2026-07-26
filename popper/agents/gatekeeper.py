"""Deterministic novelty gate and provisional category routing."""

from popper.memory.map import categorize


def screen(hypothesis, view) -> dict:
    """Assess once and return the prepared hypothesis for downstream reuse."""
    assessment = view.assess(hypothesis)
    prepared = assessment["hypothesis"].model_copy(
        update={"novelty_score": round(assessment["novelty"], 4)}
    )
    if assessment["stale"]:
        return {
            "accepted": False,
            "reason": "near-duplicate of an existing hypothesis",
            "novelty": assessment["novelty"],
            "nearest_id": assessment["nearest_id"],
            "category": None,
            "hypothesis": prepared,
        }
    return {
        "accepted": True,
        "reason": "novel",
        "novelty": assessment["novelty"],
        "nearest_id": assessment["nearest_id"],
        "category": categorize(prepared),
        "hypothesis": prepared,
    }
