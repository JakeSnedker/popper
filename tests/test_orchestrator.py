"""Phase 7 acceptance (spec/06 row 7 — the autonomy milestone): an UNATTENDED run
proposes candidates, KILLs most via the gauntlet, and surfaces >=1 survivor.
Fully offline + deterministic: a type-routing scripted LLM feeds canned
hypotheses/specs, and _run_fn_over backtests the real engine over synthetic
bars (no US30 parquet, no network). Also asserts the budget split is honored and
a dead category is pruned."""

import json
import os
import tempfile

from test_falsify import END, START, _bars, _cfg, _run_fn_over

import numpy as np

from popper.memory import LocalEmbedder
from popper.memory.sqlite_repo import SqliteMemory
from popper.orchestrator import run_research


class ScriptedLLM:
    """Routes by agent system-prompt: Hypothesis calls get the next hyp JSON,
    Synthesis calls get the next spec YAML. Deterministic, offline."""

    def __init__(self, hyps, specs):
        self.hyps, self.specs, self.models = list(hyps), list(specs), []

    def complete(self, system, prompt, model, max_tokens=1024):
        self.models.append(model)
        return self.hyps.pop(0) if "FALSIFIABLE" in system else self.specs.pop(0)


def _hyp(claim, mech, feats):
    return json.dumps({"claim": claim, "mechanism": mech, "required_features": feats,
                       "expected_regime": "unknown"})


# a trend-follower that SURVIVES on the gentle-uptrend bars (cf. test_falsify robust)
_GOOD = (
    "name: momo\n"
    "alpha:\n  features: [ema_10]\n  signal: \"close > ema_10\"\n"
    "wrapper:\n  stop: {type: atr, mult: 1.5}\n  target: {type: rr, ratio: \"p[rr]\"}\n"
    "context: {universe: [US30], timeframe: 1h}\n"
    "param_space: {rr: [1.5, 2.0, 2.5]}\n"
)
# an impossible signal -> zero trades -> guaranteed KILL, bars-independent
_DEAD = (
    "name: dead\n"
    "alpha:\n  features: [atr_14]\n  signal: \"atr_14 > p[floor]\"\n"
    "context: {universe: [US30], timeframe: 1h}\n"
    "param_space: {floor: [1000000.0]}\n"
)


def _mem():
    return SqliteMemory(os.path.join(tempfile.mkdtemp(), "t.db"), embedder=LocalEmbedder())


def _llm():
    # consumption order: survivor first (so a live category exists for exploit),
    # then two DISTINCT mean-reversion kills (prunes the category), then more kills.
    hyps = [
        _hyp("US30 momentum: price above the 10-EMA keeps trending higher", "herding flow", ["ema_10"]),
        _hyp("US30 prices revert to the session mean after an extreme stretch", "overreaction", ["atr_14"]),
        _hyp("sharp moves on US30 fade back toward fair value within the hour", "liquidity absorption", ["atr_14"]),
        _hyp("an opening-range breakout on US30 continues after expansion", "auction imbalance", ["atr_14"]),
        _hyp("stop-run sweeps below prior lows on US30 snap back", "liquidity vacuum", ["atr_14"]),
        _hyp("US30 grinds higher into the London close on order-flow drift", "flow", ["atr_14"]),
    ]
    specs = [_GOOD, _DEAD, _DEAD, _DEAD, _DEAD, _DEAD]
    return ScriptedLLM(hyps, specs)


def _bars_uptrend():
    n = 13000
    rng = np.random.default_rng(3)
    close = 100 + 0.04 * np.arange(n) + rng.normal(0, 1.5, n).cumsum()
    return _bars(close)


def test_unattended_run_kills_most_surfaces_survivor():
    mem = _mem()
    s = run_research(mem=mem, llm=_llm(), run_fn=_run_fn_over(_bars_uptrend()),
                     period=(START, END), cfg=_cfg(), iterations=8)

    # DONE-WHEN: proposed several, killed most, surfaced >=1 survivor
    assert s.proposed >= 2, s
    assert s.survived >= 1, s
    assert s.killed > s.survived, f"expected most candidates killed: {s}"
    assert s.survivors and s.survivors[0]["verdict"] in ("survive", "promote")

    # budget split honored across iterations (70 exploit / 20 explore / 10 wildcard)
    assert sum(s.budget_spent.values()) == 8
    assert s.budget_spent["exploit"] > s.budget_spent["explore"] >= s.budget_spent["wildcard"] >= 1
    assert s.budget_spent["exploit"] == max(s.budget_spent.values())

    # exploit engine actually ran Workers on the live category
    assert s.worker_runs >= 1, s

    # a category that only ever KILLed got pruned dead
    dead = [et for et, c in s.categories.items() if c["dead"]]
    assert dead, f"expected a pruned dead category: {s.category_report()}"

    # runtime models come from the split (Fable is build-only)
    assert set(s.budget_spent) == {"exploit", "explore", "wildcard"}


def test_run_is_deterministic():
    a = run_research(mem=_mem(), llm=_llm(), run_fn=_run_fn_over(_bars_uptrend()),
                     period=(START, END), cfg=_cfg(), iterations=8)
    b = run_research(mem=_mem(), llm=_llm(), run_fn=_run_fn_over(_bars_uptrend()),
                     period=(START, END), cfg=_cfg(), iterations=8)
    assert (a.proposed, a.killed, a.survived, a.worker_runs, a.budget_spent) == \
           (b.proposed, b.killed, b.survived, b.worker_runs, b.budget_spent)
