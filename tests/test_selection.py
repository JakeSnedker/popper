"""B2 acceptance (docs/SELF_LEARNING_ROADMAP.md): near-miss cluster selection.
classify() tiers candidates (survivor / cost_killed / near_miss / dead) and
rank_targets() feeds the exploit loop cost-killed edges ABOVE marginal
near-misses, drops clear failures, and honours `limit`. Fully offline."""

from popper.artifacts import Scorecard
from popper.falsify import GauntletConfig
from popper.orchestrator.selection import classify, rank_targets

CFG = GauntletConfig()
T = CFG.thresholds  # max_pbo=0.50, min_profitable_fraction=0.50


def _sc(pbo, stability):
    return Scorecard(experiment_id="E-1", pbo=pbo, parameter_stability=stability)


def _cand(name, metrics, verdict, scorecard=None):
    return {"spec": name, "metrics": metrics, "verdict": verdict,
            "reasons": [], "scorecard": scorecard}


SURVIVOR = _cand("surv", {"expectancy": 0.20, "expectancy_net": 0.15}, "survive",
                 _sc(0.10, 0.90))
COST_KILLED = _cand("cost", {"expectancy": 0.12, "expectancy_net": -0.02}, "kill",
                    _sc(0.30, 0.70))
NEAR_MISS = _cand("near", {"expectancy": -0.01, "expectancy_net": -0.05}, "kill",
                  _sc(T.max_pbo + 0.05, T.min_profitable_fraction - 0.05))  # inside band=0.1
DEAD = _cand("dead", {"expectancy": -0.30, "expectancy_net": -0.40}, "kill",
             _sc(0.95, 0.10))  # pbo far over gate, no gross edge


def test_classify_tiers():
    assert classify(SURVIVOR["metrics"], "survive", scorecard=SURVIVOR["scorecard"]) == "survivor"
    assert classify(SURVIVOR["metrics"], "promote") == "survivor"
    assert classify(COST_KILLED["metrics"], "kill", scorecard=COST_KILLED["scorecard"]) == "cost_killed"
    assert classify(NEAR_MISS["metrics"], "kill", scorecard=NEAR_MISS["scorecard"], cfg=CFG) == "near_miss"
    assert classify(DEAD["metrics"], "kill", scorecard=DEAD["scorecard"], cfg=CFG) == "dead"
    # no scorecard + no gross edge = nothing to judge nearness by -> dead
    assert classify({"expectancy": -0.1}, "kill") == "dead"


def test_rank_orders_tiers_and_drops_dead():
    ranked = rank_targets([DEAD, NEAR_MISS, COST_KILLED, SURVIVOR], cfg=CFG)
    assert [c["kind"] for c in ranked] == ["survivor", "cost_killed", "near_miss"]
    assert ranked[0]["spec"] == "surv"          # survivor first
    assert ranked[1]["spec"] == "cost"          # cost-killed above the marginal near-miss
    assert all(c["spec"] != "dead" for c in ranked)


def test_rank_sorts_within_tier_by_net_expectancy():
    worse = _cand("cost_worse", {"expectancy": 0.05, "expectancy_net": -0.10}, "kill")
    ranked = rank_targets([worse, COST_KILLED], cfg=CFG)
    assert [c["spec"] for c in ranked] == ["cost", "cost_worse"]


def test_limit_truncates():
    ranked = rank_targets([SURVIVOR, COST_KILLED, NEAR_MISS], cfg=CFG, limit=2)
    assert len(ranked) == 2
    assert [c["kind"] for c in ranked] == ["survivor", "cost_killed"]
