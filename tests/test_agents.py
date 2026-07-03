"""Phase 6 acceptance (spec/06 row 6): the research agents produce specs that
pass validate_spec UNAIDED — Synthesis via a compiler-feedback repair loop.
Plus the load-bearing rule: generation views are STRUCTURALLY performance-blind.
All offline: StubLLM canned responses, temp DB + LocalEmbedder. No network."""

import json
import os
import tempfile

import pytest

from popper.agents import StubLLM, propose, screen, synthesize, view
from popper.agents.views import BlindView, MapEntry
from popper.artifacts import (
    ExperimentResult,
    Hypothesis,
    HypothesisStatus,
    Scorecard,
    StrategySpec,
    Verdict,
    VerdictType,
)
from popper.dsl import DSLValidationError, validate_spec
from popper.memory import LocalEmbedder
from popper.memory.sqlite_repo import SqliteMemory


def _mem() -> SqliteMemory:
    return SqliteMemory(os.path.join(tempfile.mkdtemp(), "t.db"), embedder=LocalEmbedder())


# --- Hypothesis agent: emits a valid Hypothesis aimed via the blind map ---

def test_hypothesis_agent_emits_hypothesis():
    mem = _mem()
    llm = StubLLM(json.dumps({
        "claim": "London-session breakouts on US30 continue when ATR expands",
        "mechanism": "volatility expansion signals fresh institutional flow",
        "required_features": ["atr_14", "session_london"],
        "expected_regime": "trending",
    }))
    h = propose(view("hypothesis", mem), llm)
    assert h.claim.startswith("London-session")
    assert h.mechanism and h.required_features == ["atr_14", "session_london"]
    assert h.expected_regime.value == "trending"
    assert llm.calls[0]["model"] == "claude-sonnet-5"  # model split wired
    # the prompt is built from the map only (whitespace listed), never results
    assert "whitespace" in llm.calls[0]["prompt"]


# --- DONE-WHEN: Synthesis produces a spec that passes validate_spec, repairing ---

def test_synthesis_repairs_until_valid():
    mem = _mem()
    h = mem.add_hypothesis(Hypothesis(claim="momentum persists on US30", mechanism="flow",
                                      required_features=["ema_50"]))
    # first spec is INVALID: signal uses atr_14 (undeclared) + p[atr_min] (not in
    # param_space) -> DSLValidationError. Second is valid.
    invalid = (
        "name: gen\n"
        "alpha:\n  features: [ema_50]\n  signal: \"close > ema_50 AND atr_14 > p[atr_min]\"\n"
        "context: {universe: [US30], timeframe: 5m}\n"
    )
    valid = (
        "name: gen\n"
        "alpha:\n  features: [ema_50]\n  signal: \"close > ema_50\"\n"
        "context: {universe: [US30], timeframe: 5m}\n"
    )
    llm = StubLLM(invalid, valid)
    spec = synthesize(h, llm)

    assert isinstance(spec, StrategySpec)
    validate_spec(spec)                    # passes unaided
    assert spec.hypothesis_id == h.id      # linked to the hypothesis
    assert len(llm.calls) == 2             # repaired exactly once
    assert "INVALID" in llm.calls[1]["prompt"]  # validator error fed back
    assert llm.calls[0]["model"] == "claude-opus-4-8"


def test_synthesis_raises_after_cap():
    mem = _mem()
    h = mem.add_hypothesis(Hypothesis(claim="x", mechanism="y"))
    bad = ("name: g\nalpha: {features: [nope_feature], signal: \"close > nope_feature\"}\n"
           "context: {universe: [US30], timeframe: 5m}\n")
    llm = StubLLM(bad, bad, bad, bad)      # max_repairs=3 -> 4 attempts
    with pytest.raises(DSLValidationError):
        synthesize(h, llm, max_repairs=3)
    assert len(llm.calls) == 4


# --- Gatekeeper: routes a fresh idea, rejects a near-duplicate ---

def test_gatekeeper_routes_fresh_rejects_dup():
    mem = _mem()
    claim = "NY-open drive on US30 persists after a wide opening range"
    mem.add_hypothesis(Hypothesis(claim=claim, mechanism="liquidity vacuum",
                                  required_features=["opening_range", "session_ny"]))
    v = view("gatekeeper", mem)

    dup = Hypothesis(claim=claim + ".", mechanism="liquidity vacuum",
                     required_features=["opening_range", "session_ny"])
    r_dup = screen(dup, v)
    assert r_dup["accepted"] is False and r_dup["category"] is None

    fresh = Hypothesis(claim="NAS100 gap-fills before 10:30 on a small overnight gap",
                       mechanism="dealer hedging", required_features=["vwap"])
    r_fresh = screen(fresh, v)
    assert r_fresh["accepted"] is True
    assert r_fresh["category"]["edge_type"]  # routed to a provisional category


# --- THE load-bearing rule: the blind view is structurally performance-blind ---

def test_blind_view_is_structurally_performance_blind():
    mem = _mem()
    h = mem.add_hypothesis(Hypothesis(claim="a promoted winning idea", mechanism="m",
                                      required_features=["ema_50"],
                                      status=HypothesisStatus.PROMOTED))
    spec = mem.add_spec(StrategySpec(
        hypothesis_id=h.id, name="s",
        alpha={"features": ["ema_50"], "signal": "close > ema_50"},
        context={"universe": ["US30"], "timeframe": "5m"}))
    exp = mem.add_experiment(ExperimentResult(
        spec_id=spec.id, dataset="d", period_start="a", period_end="b", trades_n=3,
        raw_metrics={"pnl": 0.5, "trades": [{"pnl_r": 2}]}, equity_curve=[1.0, 1.1]))
    mem.add_scorecard(Scorecard(experiment_id=exp.id, deflated_sharpe=1.2, pbo=0.1, novelty=0.9))
    mem.add_verdict(Verdict(hypothesis_id=h.id, experiment_id=exp.id,
                            type=VerdictType.PROMOTE, reasons=["good"]))

    blind = view("hypothesis", mem)
    assert isinstance(blind, BlindView)

    # exposes the map...
    entries = blind.hypotheses()
    assert entries and isinstance(entries[0], MapEntry) and entries[0].claim
    # ...but the projection has NO performance fields (status / behaviour leak it)
    assert not hasattr(entries[0], "status")
    assert not hasattr(entries[0], "behaviour_signature")
    # ...and NO results-returning method exists on the view at all
    for leak in ("record", "add_scorecard", "validated_equity_curves",
                 "get_scorecard", "scorecards", "verdicts", "experiments"):
        assert not hasattr(blind, leak), f"blind view leaks {leak}"

    # map math still works for the generator
    assert 0.0 <= blind.novelty(Hypothesis(claim="brand new", mechanism="m")) <= 1.0
    assert "momentum" in blind.coverage()

    # the FULL view (Workers/Director, Phase 7) CAN reach results — the hook
    full = view("director", mem)
    assert full.record(h.id)["scorecards"][0]["pbo"] == 0.1
