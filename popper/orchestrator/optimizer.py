"""Guided local optimizer for the exploit engine (docs/SELF_LEARNING_ROADMAP.md B1).

Coordinate search over a spec's DISCRETE param_space: a move re-centers one
numeric grid on the adjacent value by shifting the whole grid one local step,
so the anchor/default param (the grid midpoint) walks the landscape while the
gauntlet keeps probing a same-sized neighbourhood around it.

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


def optimize(spec, run_fn=run, period=None, cfg: GauntletConfig | None = None,
             mem=None, max_iters: int = 12):
    """Greedy coordinate search around `spec`. Returns
    (best_spec, best_scorecard, best_verdict, history).

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
        why = ("ok" if survived and stable and pbo_ok else
               f"overfit: parameter_stability {sc.parameter_stability} < {t.min_profitable_fraction}"
               if not stable else f"overfit: pbo {sc.pbo} > {t.max_pbo}"
               if not pbo_ok else f"verdict {v.type.value}: {'; '.join(v.reasons)}")
        return obj, key, sc, v, (survived and stable and pbo_ok), why

    best = spec
    best_obj, obj_key, best_sc, best_v, _, why = _eval(spec)
    history.append({"move": "baseline", "objective": best_obj, "objective_key": obj_key,
                    "verdict": best_v.type.value, "parameter_stability": best_sc.parameter_stability,
                    "pbo": best_sc.pbo, "accepted": True, "reason": why})

    movable = _movable(spec)
    evals, moved = 0, True
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
                obj, _, sc, v, robust, why = _eval(cand)
                accepted = robust and obj > best_obj
                if robust and not accepted:
                    why = f"objective {obj} did not improve on {best_obj}"
                history.append({"move": f"{name}{'+' if direction > 0 else '-'}",
                                "param": name, "direction": direction,
                                "param_space": cand.param_space, "objective": obj,
                                "objective_key": obj_key, "verdict": v.type.value,
                                "parameter_stability": sc.parameter_stability, "pbo": sc.pbo,
                                "accepted": accepted, "reason": why})
                if accepted:
                    best, best_obj, best_sc, best_v = cand, obj, sc, v
                    moved = True

    history.append({"move": "done", "converged": not moved and evals < max_iters,
                    "evals": evals, "improved": best is not spec,
                    "objective": best_obj, "objective_key": obj_key})
    return best, best_sc, best_v, history
