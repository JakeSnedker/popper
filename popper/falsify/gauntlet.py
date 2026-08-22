"""The Falsification Gauntlet v0 (spec/03) + DSL v0.3 layered falsification
(docs/DSL_v0.3_EXIT_SPEC.md — predict -> harvest -> prove).

judge(spec) runs the gates fail-fast (cheapest first) over the Phase-2 runner
and emits a Scorecard + Verdict (SURVIVE | KILL(reasons) | PROMOTE):

  L1. signal edge  — exit-neutral forward-return gate over time horizons
                     {3,10,20,60} bars (spec §3). Hard KILL before anything
                     expensive if the entry predicts nothing.
  L2. exit menu    — staged first-pass SL x TP menu (27 configs, spec §5) on
                     Layer-1 survivors; the best-edge config carries into the
                     gates below, and the menu breadth inflates DSR n_trials.
  0. leakage       — look-ahead detector (future-perturbation) + purged/embargoed CV
  1. walk-forward  — anchored OOS windows (JJC calculate_windows, adapted to our dates)
  2. robustness    — param-neighbourhood stability (PF dispersion, profitable fraction)
  3. monte carlo   — trade-shuffle P(ruin)
  4. multiple-test — Deflated Sharpe + PBO via CSCV (the LdP moat)

Gate thresholds are PORTED from the JJC PassFailCriteria. The runner is
injectable (`run_fn`) so the gauntlet's decision logic is testable with
controlled data; the default is the real US30 Phase-2 runner. Layer 1/2 need
raw bars: they are auto-loaded for the real runner, or injected via `bars=`
for offline tests; with neither, both layers record themselves as skipped and
gates 0-4 behave exactly as v0.2.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from itertools import product
from math import sqrt
from statistics import mean, pstdev

import numpy as np
import pandas as pd

from popper.artifacts import Scorecard, Verdict, VerdictType
from popper.artifacts.strategy_spec import StopRule, TargetRule
from popper.data import load_bars, load_higher_timeframe_bars
from popper.data.timeframes import timeframe_minutes
from popper.dsl import compile_to_backtest
from popper.dsl.expr import eval_signal
from popper.falsify.overlap import (
    block_bootstrap_lower,
    effective_n,
    newey_west_t,
)
from popper.experiments import run
from popper.falsify.stats import (
    calculate_edge,
    deflated_sharpe,
    monte_carlo_ruin,
    pbo_cscv,
    purged_kfold,
)

L1_HORIZONS = (3, 10, 20, 60)  # spec §3: M5 ~15m / 50m / ~1.5h / 5h
L1_HORIZON_FAMILIES = {
    "scalp_intraday": (3, 10, 20),
    "intraday_swing": (10, 20, 60),
}
L1_MIN_T = 2.0                 # balanced/strict min t-stat inside the winning horizon family
L1_MIN_SIGNALS = 30            # fewer firings than this = no evidence of edge
L1_BOOT_ALPHA = 0.05           # bootstrap lower bound is the 5th percentile
# `min_t` now reads the OVERLAP-CORRECTED t, which is strictly smaller than the
# raw one, so the same numbers are a materially higher bar than before.
# `required_boot_ok` counts horizons whose bootstrap lower bound clears zero --
# evidence that survives without assuming thin tails, which is how a skewed
# high-R payoff earns a pass it could never get from t alone.
L1_SIGNAL_EDGE_MODES = {
    # coherent positive family is enough; useful for discovery, not promotion
    "loose": {"min_t": 1.0, "required_t_ok": 0, "required_avg_t": 0.0, "required_boot_ok": 0},
    # default: coherent family plus moderate statistical evidence
    "balanced": {"min_t": 2.0, "required_t_ok": 2, "required_avg_t": 1.5, "required_boot_ok": 1},
    # demo/production hardening: every horizon in the family needs evidence
    "strict": {"min_t": 2.0, "required_t_ok": 3, "required_avg_t": 2.0, "required_boot_ok": 3},
}


# --- ported PassFailCriteria (walk_forward_config.py) ---
@dataclass
class Thresholds:
    min_forward_pf: float = 1.0
    ideal_forward_pf: float = 1.3
    max_drawdown: float = 0.10
    min_trades_per_window: int = 20
    min_wfe: float = 0.5
    min_win_rate: float = 0.25
    max_mc_ruin: float = 0.15
    ideal_mc_ruin: float = 0.05
    min_recovery: float = 1.0
    ideal_recovery: float = 2.0
    # parameter stability
    min_profitable_fraction: float = 0.50   # params win in >50% of the grid
    max_pf_cv: float = 0.30                  # PF dispersion across the grid <= 30%
    # ADD (LdP)
    max_pbo: float = 0.50
    ideal_pbo: float = 0.20
    min_deflated_sharpe: float = 0.50
    ideal_deflated_sharpe: float = 0.70


@dataclass
class GauntletConfig:
    opt_months: int = 4
    fwd_months: int = 2
    step_months: int = 2
    min_windows: int = 4
    n_slices: int = 8          # CSCV time-slices for PBO
    mc_sims: int = 2000
    max_grid: int = 48         # DECISION: cap combos so the loop stays tractable
    exit_menu: bool = False    # v0.3 §5: run the staged first-pass SL×TP menu (Layer 2)
    signal_edge_mode: str = "balanced"  # loose | balanced | strict
    thresholds: Thresholds = None

    def __post_init__(self):
        if self.thresholds is None:
            self.thresholds = Thresholds()
        if self.signal_edge_mode not in L1_SIGNAL_EDGE_MODES:
            raise ValueError(f"unknown signal_edge_mode: {self.signal_edge_mode}")


def _add_months(dt: datetime, months: int) -> datetime:
    m = dt.month - 1 + months
    y = dt.year + m // 12
    m = m % 12 + 1
    d = min(dt.day, [31, 29 if y % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return dt.replace(year=y, month=m, day=d)


def calculate_windows(start: str, end: str, cfg: GauntletConfig) -> list[dict]:
    """Anchored walk-forward schedule (ported from WalkForwardSettings)."""
    s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    windows, cur, i = [], s, 0
    while True:
        opt_end = _add_months(cur, cfg.opt_months)
        fwd_end = _add_months(opt_end, cfg.fwd_months)
        if fwd_end > e:
            break
        windows.append({
            "opt": (cur.date().isoformat(), opt_end.date().isoformat()),
            "fwd": (opt_end.date().isoformat(), fwd_end.date().isoformat()),
        })
        cur = _add_months(cur, cfg.step_months)
        i += 1
    return windows


def context_trial_count(spec) -> int:
    """One base search plus each declared higher-timeframe feature binding."""
    return 1 + sum(
        len(features) for features in spec.alpha.higher_timeframe_features.values()
    )


def multiple_testing_trial_count(spec, parameter_trials: int, exit_trials: int) -> int:
    return max(parameter_trials * exit_trials * context_trial_count(spec), 1)


def _grid(spec, cap: int) -> list[dict]:
    """Cartesian product of param_space, capped (evenly sampled) to `cap`."""
    if not spec.param_space:
        return [{}]
    names = list(spec.param_space)
    combos = [dict(zip(names, vals)) for vals in product(*(spec.param_space[n] for n in names))]
    if len(combos) > cap:
        stride = len(combos) / cap
        combos = [combos[int(i * stride)] for i in range(cap)]
    return combos


def _pf(exp) -> float:
    pf = exp.raw_metrics.get("profit_factor", 0.0)
    return float(pf) if pf not in (float("inf"),) else 5.0  # cap inf PF for math


def _trades_r(exp) -> list[float]:
    return [t["pnl_r"] for t in exp.raw_metrics.get("trades", [])]


def _horizon_families(horizons: dict, mode: str = "balanced") -> dict:
    rules = L1_SIGNAL_EDGE_MODES[mode]
    families = {}
    for name, hs in L1_HORIZON_FAMILIES.items():
        fav = sum(horizons[h]["mean"] > 0 for h in hs)
        t_values = [horizons[h]["t"] for h in hs]
        avg_t = sum(t_values) / len(t_values)
        t_ok = sum(t >= rules["min_t"] for t in t_values)
        boot_ok = sum(horizons[h].get("boot_lo", 0.0) > 0 for h in hs)
        families[name] = {
            "horizons": list(hs),
            "mode": mode,
            "favourable": fav,
            "required_favourable": len(hs),
            "t_ok": t_ok,
            "required_t_ok": rules["required_t_ok"],
            "min_t": rules["min_t"],
            "avg_t": round(avg_t, 3),
            "required_avg_t": rules["required_avg_t"],
            "boot_ok": boot_ok,
            "required_boot_ok": rules["required_boot_ok"],
            "passed": (
                fav == len(hs)
                and t_ok >= rules["required_t_ok"]
                and avg_t >= rules["required_avg_t"]
                and boot_ok >= rules["required_boot_ok"]
            ),
        }
    return families


def _signal_edge(
    spec, bars, mode: str = "balanced",
    higher_timeframe_bars: dict | None = None,
) -> dict:
    """Layer-1 signal-edge gate (spec §3): exit-neutral. Sign-adjusted forward
    close-to-close return over L1_HORIZONS on bars where the compiled signal
    fires. Pass = one coherent horizon family is favourable (positive mean on
    every horizon in the family) and satisfies the configured strictness mode.
    A scalp need not survive 60 bars; a slower edge need not prove itself after
    3 bars."""
    bt = compile_to_backtest(spec)
    params = bt.default_params()
    env = bt._env(bars, higher_timeframe_bars)
    long_sig = eval_signal(spec.alpha.signal, env, params).to_numpy()
    short_sig = (eval_signal(spec.alpha.signal_short, env, params).to_numpy()
                 if spec.alpha.signal_short else np.zeros(len(bars), dtype=bool))
    d = np.where(long_sig, 1.0, np.where(short_sig, -1.0, 0.0))  # long precedence, as the compiler
    c = bars["close"].to_numpy(dtype=float)

    horizons, n_fav, n_t_ok = {}, 0, 0
    for h in L1_HORIZONS:
        if len(c) <= h:
            horizons[h] = {"n": 0, "mean": 0.0, "t": 0.0}
            continue
        fwd = (c[h:] - c[:-h]) / c[:-h]
        mask = d[:-h] != 0
        r = fwd[mask] * d[:-h][mask]  # sign-adjusted: shorts favour falling prices
        n = len(r)
        mu = float(r.mean()) if n else 0.0
        sd = float(r.std(ddof=1)) if n > 1 else 0.0
        raw_t = mu / (sd / sqrt(n)) if n > 1 and sd > 0 else 0.0
        # Overlapping windows: an h-bar forward return computed every bar shares
        # h-1 neighbours, so the plain t buys significance it has not earned.
        # The HAC t is the one the gate reads; raw_t stays for comparison.
        t_nw = newey_west_t(r, h - 1) if n > 2 else 0.0
        # ...and the plain t punishes fat right tails, the exact payoff shape
        # worth finding. A bootstrap lower bound asks only whether the mean is
        # reliably positive, with no normality assumption.
        boot_lo = block_bootstrap_lower(r, h, alpha=L1_BOOT_ALPHA) if n > 2 else 0.0
        horizons[h] = {
            "n": n, "n_eff": effective_n(n, h - 1),
            "mean": round(mu, 8), "t": round(t_nw, 3),
            "t_raw": round(raw_t, 3), "boot_lo": round(boot_lo, 10),
        }
        n_fav += mu > 0
        n_t_ok += t_nw >= L1_MIN_T

    families = _horizon_families(horizons, mode=mode)

    n_signals = int((d != 0).sum())
    passed_family = None
    if n_signals < L1_MIN_SIGNALS:
        passed, reason = False, f"signal fires only {n_signals}x < {L1_MIN_SIGNALS} (no evidence)"
    else:
        winners = [name for name, f in families.items() if f["passed"]]
        if winners:
            passed, reason, passed_family = True, "", winners[0]
        elif max(f["favourable"] for f in families.values()) < 3:
            passed = False
            reason = "no coherent horizon family: no 3-horizon cluster has favourable forward returns"
        else:
            best = max(families.items(), key=lambda kv: (kv[1]["favourable"], kv[1]["avg_t"], kv[1]["t_ok"]))
            f = best[1]
            passed = False
            needs = []
            if f["t_ok"] < f["required_t_ok"]:
                needs.append(f"t >= {f['min_t']} on {f['t_ok']}/{len(f['horizons'])} horizons")
            if f["avg_t"] < f["required_avg_t"]:
                needs.append(f"avg t {f['avg_t']:.2f} < required {f['required_avg_t']:.2f}")
            if f["boot_ok"] < f["required_boot_ok"]:
                needs.append(
                    f"bootstrap lower bound above zero on only "
                    f"{f['boot_ok']}/{len(f['horizons'])} horizons"
                )
            reason = f"weak signal edge in best family {best[0]} ({mode}): " + "; ".join(needs)
    return {"mode": mode, "horizons": {str(h): v for h, v in horizons.items()},
            "horizon_families": families,
            "passed_family": passed_family,
            "n_signals": n_signals, "min_signals": L1_MIN_SIGNALS,
            "passed": passed, "reason": reason}


# --- LAYER 2 (v0.3 §5): the first-pass exit menu ---
# 3 SL × (7 ratio RR + 2 structural TP) = 27 configs. Long-oriented anchors, but
# direction-SAFE: the compiler skips a level exit that lands on the wrong side of
# entry, so a short alpha naturally selects the atr/ratio configs (which are
# direction-agnostic). ponytail: structural exits for shorts (hi-anchored stops)
# need the M2 side-rule made direction-aware — wave 2. Also: `-spread` offset on
# the fractal stop is deferred (offset=0 keeps it scale-safe for now).
_MENU_SL = [
    ("atr1.5", StopRule(type="atr", mult=1.5)),
    ("swing_lo", StopRule(type="level", anchor="prior_swing_wick_lo")),
    ("fractal_lo", StopRule(type="level", anchor="prior_fractal_lo")),
]
_MENU_RR = (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 10.0)  # spec §5 — coarse, each is a DSR trial
_MENU_TP = [(f"rr{r}", TargetRule(type="ratio", ratio=r)) for r in _MENU_RR] + [
    ("swing_hi", TargetRule(type="level", anchor="prior_swing_wick_hi")),
    ("session_hi", TargetRule(type="level", anchor="session_ny_hi")),
]


_PARAM_REF_RE = re.compile(r"p\[(\w+)\]")


def _exit_menu() -> list[tuple[str, StopRule, TargetRule]]:
    return [(f"{sl}/{tp}", s, t) for sl, s in _MENU_SL for tp, t in _MENU_TP]


def _menu_variant(spec, stop: StopRule, tgt: TargetRule):
    """Swap the wrapper's stop/target for a menu config and PRUNE param_space keys
    that only the old exit referenced (the menu owns the exit now) — otherwise the
    orphaned p[...] entries fail validation."""
    w = spec.wrapper.model_copy(update={"stop": stop, "target": tgt})
    fields = [spec.alpha.signal, spec.alpha.signal_short or "",
              *(str(x) for x in (w.stop.mult, w.stop.offset, w.target.ratio, w.target.offset,
                                 w.trail.mult, w.break_even.trigger_r) if isinstance(x, str))]
    refs = set(_PARAM_REF_RE.findall(" ".join(fields)))
    ps = {k: v for k, v in spec.param_space.items() if k in refs}
    return spec.model_copy(update={"id": spec.id, "wrapper": w, "param_space": ps})


def _select_exit_config(spec, run_fn, period):
    """Run the alpha across the 27-config menu (one full-period backtest each) and
    return (best-config spec variant, diagnostics, n_configs). Best = highest
    expected edge (PF-1)×trades — opportunity-aware, so it discovers the alpha's
    natural exit regime (a runner vs a scalp) instead of assuming one."""
    ranked = []
    best = None  # (edge, label, variant)
    for label, stop, tgt in _exit_menu():
        variant = _menu_variant(spec, stop, tgt)
        exp = run_fn(variant, period=period, params=None)
        edge = round(calculate_edge(_pf(exp), exp.trades_n), 2)
        ranked.append((edge, label, exp.trades_n))
        if best is None or edge > best[0]:
            best = (edge, label, variant)
    ranked.sort(reverse=True)
    diag = {
        "n_configs": len(ranked), "best": best[1], "best_edge": best[0],
        "ranked": [{"config": l, "edge": e, "trades": n} for e, l, n in ranked[:5]],
        "passed": True,
    }
    return best[2], diag, len(ranked)


def judge(spec, run_fn=run, period: tuple[str, str] | None = None,
          cfg: GauntletConfig | None = None, mem=None, bars=None,
          costs: dict | None = None,
          higher_timeframe_bars: dict | None = None) -> tuple[Scorecard, Verdict]:
    """Run the gauntlet. Returns (Scorecard, Verdict). If `mem`, persists the
    full-period anchor experiment (its id anchors experiment_id on both).
    Scorecard/Verdict persistence is the caller's job — the research loop
    completes the map-derived fields (novelty/correlation/...) before storing.
    `bars` feeds the Layer-1/Layer-2 stages; auto-loaded for the real runner,
    injectable for offline tests, and both stages skip cleanly without it."""
    cfg = cfg or GauntletConfig()
    real_runner = run_fn is run
    # costs (None = frictionless) bound onto every run_fn call — re-run the whole
    # gauntlet with friction on to stress-test edges that passed clean.
    if costs is not None:
        run_fn = partial(run_fn, costs=costs)
    t = cfg.thresholds
    reasons: list[str] = []
    diag: dict = {}  # per-gate glass box — every computed value + its threshold
    context_trials = context_trial_count(spec)

    # bars power Layer 1/2 and period discovery; only the real runner can load them
    if bars is None and (period is None or real_runner):
        s_, e_ = period or (None, None)
        bars = load_bars(spec.context.universe[0], _mt5_tf(spec.context.timeframe), s_, e_)
    if (
        higher_timeframe_bars is None
        and spec.alpha.higher_timeframe_features
        and real_runner
    ):
        _, e_ = period or (None, None)
        higher_timeframe_bars = load_higher_timeframe_bars(
            spec.context.universe[0], spec.alpha.higher_timeframe_features, end=e_,
        )
    if period is None:
        period = (bars["time"].iloc[0].date().isoformat(), bars["time"].iloc[-1].date().isoformat())
    start, end = period
    grid = _grid(spec, cfg.max_grid)
    n_menu = 1  # exit-menu breadth → inflates the DSR trial count (v0.3 §6)

    # anchor experiment: full-period run at default params. Stored LAZILY so the
    # Layer-2 menu can rebind the spec first — the persisted experiment then
    # reflects the CHOSEN exit config, not the pre-menu default.
    full = run_fn(spec, period=period, params=None)
    exp_id = ""

    def _store_anchor():
        nonlocal full, exp_id
        if mem is not None and full.id is None:
            full = mem.add_experiment(full)
        exp_id = full.id or ""

    def scorecard(robustness, param_stability, dsr, pbo) -> Scorecard:
        return Scorecard(
            experiment_id=exp_id,
            robustness=round(robustness, 3),
            parameter_stability=(
                round(param_stability, 3) if param_stability is not None else None
            ),
            deflated_sharpe=round(dsr, 3),
            pbo=round(pbo, 3),
            diagnostics=diag,  # whatever gates ran up to this return path
        )

    def verdict(vtype, sc) -> tuple[Scorecard, Verdict]:
        v = Verdict(hypothesis_id=spec.hypothesis_id, experiment_id=exp_id, type=vtype, reasons=reasons)
        return sc, v

    # --- LAYER 1 (v0.3 §3): signal-edge gate — exit-neutral, kills junk before
    # anything expensive. Skipped (recorded) when no bars are available. ---
    if bars is not None:
        se = _signal_edge(
            spec, bars, mode=cfg.signal_edge_mode,
            higher_timeframe_bars=higher_timeframe_bars,
        )
        diag["gate_signal_edge"] = se
        if not se["passed"]:
            reasons.append(f"no signal edge: {se['reason']}")
            _store_anchor()
            return verdict(VerdictType.KILL, scorecard(0, 0, 0, 1.0))
    else:
        diag["gate_signal_edge"] = {"passed": None, "skipped": "no bars (injected run_fn)"}

    # --- LAYER 2 (v0.3 §5): staged first-pass exit menu on Layer-1 survivors.
    # Picks the alpha's best exit config; the breadth inflates DSR n_trials. ---
    if cfg.exit_menu:
        spec, diag["exit_menu"], n_menu = _select_exit_config(spec, run_fn, period)
        grid = _grid(spec, cfg.max_grid)  # exit params pruned → re-derive the grid
        full = run_fn(spec, period=period, params=None)  # anchor = the chosen config

    _store_anchor()

    # --- GATE 0: leakage / look-ahead ---
    leaked = _detect_lookahead(spec, start, end, real_runner)
    diag["gate0_leakage"] = {"passed": not leaked}
    if leaked:
        reasons.append("leakage: look-ahead detected (future bars change past trades)")
        return verdict(VerdictType.KILL, scorecard(0, 0, 0, 1.0))

    # --- GATE 1: walk-forward OOS ---
    windows = calculate_windows(start, end, cfg)
    if len(windows) < cfg.min_windows:
        reasons.append(f"insufficient data: {len(windows)} WF windows < {cfg.min_windows}")
        diag["gate1_walkforward"] = {
            "windows": [], "n_windows": len(windows), "min_windows": cfg.min_windows,
            "passed": False,
        }
        return verdict(VerdictType.KILL, scorecard(0, 0, 0, 1.0))

    fwd_trades: list[float] = []
    fwd_pfs, is_pfs, win_ok = [], [], 0
    win_diag: list[dict] = []
    for w in windows:
        # optimise IS: pick best param by PF on the optimisation slice
        is_runs = [(c, run_fn(spec, period=w["opt"], params=c)) for c in grid]
        best_c, best_is = max(is_runs, key=lambda cr: _pf(cr[1]))
        oos = run_fn(spec, period=w["fwd"], params=best_c)
        is_pfs.append(_pf(best_is))
        fwd_pfs.append(_pf(oos))
        fwd_trades += _trades_r(oos)
        if oos.trades_n >= 1 and _pf(oos) > 1.0:
            win_ok += 1
        win_diag.append({"opt": list(w["opt"]), "fwd": list(w["fwd"]),
                         "is_pf": round(_pf(best_is), 4), "fwd_pf": round(_pf(oos), 4)})

    combined_fwd_pf = _pf_from_r(fwd_trades)
    wfe = (mean(fwd_pfs) / mean(is_pfs)) if is_pfs and mean(is_pfs) > 0 else 0.0
    window_consistency = win_ok / len(windows)
    avg_fwd_trades = len(fwd_trades) / len(windows)

    n_before = len(reasons)
    if avg_fwd_trades < t.min_trades_per_window:
        reasons.append(f"forward trades/window {avg_fwd_trades:.0f} < {t.min_trades_per_window}")
    if combined_fwd_pf < t.min_forward_pf:
        reasons.append(f"forward PF {combined_fwd_pf:.3f} < {t.min_forward_pf}")
    if wfe < t.min_wfe:
        reasons.append(f"walk-forward efficiency {wfe:.2f} < {t.min_wfe}")
    diag["gate1_walkforward"] = {
        "windows": win_diag,
        "wfe": round(wfe, 4), "wfe_threshold": t.min_wfe,
        "window_consistency": round(window_consistency, 4),
        "avg_fwd_trades": round(avg_fwd_trades, 2), "min_trades": t.min_trades_per_window,
        "combined_fwd_pf": round(combined_fwd_pf, 4), "min_forward_pf": t.min_forward_pf,
        "passed": len(reasons) == n_before,
    }

    # --- GATE 2: parameter-neighbourhood robustness (over full period) ---
    grid_pfs = [_pf(run_fn(spec, period=period, params=c)) for c in grid]
    # A single-point grid has no neighbourhood. profitable_frac is then 1.0 or 0.0
    # and always clears the 50% bar, so the gate measures NOTHING -- it must say so
    # rather than pass quietly with a 0.0 score that reads as "maximally unstable".
    measured = len(grid_pfs) > 1
    profitable_frac = mean(1.0 if p > 1.0 else 0.0 for p in grid_pfs)
    pf_cv = (pstdev(grid_pfs) / mean(grid_pfs)) if measured and mean(grid_pfs) > 0 else None
    param_stability = (
        max(0.0, min(profitable_frac, 1.0 - min(pf_cv, 1.0))) if pf_cv is not None else None
    )
    n_before = len(reasons)
    if not measured:
        reasons.append(
            "parameter robustness not measured: the whole param grid is a single point, "
            "so the neighbourhood test had nothing to vary"
        )
    elif profitable_frac < t.min_profitable_fraction:
        reasons.append(f"only {profitable_frac:.0%} of param grid profitable < {t.min_profitable_fraction:.0%}")
    if pf_cv is not None and pf_cv > t.max_pf_cv:
        reasons.append(f"PF dispersion across grid {pf_cv:.0%} > {t.max_pf_cv:.0%} (knife-edge)")
    diag["gate2_robustness"] = {
        "profitable_frac": round(profitable_frac, 4), "min_profitable_fraction": t.min_profitable_fraction,
        "pf_cv": round(pf_cv, 4) if pf_cv is not None else None,
        "max_pf_cv": t.max_pf_cv, "measured": measured,
        "grid_pfs": [round(p, 4) for p in grid_pfs],
        "passed": len(reasons) == n_before,
    }

    # --- GATE 3: Monte Carlo ruin (on the forward OOS trades) ---
    mc = monte_carlo_ruin(fwd_trades, n_sims=cfg.mc_sims, ruin_dd=t.max_drawdown,
                          risk_frac=spec.wrapper.sizing.risk_pct / 100.0)
    if mc["p_ruin"] > t.max_mc_ruin:
        reasons.append(f"P(ruin) {mc['p_ruin']:.0%} > {t.max_mc_ruin:.0%}")
    diag["gate3_mc_ruin"] = {
        "p_ruin": round(mc["p_ruin"], 4), "p_profit": round(mc["p_profit"], 4),
        "dd_mean": round(mc["dd_mean"], 4), "dd_95": round(mc["dd_95"], 4),
        "dd_worst": round(mc["dd_worst"], 4),
        "max_mc_ruin": t.max_mc_ruin, "ruin_dd": t.max_drawdown,
        "passed": mc["p_ruin"] <= t.max_mc_ruin,
    }

    # --- GATE 4: multiple-testing correction (Deflated Sharpe + PBO) ---
    perf_matrix = _perf_matrix(spec, grid, start, end, cfg, run_fn)
    pbo = pbo_cscv(perf_matrix, s=cfg.n_slices)
    # per-combo Sharpe dispersion feeds the DSR benchmark (breadth of the search)
    combo_sharpes = [_sharpe_of(run_fn(spec, period=period, params=c)) for c in grid]
    sr_std = pstdev(combo_sharpes) if len(combo_sharpes) > 1 else 0.0
    # Parameter, exit-menu and context searches all raise the multiple-testing bar.
    n_trials = multiple_testing_trial_count(spec, len(grid), n_menu)
    dsr = deflated_sharpe(fwd_trades, n_trials=n_trials, sr_std=sr_std)

    n_before = len(reasons)
    if pbo > t.max_pbo:
        reasons.append(f"PBO {pbo:.0%} > {t.max_pbo:.0%} (overfit)")
    if dsr < t.min_deflated_sharpe:
        reasons.append(f"deflated Sharpe {dsr:.2f} < {t.min_deflated_sharpe} (not distinguishable from noise)")
    diag["gate4_multiple_testing"] = {
        "pbo": round(pbo, 4), "max_pbo": t.max_pbo,
        "dsr": round(dsr, 4), "min_deflated_sharpe": t.min_deflated_sharpe,
        "sr_std": round(sr_std, 4), "n_trials": n_trials,
        "context_trials": context_trials,
        "passed": len(reasons) == n_before,
    }

    sc = scorecard(window_consistency, param_stability, dsr, pbo)

    if reasons:
        return verdict(VerdictType.KILL, sc)

    edge = calculate_edge(combined_fwd_pf, len(fwd_trades))
    promote = (
        combined_fwd_pf >= t.ideal_forward_pf and pbo <= t.ideal_pbo
        and dsr >= t.ideal_deflated_sharpe and mc["p_ruin"] <= t.ideal_mc_ruin
        and edge > 0
    )
    return verdict(VerdictType.PROMOTE if promote else VerdictType.SURVIVE, sc)


# --- helpers ---
def _mt5_tf(tf: str) -> str:
    tf = tf.strip()
    return f"M{tf[:-1]}" if tf[:-1].isdigit() and tf[-1].lower() == "m" else tf.upper()


def _pf_from_r(rs: list[float]) -> float:
    gp = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r < 0))
    return gp / gl if gl else (5.0 if gp else 0.0)


def _sharpe_of(exp) -> float:
    return float(exp.raw_metrics.get("sharpe", 0.0))


def _perf_matrix(spec, grid, start, end, cfg, run_fn) -> list[list[float]]:
    """rows = contiguous date slices, cols = param combos, value = mean trade R."""
    s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    total_days = (e - s).days
    bounds = [s + (e - s) * i // cfg.n_slices for i in range(cfg.n_slices + 1)] if total_days else [s, e]
    matrix = []
    for i in range(cfg.n_slices):
        sl = (bounds[i].date().isoformat(), bounds[i + 1].date().isoformat())
        row = []
        for c in grid:
            rs = _trades_r(run_fn(spec, period=sl, params=c))
            row.append(mean(rs) if rs else 0.0)
        matrix.append(row)
    return matrix


def _detect_lookahead(spec, start, end, real_runner: bool) -> bool:
    """Leakage gate: a causal strategy's trades in the first half of the data
    must NOT change when the second half is unknown. We compare trades produced
    on the full period against those on the first-half period; any trade that
    opened and closed inside the first half must be identical. If a feature peeks
    forward, truncating the future perturbs those early trades.

    Uses compile_to_backtest directly on real bars (not run_fn) so it always
    tests the actual engine; skipped when run_fn is injected for unit tests."""
    if not real_runner:  # injected fake runner has no real bars to truncate
        return False
    symbol = spec.context.universe[0]
    bars = load_bars(symbol, _mt5_tf(spec.context.timeframe), start, end)
    if len(bars) < 200:
        return False
    higher = load_higher_timeframe_bars(
        symbol, spec.alpha.higher_timeframe_features, end=end,
    )
    half = len(bars) // 2
    truncated_bars = bars.iloc[:half].reset_index(drop=True)
    cutoff = (
        pd.Timestamp(truncated_bars["time"].iloc[-1])
        + pd.Timedelta(minutes=timeframe_minutes(spec.context.timeframe))
    )
    truncated_higher = {
        timeframe: frame[
            pd.to_datetime(frame["time"], utc=True)
            + pd.Timedelta(minutes=timeframe_minutes(timeframe)) <= cutoff
        ].reset_index(drop=True)
        for timeframe, frame in higher.items()
    }
    bt = compile_to_backtest(spec)
    full = bt(bars, higher_timeframe_bars=higher)["trades"]
    trunc = bt(truncated_bars, higher_timeframe_bars=truncated_higher)["trades"]
    # The truncated engine force-closes an open trade at its final bar; that
    # synthetic boundary close is not evidence of look-ahead.
    early_full = [
        (t["entry_i"], t["exit_i"], t["pnl_r"]) for t in full if t["exit_i"] < half - 1
    ]
    early_trunc = [
        (t["entry_i"], t["exit_i"], t["pnl_r"])
        for t in trunc if t["exit_i"] < half - 1
    ]
    return early_full != early_trunc
