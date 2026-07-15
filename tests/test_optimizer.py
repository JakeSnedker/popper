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


# --- B3 structural depth: wrapper-level exit-menu moves ---

def _wrapper_run_fn(wins_of):
    """run_fn stub keyed on the SPEC: wins_of(spec, x) winners of +1R, rest -1R.
    Lets the landscape depend on the wrapper (trail on/off), not just param x."""
    def run_fn(spec, period=None, params=None):
        g = spec.param_space["x"]
        x = (params or {}).get("x", g[len(g) // 2])
        w = int(round(wins_of(spec, x)))
        trades = [{"pnl_r": 1.0}] * w + [{"pnl_r": -1.0}] * (N - w)
        eq = [1.0]
        for t in trades:
            eq.append(eq[-1] * (1 + 0.005 * t["pnl_r"]))
        m = compute_metrics({"trades": trades, "equity_curve": eq,
                             "total_return": eq[-1] - 1.0, "n_trades": N})
        m["trades"] = trades
        return ExperimentResult(spec_id=spec.id or "", dataset="synth",
                                period_start=str(period[0]) if period else "",
                                period_end=str(period[1]) if period else "",
                                trades_n=N, raw_metrics=m, equity_curve=eq)
    return run_fn


def test_optimizer_accepts_robust_structural_move():
    # numeric hill already peaks at the anchor (x=3): no grid shift improves.
    # Turning the ATR trail ON adds 5 winners at every x -> robustly better.
    wins = lambda spec, x: (36 - abs(x - 3)) + (5 if spec.wrapper.trail.type == "atr" else 0)
    spec = _spec([2, 3, 4])  # default wrapper: trail none, break_even none
    best, sc, v, hist = optimize(spec, run_fn=_wrapper_run_fn(wins),
                                 period=(START, END), cfg=_cfg())

    assert best.wrapper.trail.type == "atr"      # structural move landed
    assert best.alpha == spec.alpha              # exploit never touches the alpha
    assert best.param_space == spec.param_space  # ...and this win wasn't a grid shift
    assert v.type.value in ("survive", "promote"), v.reasons

    done = hist[-1]
    assert done["improved"] and done["converged"]
    assert "trail_on" in done["move_log"]
    assert done["moves_accepted"] == len(done["move_log"]) == 1
    assert done["objective"] > hist[0]["objective"]
    # the accepted structural candidate went through the SAME gauntlet guard
    entry = next(e for e in hist if e.get("move") == "trail_on" and e["accepted"])
    assert entry["structural"] is True
    assert entry["verdict"] in ("survive", "promote")


def test_optimizer_skips_invalid_structural_variant():
    # trail mult is a p[tm] grid ref: the trail_off variant orphans param_space
    # key 'tm' -> DSLValidationError -> dropped, never evaluated, no crash.
    spec = StrategySpec(
        hypothesis_id="H-OPT", name="opt_probe",
        alpha={"features": ["ema_10"], "signal": "close > p[x]"},
        wrapper={"trail": {"type": "atr", "mult": "p[tm]"}},
        param_space={"x": [2, 3, 4], "tm": [1.5, 2.0, 2.5]},
    )
    flat_hill = lambda spec, x: 36 - abs(x - 3)  # nothing improves anywhere
    best, sc, v, hist = optimize(spec, run_fn=_wrapper_run_fn(flat_hill),
                                 period=(START, END), cfg=_cfg())

    assert best is spec
    assert not any(e.get("move") == "trail_off" for e in hist)      # dropped variant
    assert not any(e.get("move", "").startswith("trail_mult") for e in hist)  # p[tm] not steppable
    assert any(e.get("move") == "be_on" for e in hist)              # valid sibling still tried
    done = hist[-1]
    assert done["converged"] and not done["improved"]


def test_done_entry_contract_and_depth_keys():
    spec = _spec([2, 3, 4])
    hill = lambda x: 36 - abs(x - 3)  # anchored at the peak: converge immediately
    best, sc, v, hist = optimize(spec, run_fn=_run_fn(hill),
                                 period=(START, END), cfg=_cfg())

    done = hist[-1]
    assert done["move"] == "done"
    # Director contract: these keys must survive untouched
    assert done["converged"] is True and done["improved"] is False
    assert set(done) >= {"evals", "objective", "objective_key"}
    # B3 depth instrumentation
    assert done["moves_tried"] == done["evals"] > 0
    assert done["moves_accepted"] == 0
    assert done["move_log"] == []


def test_accept_rescues_cost_killed_but_protects_survivors():
    """B2 rescue: a KILL (require_survive=False) accepts an improving, guarded
    step even while still a non-survivor; a SURVIVOR (require_survive=True) does
    not walk into a non-survivor. Guards (stable, pbo_ok) always bind."""
    from popper.orchestrator.optimizer import _accept
    # rescuing a cost-killed kill: improving + guarded + still-losing -> accept
    assert _accept(obj=-0.01, best_obj=-0.02, survived=False, stable=True,
                   pbo_ok=True, require_survive=False)
    # same step while protecting a survivor -> rejected (would leave survive tier)
    assert not _accept(obj=-0.01, best_obj=-0.02, survived=False, stable=True,
                       pbo_ok=True, require_survive=True)
    # guards always bind: unstable or pbo-blown never accepts, rescue or not
    assert not _accept(0.5, 0.0, survived=True, stable=False, pbo_ok=True, require_survive=False)
    assert not _accept(0.5, 0.0, survived=True, stable=True, pbo_ok=False, require_survive=False)
    # no improvement -> never accept
    assert not _accept(obj=-0.02, best_obj=-0.02, survived=True, stable=True,
                       pbo_ok=True, require_survive=False)
