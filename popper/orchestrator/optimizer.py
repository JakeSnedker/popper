"""Guided local optimizer for the exploit engine (docs/SELF_LEARNING_ROADMAP.md B1/B3).

Coordinate search over a spec's DISCRETE param_space: a move re-centers one
numeric grid on the adjacent value by shifting the whole grid one local step,
so the anchor/default param (the grid midpoint) walks the landscape while the
gauntlet keeps probing a same-sized neighbourhood around it.

STRUCTURAL DEPTH (B3-adjacent): each sweep also tries wrapper-level exit-menu
variants of the current best — toggle trailing/break-even, step their knobs.
Refining the SAME alpha with a better wrapper is exploit; `spec.alpha` is
never touched (changing the alpha is explore's job).

ANTI-OVERFIT GUARD — the point of B1: every candidate is scored by re-running
the FULL falsification gauntlet (`judge` — walk-forward CV, parameter
stability, Monte Carlo, DSR, PBO), never by a lone in-sample fit. A move is
accepted only if it BOTH improves the objective (net-of-cost expectancy when
`expectancy_net` is recorded, else `expectancy`) AND still survives:
verdict in {survive, promote}, parameter_stability >= the config's
min_profitable_fraction, PBO <= max_pbo. An in-sample spike with a knife-edge
neighbourhood raises the objective but fails the gauntlet and is REJECTED —
there is no scoring path that bypasses falsification.
"""

from popper.artifacts.strategy_spec import BreakEvenRule, TrailRule
from popper.dsl import DSLValidationError, validate_spec
from popper.experiments.runner import run
from popper.falsify import GauntletConfig
from popper.falsify.gauntlet import judge

SURVIVE = ("survive", "promote")


def _objective(exp) -> tuple[float, str]:
    """Net-of-cost expectancy when the anchor experiment recorded it, else
    gross expectancy. Never raw PnL."""
    m = exp.raw_metrics
    key = "expectancy_net" if "expectancy_net" in m else "expectancy"
    return float(m.get(key, 0.0)), key


def _movable(spec) -> list[str]:
    """Params whose grid can shift: >=2 strictly-ascending numeric values."""
    out = []
    for name, g in spec.param_space.items():
        if (len(g) >= 2
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in g)
                and all(a < b for a, b in zip(g, g[1:]))):
            out.append(name)
    return out


def _shift(spec, name: str, direction: int):
    """Neighbour move: re-center `name`'s grid on its adjacent value — shift
    every grid value by one local step (midpoint spacing). None if illegal."""
    g = spec.param_space[name]
    mid = len(g) // 2
    if direction > 0:
        step = g[mid + 1] - g[mid] if mid + 1 < len(g) else g[mid] - g[mid - 1]
    else:
        step = g[mid] - g[mid - 1] if mid >= 1 else g[mid + 1] - g[mid]
    new = [round(x + direction * step, 10) for x in g]
    if min(g) > 0 and min(new) <= 0:
        return None  # don't walk a positive-domain grid (rr, atr mult) through zero
    ps = dict(spec.param_space)
    ps[name] = new
    return spec.model_copy(update={"id": None, "param_space": ps})


