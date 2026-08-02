"""Rescue backlog: honest tiering, and exploit targets that outlive one run."""

from types import SimpleNamespace

from popper.artifacts import StrategySpec
from popper.orchestrator.rescue import load_backlog, scan_backlog, summarise
from popper.orchestrator.selection import classify, gate_ran, rank_targets


def _sc(pbo=0.4, stability=0.9):
    return SimpleNamespace(pbo=pbo, parameter_stability=stability)


def _measured(passed=False):
    """Diagnostics for a spec that ran the whole gauntlet."""
    return {
        "gate_signal_edge": {"passed": True},
        "gate1_walkforward": {"passed": passed},
        "gate2_robustness": {"passed": passed},
        "gate4_multiple_testing": {"passed": passed},
    }


def _signal_edge_kill():
    """Diagnostics for a spec killed at the first gate — nothing else ever ran."""
    return {"gate_signal_edge": {"passed": False, "reason": "no coherent horizon family"}}


# --- the sentinel bug: unmeasured defaults must not read as measurements ---

def test_signal_edge_kill_is_dead_not_a_rescue_target():
    # Gross positive, net negative — looks exactly like a cost-killed edge, but
    # the strategy never showed any forward return at all.
    kind = classify({"expectancy": 0.08, "expectancy_net": -0.11}, "kill",
                    scorecard=_sc(pbo=1.0, stability=0.0),
                    diagnostics=_signal_edge_kill())
    assert kind == "dead"


def test_real_cost_killed_edge_is_still_rescued():
    kind = classify({"expectancy": 0.0587, "expectancy_net": -0.0331}, "kill",
                    scorecard=_sc(pbo=0.486, stability=0.947),
                    diagnostics=_measured())
    assert kind == "cost_killed"


def test_near_miss_requires_the_overfit_gates_to_have_run():
    # Sentinel scorecard from an early kill: pbo/stability were never computed.
    unmeasured = {"gate_signal_edge": {"passed": True},
                  "gate1_walkforward": {"passed": False}}
    assert classify({"expectancy": -0.01}, "kill", scorecard=_sc(pbo=0.4, stability=0.9),
                    diagnostics=unmeasured) == "unclassifiable"
    # Same numbers, but the gates actually ran.
    assert classify({"expectancy": -0.01}, "kill", scorecard=_sc(pbo=0.4, stability=0.9),
                    diagnostics=_measured()) == "near_miss"


def test_gate_ran_rejects_missing_and_skipped_gates():
    assert gate_ran({"gate2_robustness": {"passed": True}}, "gate2_robustness")
    assert not gate_ran({}, "gate2_robustness")
    assert not gate_ran({"gate_signal_edge": {"passed": None, "skipped": "no bars"}},
                        "gate_signal_edge")
    assert not gate_ran(None, "gate2_robustness")


def test_ranking_drops_sentinel_candidates_but_keeps_real_ones():
    spec_a = StrategySpec(id="S-1", hypothesis_id="H-1", name="real",
                          alpha={"features": ["ema_50"], "signal": "close > ema_50"})
    spec_b = StrategySpec(id="S-2", hypothesis_id="H-2", name="no-edge",
                          alpha={"features": ["ema_50"], "signal": "close > ema_50"})
    kept = rank_targets([
        {"spec": spec_b, "metrics": {"expectancy": 0.09, "expectancy_net": -0.1},
         "verdict": "kill", "scorecard": _sc(1.0, 0.0), "diagnostics": _signal_edge_kill()},
        {"spec": spec_a, "metrics": {"expectancy": 0.05, "expectancy_net": -0.03},
         "verdict": "kill", "scorecard": _sc(0.486, 0.947), "diagnostics": _measured()},
    ])
    assert [t["spec"].id for t in kept] == ["S-1"]
    assert kept[0]["kind"] == "cost_killed"


# --- the backlog itself ---

class _Memory:
    """Minimal stand-in exposing only what load_backlog reads."""

    def __init__(self, rows):
        self.rows = rows

    def list_hypotheses(self):
        return [SimpleNamespace(id=r["id"], status="killed", claim=r["claim"],
                                required_features=r.get("features", []))
                for r in self.rows]

    def analysis_records(self, ids):
        return {
            r["id"]: {
                "specs": [{"id": f"S-{r['id']}", "hypothesis_id": r["id"],
                           "created_at": "2026-01-01"}],
                "experiments": [{"id": f"E-{r['id']}", "spec_id": f"S-{r['id']}",
                                 "created_at": "2026-01-01", "raw_metrics": r["metrics"]}],
                "scorecards": [{"id": f"SC-{r['id']}",
                                "experiment_id": f"E-{r['id']}",
                                "created_at": "2026-01-01", "pbo": r["pbo"],
                                "parameter_stability": r["stability"],
                                "diagnostics": r["diagnostics"]}],
                "verdicts": [{"id": f"V-{r['id']}",
                              "experiment_id": f"E-{r['id']}",
                              "created_at": "2026-01-01", "type": "kill", "reasons": []}],
            }
            for r in self.rows if r["id"] in ids
        }

    def record(self, hypothesis_id):
        return {"specs": [{"id": f"S-{hypothesis_id}", "hypothesis_id": hypothesis_id,
                           "name": "x",
                           "alpha": {"features": ["ema_50"], "signal": "close > ema_50"}}]}

    def add_research_event(self, event):
        return event


