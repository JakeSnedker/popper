"""Phase 8 acceptance (observability): judge() records a glass-box `diagnostics`
dict on the Scorecard — every gate's computed values + its threshold + pass/fail
— and the sqlite store round-trips it. Verdict logic itself is unchanged."""

import numpy as np
from test_falsify import END, START, _bars, _cfg, _run_fn_over
from test_loop import HYP_YAML

from popper.artifacts import StrategySpec, VerdictType
from popper.falsify import judge
from popper.memory import LocalEmbedder
from popper.memory.sqlite_repo import SqliteMemory
from popper.research import run_loop

ALL_GATES = {"gate_signal_edge", "gate0_leakage", "gate1_walkforward",
             "gate2_robustness", "gate3_mc_ruin", "gate4_multiple_testing"}


def _trend_bars():
    n = 13000
    rng = np.random.default_rng(3)
    return _bars(100 + 0.04 * np.arange(n) + rng.normal(0, 1.5, n).cumsum())


def _spec() -> StrategySpec:
    return StrategySpec(
        hypothesis_id="H-DIAG", name="trend_follow",
        alpha={"features": ["ema_10"], "signal": "close > ema_10"},
        wrapper={"stop": {"type": "atr", "mult": 1.5}, "target": {"type": "rr", "ratio": "p[rr]"}},
        context={"universe": ["US30"], "timeframe": "1h"},
        param_space={"rr": [1.5, 2.0, 2.5]},
    )


# --- a fully-judged spec carries every gate + threshold, and sqlite round-trips it ---

def test_diagnostics_all_gates_and_persistence(tmp_path):
    mem = SqliteMemory(str(tmp_path / "t.db"), embedder=LocalEmbedder())
    f = tmp_path / "h.yaml"
    f.write_text(HYP_YAML, encoding="utf-8")
    tb = _trend_bars()
    s = run_loop(f, period=(START, END), cfg=_cfg(), mem=mem, run_fn=_run_fn_over(tb), bars=tb)

    d = mem.record(s["hypothesis_id"])["scorecards"][0]["diagnostics"]  # reloaded from sqlite
    assert set(d) == ALL_GATES
    for gate in d.values():
        assert isinstance(gate["passed"], bool)

    g1 = d["gate1_walkforward"]
    assert g1["wfe_threshold"] == 0.5 and g1["min_trades"] == 20 and g1["min_forward_pf"] == 1.0
    assert g1["windows"] and {"opt", "fwd", "is_pf", "fwd_pf"} <= set(g1["windows"][0])
    g2 = d["gate2_robustness"]
    assert g2["min_profitable_fraction"] == 0.5 and g2["max_pf_cv"] == 0.3 and len(g2["grid_pfs"]) == 3
    g3 = d["gate3_mc_ruin"]
    assert g3["max_mc_ruin"] == 0.15 and {"p_ruin", "p_profit", "dd_mean", "dd_95", "dd_worst"} <= set(g3)
    g4 = d["gate4_multiple_testing"]
    assert g4["max_pbo"] == 0.5 and g4["min_deflated_sharpe"] == 0.5 and g4["n_trials"] == 3


# --- an early KILL still records what ran, with the failing gate marked ---

def test_killed_spec_records_failing_gate():
    # 2-month period -> zero walk-forward windows -> insufficient-data KILL at gate 1
    sc, v = judge(_spec(), run_fn=_run_fn_over(_trend_bars()),
                  period=(START, "2025-03-01"), cfg=_cfg())
    assert v.type == VerdictType.KILL
    d = sc.diagnostics
    assert d["gate0_leakage"]["passed"] is True
    assert d["gate1_walkforward"]["passed"] is False
    assert d["gate1_walkforward"]["min_windows"] == 4
    assert "gate2_robustness" not in d  # later gates never ran
