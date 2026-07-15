"""D2/D3 portfolio constructor: friction HARD-GATE + de-correlation + weights."""
from popper.portfolio import build_book


def _cand(sid, net, pbo, stab, trades, n=10):
    return {"spec_id": sid, "metrics": {"expectancy_net": net},
            "scorecard": {"pbo": pbo, "parameter_stability": stab},
            "trades": trades, "n_bars": n}


def _up(): return [{"exit_i": i, "pnl_r": 1.0} for i in range(5)]
def _alt(): return [{"exit_i": i, "pnl_r": (-1.0 if i % 2 else 1.0)} for i in range(5)]


def test_friction_gate_and_decorrelation():
    book = build_book([
        _cand("A", 0.15, 0.3, 0.9, _up()),
        _cand("clone", 0.10, 0.3, 0.9, _up()),      # corr 1 with A -> dropped
        _cand("B", 0.12, 0.3, 0.9, _alt()),          # uncorrelated -> kept
        _cand("costneg", -0.05, 0.3, 0.9, _up()),    # friction hard-gate
        _cand("overfit", 0.20, 0.9, 0.9, _up()),     # pbo blown
        {"spec_id": "nonet", "metrics": {}, "scorecard": {}, "trades": _up(), "n_bars": 10},
    ])
    assert [e["spec_id"] for e in book["edges"]] == ["A", "B"]
    assert abs(sum(e["weight"] for e in book["edges"]) - 1.0) < 1e-9
    rej = {r["spec_id"] for r in book["rejected"]}
    assert {"costneg", "overfit", "nonet", "clone"} <= rej


def test_empty_when_all_fail_friction():
    book = build_book([_cand("x", -0.1, 0.3, 0.9, _up())])
    assert book["n_selected"] == 0 and book["edges"] == []
    assert book["combined"]["expectancy_per_bar"] == 0.0
