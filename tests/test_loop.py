"""Phase 5 acceptance (spec/06 row 5): one call takes a hand-written hypothesis
file end-to-end — hypothesis stored, spec linked, scorecard with novelty
populated, verdict + status persisted — and re-running the SAME file is
flagged stale. Injected run_fn over synthetic bars: fast, offline,
deterministic (no US30 parquet dependency)."""

import numpy as np
import pytest
from test_falsify import END, START, _bars, _cfg, _run_fn_over

from popper.dsl import DSLValidationError
from popper.memory import LocalEmbedder
from popper.memory.sqlite_repo import SqliteMemory
from popper.research import run_loop

HYP_YAML = """\
hypothesis:
  claim: "US30 trends persist: price above the 10-EMA keeps drifting higher"
  mechanism: "momentum herding: participants chase established direction"
spec:
  name: trend_follow
  alpha:
    features: [ema_10]
    signal: "close > ema_10"
  wrapper:
    stop: { type: atr, mult: 1.5 }
    target: { type: rr, ratio: "p[rr]" }
  context: { universe: [US30], timeframe: 1h }
  param_space:
    rr: [1.5, 2.0, 2.5]
"""


@pytest.fixture
def setup(tmp_path):
    mem = SqliteMemory(str(tmp_path / "t.db"), embedder=LocalEmbedder())
    f = tmp_path / "h.yaml"
    f.write_text(HYP_YAML, encoding="utf-8")
    n = 13000  # same gentle-uptrend series test_falsify's robust spec survives on
    rng = np.random.default_rng(3)
    close = 100 + 0.04 * np.arange(n) + rng.normal(0, 1.5, n).cumsum()
    return mem, f, _run_fn_over(_bars(close))


# --- DONE-WHEN: hypothesis stored, spec scored + judged, results persisted ---

def test_full_loop_stores_scores_and_judges(setup):
    mem, f, run_fn = setup
    s = run_loop(f, period=(START, END), cfg=_cfg(), mem=mem, run_fn=run_fn)

    # hypothesis stored + enriched, not stale on first submission
    h = mem.get_hypothesis(s["hypothesis_id"])
    assert h is not None and h.embedding and h.novelty_score == 1.0
    assert s["stale"] is False

    # full artifact trail persisted: spec -> experiment -> scorecard -> verdict
    trail = mem.record(h.id)
    assert trail["specs"][0]["id"] == s["spec_id"]
    assert trail["specs"][0]["hypothesis_id"] == h.id
    assert trail["experiments"][0]["id"] == s["experiment_id"]
    row = trail["scorecards"][0]
    assert row["id"] == s["scorecard_id"]
    assert row["novelty"] > 0.0  # map fields completed before persisting
    assert row["behaviour_signature"]
    assert trail["verdicts"][0]["type"] == s["verdict"]

    # verdict drives status; this robust trend spec must not be killed
    assert s["verdict"] in ("survive", "promote"), s["reasons"]
    assert h.status.value == s["status"]
    assert h.behaviour_signature  # post-test behaviour attached to the hypothesis

    # readable-summary contract the CLI prints from
    assert s["claim"].startswith("US30 trends persist")
    assert {"pbo", "deflated_sharpe", "robustness", "param_stability",
            "novelty", "n_trades", "expected_edge"} <= set(s["stats"])
    assert s["stats"]["n_trades"] > 0


# --- DONE-WHEN: re-running the SAME file flags it stale (but still runs) ---

def test_second_run_of_same_file_is_stale(setup):
    mem, f, run_fn = setup
    first = run_loop(f, period=(START, END), cfg=_cfg(), mem=mem, run_fn=run_fn)
    second = run_loop(f, period=(START, END), cfg=_cfg(), mem=mem, run_fn=run_fn)

    assert first["stale"] is False
    assert second["stale"] is True
    assert second["hypothesis_id"] != first["hypothesis_id"]  # ran anyway, fully recorded
    assert mem.stats()["counts"]["verdicts"] == 2


# --- a bad spec is rejected before anything is stored ---

def test_invalid_spec_rejected_and_nothing_stored(setup):
    mem, f, run_fn = setup
    f.write_text(HYP_YAML.replace("ema_10", "not_a_feature"), encoding="utf-8")
    with pytest.raises(DSLValidationError):
        run_loop(f, period=(START, END), cfg=_cfg(), mem=mem, run_fn=run_fn)
    assert mem.stats()["counts"]["hypotheses"] == 0
