"""B1 acceptance (docs/SELF_LEARNING_ROADMAP.md): the guided local optimizer
climbs to a genuinely better ROBUST config, and REJECTS an overfit spike whose
in-sample expectancy is higher but whose param neighbourhood is knife-edge.
Fully offline + deterministic: an injected run_fn maps the param value straight
to a fixed win/loss trade book, so the landscape is controlled exactly."""

from popper.artifacts import ExperimentResult, StrategySpec
from popper.experiments.runner import compute_metrics
from popper.falsify import GauntletConfig
from popper.orchestrator.optimizer import optimize

START, END = "2025-01-01", "2026-08-01"
N = 60  # trades per run, period-independent (honest edge: IS == OOS)


def _cfg() -> GauntletConfig:
    return GauntletConfig(mc_sims=200, n_slices=8)


def _spec(grid):
    return StrategySpec(
        hypothesis_id="H-OPT", name="opt_probe",
        alpha={"features": ["ema_10"], "signal": "close > p[x]"},
        param_space={"x": grid},
    )


def _run_fn(wins_at, net=None):
    """run_fn stub: param x -> wins_at(x) winners of +1R, rest -1R. Same trades
    for every period, so WF/PBO see a stable landscape and the only thing under
    test is the optimizer's accept/reject logic. `net` (optional) injects an
    expectancy_net(x) into raw_metrics to exercise the net-of-cost objective."""
    def run_fn(spec, period=None, params=None):
        g = spec.param_space["x"]
        x = (params or {}).get("x", g[len(g) // 2])  # default = grid midpoint (compiler rule)
        w = int(round(wins_at(x)))
        trades = [{"pnl_r": 1.0}] * w + [{"pnl_r": -1.0}] * (N - w)
        eq = [1.0]
        for t in trades:
            eq.append(eq[-1] * (1 + 0.005 * t["pnl_r"]))
        m = compute_metrics({"trades": trades, "equity_curve": eq,
                             "total_return": eq[-1] - 1.0, "n_trades": N})
        m["trades"] = trades
        if net is not None:
            m["expectancy_net"] = round(net(x), 4)
        return ExperimentResult(spec_id=spec.id or "", dataset="synth",
                                period_start=str(period[0]) if period else "",
                                period_end=str(period[1]) if period else "",
                                trades_n=N, raw_metrics=m, equity_curve=eq)
    return run_fn


# --- DONE-WHEN 1: a better, still-robust config exists -> optimizer finds it ---

def test_optimizer_finds_better_robust_config():
    hill = lambda x: 40 - abs(x - 6)  # smooth hill peaking at x=6: every grid is stable
    spec = _spec([2, 3, 4])           # anchored at x=3
    best, sc, v, hist = optimize(spec, run_fn=_run_fn(hill), period=(START, END), cfg=_cfg())

    assert best.param_space["x"] == [5, 6, 7], hist  # walked the grid onto the hilltop
    # the returned scorecard still PASSES the gauntlet
    assert v.type.value in ("survive", "promote"), v.reasons
    assert sc.parameter_stability >= 0.5 and sc.pbo <= 0.5

    done = hist[-1]
    assert done["improved"] and done["converged"]           # stopped because no neighbour improved
    assert done["objective"] > hist[0]["objective"]         # objective genuinely rose
    assert done["objective_key"] == "expectancy"


# --- DONE-WHEN 2: the only higher-expectancy config is an overfit spike ->
# the anti-overfit guard REJECTS it and keeps the robust original ---

def test_optimizer_rejects_overfit_spike():
    # x=4 spikes in-sample expectancy, but its neighbour x=5 is a cliff: the
    # shifted grid [2,3,4] is knife-edge (PF dispersion) -> gauntlet KILL +
    # parameter_stability collapse. A single-fit optimizer would take it.
    table = {1: 34, 2: 35, 3: 36, 4: 50, 5: 10}
    spec = _spec([1, 2, 3])  # anchored at x=2; down-shift is blocked (x=0 illegal)
    best, sc, v, hist = optimize(spec, run_fn=_run_fn(lambda x: table[int(round(x))]),
                                 period=(START, END), cfg=_cfg())

    # kept the robust original, unchanged
    assert best is spec and best.param_space["x"] == [1, 2, 3]
    assert v.type.value in ("survive", "promote"), v.reasons
    assert sc.parameter_stability >= 0.5

    # THE GUARD FIRED: the up-move raised the objective but failed falsification
    spike = next(e for e in hist if e.get("move") == "x+")
    assert spike["objective"] > hist[0]["objective"]   # tempting in-sample improvement
    assert spike["accepted"] is False                  # ...rejected anyway
    assert spike["verdict"] == "kill"
    assert spike["parameter_stability"] < 0.5
    assert "overfit" in spike["reason"]

    done = hist[-1]
    assert not done["improved"] and done["converged"]


# --- objective = expectancy_net when the anchor experiment records it ---

def test_optimizer_prefers_net_of_cost_objective():
    hill = lambda x: 40 - abs(x - 6)          # gross says: move up
    net = lambda x: -x / 100                  # net-of-cost says: move down
    spec = _spec([2, 3, 4])
    best, sc, v, hist = optimize(spec, run_fn=_run_fn(hill, net=net),
                                 period=(START, END), cfg=_cfg())
    assert hist[0]["objective_key"] == "expectancy_net"
    assert best.param_space["x"] == [1, 2, 3], hist   # followed net, not gross
    assert v.type.value in ("survive", "promote")
