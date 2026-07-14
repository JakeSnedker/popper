"""Cluster convergence / stop logic (docs/SELF_LEARNING_ROADMAP.md B4).

Decides each edge-cluster's lifecycle so the Director stops spending exploit
budget where it no longer pays: promote (keeper found), shelve (survivor
exists but the optimizer converged with no further gain), dead (no survivor,
exhausted), active (still worth refining). Pure functions over the Director's
category dicts ({"kills", "survivors", "dead", ...}) — no I/O, no LLM.
"""

_RANK = {"kill": 0, "survive": 1, "promote": 2}


def update_cluster(cluster: dict, verdict: str, done_entry: dict) -> dict:
    """Fold one exploit outcome (worker verdict + optimizer done-entry) into
    the cluster's bookkeeping. Mutates and returns `cluster`."""
    cluster["attempts"] = cluster.get("attempts", 0) + 1
    if done_entry.get("improved"):
        cluster["converged_streak"] = 0
    else:
        cluster["converged_streak"] = cluster.get("converged_streak", 0) + 1
    # ponytail: best_verdict only ever ratchets upward; no demotion path needed
    if _RANK.get(verdict, 0) > _RANK.get(cluster.get("best_verdict"), -1):
        cluster["best_verdict"] = verdict
    return cluster


def cluster_status(cluster: dict, k_dead: int = 2, shelve_after: int = 2) -> str:
    """Lifecycle of one cluster: 'promote' | 'shelve' | 'dead' | 'active'.
    `k_dead` mirrors the Director's K_DEAD prune rule; `shelve_after` is how
    many consecutive non-improving optimizer runs count as a local optimum."""
    survivors = cluster.get("survivors") or []
    streak = cluster.get("converged_streak", 0)
    if cluster.get("best_verdict") == "promote":
        return "promote"
    if not survivors and (cluster.get("kills", 0) >= k_dead or streak >= shelve_after):
        return "dead"
    if survivors and streak >= shelve_after:
        return "shelve"
    return "active"