def _rows():
    return [
        {"id": "H-real", "claim": "momentum breakout continues", "pbo": 0.486,
         "stability": 0.947, "metrics": {"expectancy": 0.0587, "expectancy_net": -0.0331},
         "diagnostics": _measured()},
        {"id": "H-noedge", "claim": "momentum breakout noise", "pbo": 1.0, "stability": 0.0,
         "metrics": {"expectancy": 0.08, "expectancy_net": -0.11},
         "diagnostics": _signal_edge_kill()},
    ]


def test_backlog_loads_prior_kills_and_excludes_the_edgeless():
    categories = load_backlog(_Memory(_rows()))
    targets = [t for c in categories.values() for t in c["near_misses"]]
    assert [t["rescued_from"] for t in targets] == ["H-real"]
    assert targets[0]["kind"] == "cost_killed"
    assert summarise(categories) == {"categories": 1, "targets": 1, "survivor": 0,
                                     "cost_killed": 1, "near_miss": 0}


def test_backlog_shape_matches_the_directors_own_categories():
    """Seeding must be a drop-in for what the Director builds during a run."""
    categories = load_backlog(_Memory(_rows()))
    for bucket in categories.values():
        assert set(bucket) == {"kills", "survivors", "dead", "near_misses"}
        assert bucket["survivors"] == [] and bucket["dead"] is False


def test_backlog_limit_caps_work_for_large_backlogs():
    rows = []
    for i in range(50):
        row = dict(_rows()[0])
        row["id"] = f"H-{i:03d}"
        row["metrics"] = {"expectancy": 0.01 * i, "expectancy_net": -0.01}
        rows.append(row)
    categories = load_backlog(_Memory(rows), limit=5)
    assert summarise(categories)["targets"] == 5


def test_empty_backlog_is_not_an_error():
    assert load_backlog(_Memory([])) == {}
    assert summarise({}) == {"categories": 0, "targets": 0, "survivor": 0,
                             "cost_killed": 0, "near_miss": 0}


def test_persisted_refinement_marker_exhausts_the_source_after_restart():
    rows = _rows()[:1]
    rows[0]["diagnostics"] = {
        **_measured(), "refined_from_spec_id": "S-H-real",
    }
    scan = scan_backlog(_Memory(rows))
    assert scan["entries"][0]["eligible"] is False
    assert scan["entries"][0]["excluded_reason"] == "already_refined"
    assert load_backlog(_Memory(rows)) == {}


def test_exploit_only_empty_backlog_never_initializes_an_llm(monkeypatch):
    import popper.agents
    from popper.orchestrator import research_budget, run_research

    def forbidden():
        raise AssertionError("exploit-only initialized an LLM")

    monkeypatch.setattr(popper.agents, "get_llm", forbidden)
    summary = run_research(
        mem=_Memory([]), llm=None, iterations=1,
        budget=research_budget("exploit"),
    )
    assert summary.worker_runs == 0
    assert summary.stopped_reason == "exploit backlog exhausted"


def test_auto_failover_runs_bounded_exploit_without_an_llm(monkeypatch):
    import time

    import scripts.run_hunt as hunt

    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            worker_runs=0, skipped=0, failures=[],
            stopped_reason="exploit backlog exhausted",
        )

    monkeypatch.setattr(hunt, "run_research", fake_run)
    assert hunt._auto_exploit(
        mem=object(), deadline=time.time() + 10,
        resume_at=time.monotonic() + 10,
    ) is False
    assert len(calls) == 1
    assert calls[0]["llm"] is None
    assert calls[0]["backlog_limit"] == 1
    assert calls[0]["exploit_max_evals"] == 1


def test_exploit_only_runs_a_historical_target_without_an_llm(monkeypatch):
    import popper.agents
    import popper.orchestrator.director as director
    from popper.orchestrator import research_budget, run_research

    def forbidden():
        raise AssertionError("exploit-only initialized an LLM")

    def worker(_mem, spec, *_args, **_kwargs):
        return "kill", {"improved": False}, spec

    monkeypatch.setattr(popper.agents, "get_llm", forbidden)
    monkeypatch.setattr(director, "_worker_refine", worker)
    summary = run_research(
        mem=_Memory(_rows()[:1]), llm=None, iterations=1,
        budget=research_budget("exploit"), backlog_limit=1,
    )
    assert summary.worker_runs == 1
    assert summary.proposed == 0