def _step_knob(value, direction: int, step: float = 0.5):
    """One discrete step on a literal numeric wrapper knob. None if the value
    is a p[...] ref (that's the numeric grid search's job) or would go <= 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    new = round(value + direction * step, 10)
    return new if new > 0 else None


def _wrapper_neighbors(spec) -> list[tuple[str, object]]:
    """Small fixed set of structural exit-menu variants of `spec` — wrapper
    ONLY, alpha untouched. Returns (move_name, spec) pairs; variants that fail
    DSL validation are dropped (e.g. toggling off a rule whose knob was the
    only reference to a param_space grid).
    ponytail: structural choices are discrete — parameter stability probes the
    numeric grids AROUND a variant, not the toggle itself; the gauntlet's
    WF/MC/PBO stages are what falsify a structural move."""
    w = spec.wrapper
    cands: list[tuple[str, object]] = []

    def add(name, **fields):
        cands.append((name, spec.model_copy(
            update={"id": None, "wrapper": w.model_copy(update=fields)})))

    if w.trail.type == "none":
        add("trail_on", trail=TrailRule(type="atr", mult=2.0))
    else:
        add("trail_off", trail=TrailRule(type="none"))
        for d, tag in ((1, "trail_mult+"), (-1, "trail_mult-")):
            m = _step_knob(w.trail.mult, d)
            if m is not None:
                add(tag, trail=w.trail.model_copy(update={"mult": m}))
    if w.break_even.type == "none":
        add("be_on", break_even=BreakEvenRule(type="after_r", trigger_r=1.0))
    else:
        add("be_off", break_even=BreakEvenRule(type="none"))
        for d, tag in ((1, "be_trigger+"), (-1, "be_trigger-")):
            r = _step_knob(w.break_even.trigger_r, d)
            if r is not None:
                add(tag, break_even=w.break_even.model_copy(update={"trigger_r": r}))

    out = []
    for name, cand in cands:
        try:
            validate_spec(cand)
        except DSLValidationError:
            continue  # invalid variant: drop, never crash the search
        out.append((name, cand))
    return out


def _accept(obj, best_obj, survived, stable, pbo_ok, require_survive) -> bool:
    """Hill-climb acceptance. Overfitting guards (parameter-stability, pbo) ALWAYS
    hold — they're what stop a 'win' from being curve-fit. `require_survive` is
    True only when refining an already-surviving seed (protect the winner); False
    when RESCUING a cost-killed kill, so the climb may pass through still-losing-
    but-improving states toward net>0 (roadmap B2). Objective must strictly improve."""
    return stable and pbo_ok and (survived or not require_survive) and obj > best_obj


def optimize(spec, run_fn=run, period=None, cfg: GauntletConfig | None = None,
             mem=None, max_iters: int = 24):
    """Greedy coordinate search around `spec`: each sweep tries the numeric
    grid shifts AND the structural wrapper variants (`_wrapper_neighbors`).
    Returns (best_spec, best_scorecard, best_verdict, history).

    Every evaluation = one anchor run (the objective) + one FULL gauntlet
    `judge` (the guard). `mem` is forwarded to judge — leave it None during
    unattended runs so rejected candidates don't persist anchor experiments;
    the Director persists the winner itself. Budget: `max_iters` candidate
    evaluations; converges early when no neighbour improves (feeds B4).
    Returns the original spec unchanged if nothing beat it robustly."""
    cfg = cfg or GauntletConfig()
    t = cfg.thresholds
    history: list[dict] = []

    def _eval(s):
        obj, key = _objective(run_fn(s, period=period, params=None))
        sc, v = judge(s, run_fn=run_fn, period=period, cfg=cfg, mem=mem)
        survived = v.type.value in SURVIVE
        stable = sc.parameter_stability >= t.min_profitable_fraction
        pbo_ok = sc.pbo <= t.max_pbo
        return obj, key, sc, v, survived, stable, pbo_ok

    def _why(sc, v, survived, stable, pbo_ok, obj=None):
        if not stable:
            return f"overfit: parameter_stability {sc.parameter_stability} < {t.min_profitable_fraction}"
        if not pbo_ok:
            return f"overfit: pbo {sc.pbo} > {t.max_pbo}"
        if require_survive and not survived:
            return f"verdict {v.type.value}: {'; '.join(v.reasons)}"
        if obj is not None and not (obj > best_obj):
            return f"objective {obj} did not improve on {best_obj}"
        return "ok"

    best = spec
    best_obj, obj_key, best_sc, best_v, base_survived, base_stable, base_pbo = _eval(spec)
    # rescue vs refine: only PROTECT an already-surviving seed (require every step
    # stay a survivor). A cost-killed KILL is allowed to climb through still-losing-
    # but-improving states toward net>0 — the whole point of B2. Guards always hold.
    require_survive = base_survived
    history.append({"move": "baseline", "objective": best_obj, "objective_key": obj_key,
                    "verdict": best_v.type.value, "parameter_stability": best_sc.parameter_stability,
                    "pbo": best_sc.pbo, "accepted": True,
                    "reason": _why(best_sc, best_v, base_survived, base_stable, base_pbo)})

    movable = _movable(spec)
    evals, moved = 0, True
    moves_tried, moves_accepted, move_log = 0, 0, []
    while moved and evals < max_iters:
        moved = False
        for name in movable:
            for direction in (1, -1):
                if evals >= max_iters:
                    break
                cand = _shift(best, name, direction)
                if cand is None:
                    continue
                evals += 1
                moves_tried += 1
                obj, _, sc, v, survived, stable, pbo_ok = _eval(cand)
                accepted = _accept(obj, best_obj, survived, stable, pbo_ok, require_survive)
                why = _why(sc, v, survived, stable, pbo_ok, obj)
                mname = f"{name}{'+' if direction > 0 else '-'}"
                history.append({"move": mname,
                                "param": name, "direction": direction,
                                "param_space": cand.param_space, "objective": obj,
                                "objective_key": obj_key, "verdict": v.type.value,
                                "parameter_stability": sc.parameter_stability, "pbo": sc.pbo,
                                "accepted": accepted, "reason": why})
                if accepted:
                    best, best_obj, best_sc, best_v = cand, obj, sc, v
                    moved = True
                    moves_accepted += 1
                    move_log.append(mname)
        # structural exit-menu moves around the current best: same _eval, same
        # acceptance rule — the full gauntlet re-clears every candidate.
        for mname, cand in _wrapper_neighbors(best):
            if evals >= max_iters:
                break
            evals += 1
            moves_tried += 1
            obj, _, sc, v, survived, stable, pbo_ok = _eval(cand)
            accepted = _accept(obj, best_obj, survived, stable, pbo_ok, require_survive)
            why = _why(sc, v, survived, stable, pbo_ok, obj)
            history.append({"move": mname, "structural": True, "objective": obj,
                            "objective_key": obj_key, "verdict": v.type.value,
                            "parameter_stability": sc.parameter_stability, "pbo": sc.pbo,
                            "accepted": accepted, "reason": why})
            if accepted:
                best, best_obj, best_sc, best_v = cand, obj, sc, v
                moved = True
                moves_accepted += 1
                move_log.append(mname)
                break  # ponytail: remaining neighbours came off the old best — rebuild next sweep

    history.append({"move": "done", "converged": not moved and evals < max_iters,
                    "evals": evals, "improved": best is not spec,
                    "objective": best_obj, "objective_key": obj_key,
                    "moves_tried": moves_tried, "moves_accepted": moves_accepted,
                    "move_log": move_log})
    return best, best_sc, best_v, history
