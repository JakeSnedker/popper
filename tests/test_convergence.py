"""B4 acceptance (docs/SELF_LEARNING_ROADMAP.md): cluster lifecycle decisions
so the Director stops spending exploit budget on converged/dead clusters.
Pure dicts in, string out — fully offline."""

from popper.orchestrator.convergence import cluster_status, update_cluster


def _cluster(**over):
    c = {"kills": 0, "survivors": [], "dead": False}
    c.update(over)
    return c


# --- cluster_status branches ---

def test_promote_when_best_verdict_promote():
    c = _cluster(survivors=["spec"], best_verdict="promote", converged_streak=5)
    assert cluster_status(c) == "promote"  # promote wins even after convergence


def test_dead_when_no_survivors_and_kills_reach_k_dead():
    assert cluster_status(_cluster(kills=2)) == "dead"
    assert cluster_status(_cluster(kills=1)) == "active"  # below threshold


def test_dead_when_no_survivors_and_converged_streak_exhausted():
    assert cluster_status(_cluster(converged_streak=2)) == "dead"


def test_shelve_when_survivor_but_converged():
    c = _cluster(survivors=["spec"], best_verdict="survive", converged_streak=2)
    assert cluster_status(c) == "shelve"


def test_active_when_survivor_still_improving():
    c = _cluster(survivors=["spec"], best_verdict="survive", converged_streak=0)
    assert cluster_status(c) == "active"


def test_thresholds_are_params():
    assert cluster_status(_cluster(kills=2), k_dead=3) == "active"
    c = _cluster(survivors=["spec"], converged_streak=2)
    assert cluster_status(c, shelve_after=3) == "active"


# --- update_cluster bookkeeping ---

def test_update_cluster_tracks_attempts_and_streak():
    c = _cluster(survivors=["spec"])
    done_flat = {"move": "done", "converged": True, "improved": False, "evals": 4}
    done_up = {"move": "done", "converged": True, "improved": True, "evals": 6}

    c = update_cluster(c, "survive", done_flat)
    assert c["attempts"] == 1 and c["converged_streak"] == 1
    c = update_cluster(c, "survive", done_flat)
    assert c["attempts"] == 2 and c["converged_streak"] == 2
    c = update_cluster(c, "survive", done_up)   # improvement resets the streak
    assert c["attempts"] == 3 and c["converged_streak"] == 0
    # existing Director keys survive untouched
    assert c["survivors"] == ["spec"] and c["kills"] == 0 and c["dead"] is False


def test_update_cluster_ratchets_best_verdict():
    c = _cluster()
    done = {"move": "done", "improved": True, "evals": 1}
    c = update_cluster(c, "survive", done)
    assert c["best_verdict"] == "survive"
    c = update_cluster(c, "promote", done)
    assert c["best_verdict"] == "promote"
    c = update_cluster(c, "kill", done)         # never demotes
    assert c["best_verdict"] == "promote"
    assert cluster_status(c) == "promote"
