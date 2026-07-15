"""Portfolio constructor + deployable book (docs/SELF_LEARNING_ROADMAP.md D2/D3).

The single-edge gauntlet asks "is this alpha promotable on its own?" — too strict
for a book: a thin edge that can't stand alone still earns a slot if it's
net-of-cost positive, robust, and UNCORRELATED with what's already in. So D2 uses
a DIFFERENT gate than single-edge promotion:

  - **Friction HARD-GATE (the promotion friction requirement lives HERE):** an edge
    is eligible only if `expectancy_net > 0`. No cost model ran (net absent) ->
    ineligible; a deployable book never guesses at cost survival.
  - **Robust:** pbo <= max_pbo and parameter_stability >= min_profitable_fraction
    (reused from the gauntlet thresholds — no overfit edges in the book).

Then greedy selection: take the best net edge, keep adding the next-best whose
signed correlation to EVERY selected member stays <= max_corr (D1's return streams).
Weights = inverse-vol, normalised. Pure functions; the caller supplies candidates
pulled from the map. Output (D3) = edges + weights + regime rules + combined stats.
"""

import numpy as np

from popper.falsify import GauntletConfig
from popper.portfolio.correlation import _pearson, return_stream


def _eligible(c: dict, cfg: GauntletConfig) -> tuple[bool, str]:
    """Friction HARD-GATE + robustness. Returns (ok, reason-if-not)."""
    t = cfg.thresholds
    net = c["metrics"].get("expectancy_net")
    if net is None:
        return False, "no net-of-cost metric (cost survival unverified)"
    if net <= 0:
        return False, f"friction: expectancy_net {net} <= 0"
    sc = c.get("scorecard") or {}
    pbo = sc.get("pbo")
    stab = sc.get("parameter_stability")
    if pbo is not None and pbo > t.max_pbo:
        return False, f"overfit: pbo {pbo} > {t.max_pbo}"
    if stab is not None and stab < t.min_profitable_fraction:
        return False, f"overfit: parameter_stability {stab} < {t.min_profitable_fraction}"
    return True, "ok"


def build_book(candidates: list[dict], cfg: GauntletConfig | None = None,
               max_corr: float = 0.5) -> dict:
    """D2 + D3. `candidates` each: {spec_id, trades, n_bars, metrics{expectancy_net,..},
    scorecard{pbo,parameter_stability}, regime?}. Returns the deployable book."""
    cfg = cfg or GauntletConfig()
    eligible, rejected = [], []
    for c in candidates:
        ok, why = _eligible(c, cfg)
        (eligible if ok else rejected).append(
            c if ok else {"spec_id": c.get("spec_id"), "reason": why})

    # best net edge first; greedily add the next whose signed corr to EVERY
    # selected member stays within max_corr (one redundant overlap disqualifies).
    eligible.sort(key=lambda c: c["metrics"]["expectancy_net"], reverse=True)
    selected: list[dict] = []
    streams: list[np.ndarray] = []
    for c in eligible:
        s = return_stream(c["trades"], c["n_bars"])
        if all(_pearson(s, prev) <= max_corr for prev in streams):
            selected.append(c)
            streams.append(s)
        else:
            rejected.append({"spec_id": c.get("spec_id"),
                             "reason": f"correlated > {max_corr} with a selected edge"})

    # inverse-vol weights (higher-variance edges get less), normalised. Flat edge -> 0.
    vols = np.array([np.std(s) or np.inf for s in streams])
    inv = 1.0 / vols
    weights = (inv / inv.sum()).tolist() if inv.sum() else [0.0] * len(streams)

    edges = [{
        "spec_id": c.get("spec_id"),
        "weight": round(w, 4),
        "expectancy_net": c["metrics"]["expectancy_net"],
        "regime": c.get("regime"),  # D3 regime rules: where each edge is meant to run
    } for c, w in zip(selected, weights)]

    combined = _combined(streams, weights)
    return {"edges": edges, "combined": combined, "rejected": rejected,
            "n_candidates": len(candidates), "n_selected": len(selected)}


def candidates_from_mem(mem, hypothesis_ids: list[str] | None = None) -> list[dict]:
    """Pull book candidates from the map: one per stored (spec, experiment). The
    friction gate in build_book sorts them out — a cost-killed original fails on
    net<=0, its rescued `_r` variant passes. ponytail: assumes all edges ran on the
    SAME period so exit_i aligns; correct for a single-instrument run, revisit if
    a book ever spans periods."""
    hids = hypothesis_ids or [h.id for h in mem.list_hypotheses()]
    out = []
    for hid in hids:
        rec = mem.record(hid)
        sc_by_exp = {sc["experiment_id"]: sc for sc in rec.get("scorecards", [])}
        for e in rec.get("experiments", []):
            m = e.get("raw_metrics", {})
            trades = m.get("trades", [])
            if not trades:
                continue
            sc = sc_by_exp.get(e["id"], {})
            out.append({
                "spec_id": e["spec_id"],
                "trades": trades,
                "n_bars": len(e.get("equity_curve") or []) or max(t["exit_i"] for t in trades) + 1,
                "metrics": {"expectancy_net": m.get("expectancy_net"), "expectancy": m.get("expectancy")},
                "scorecard": {"pbo": sc.get("pbo"), "parameter_stability": sc.get("parameter_stability")},
            })
    return out


def book_from_mem(mem, cfg: GauntletConfig | None = None, max_corr: float = 0.5,
                  hypothesis_ids: list[str] | None = None) -> dict:
    """The deployable book for a run: candidates pulled from the map -> build_book."""
    return build_book(candidates_from_mem(mem, hypothesis_ids), cfg=cfg, max_corr=max_corr)


def _combined(streams: list[np.ndarray], weights: list[float]) -> dict:
    """Weighted book return stream -> combined per-bar stats + worst in-book corr."""
    if not streams:
        return {"expectancy_per_bar": 0.0, "sharpe_per_bar": 0.0, "max_pair_corr": 0.0}
    book = np.zeros_like(streams[0], dtype=float)
    for s, w in zip(streams, weights):
        book += w * s
    mean, std = float(book.mean()), float(book.std())
    pair = [ _pearson(streams[i], streams[j])
             for i in range(len(streams)) for j in range(i + 1, len(streams)) ]
    return {"expectancy_per_bar": round(mean, 6),
            "sharpe_per_bar": round(mean / std, 4) if std else 0.0,
            "max_pair_corr": round(max(pair), 4) if pair else 0.0}


if __name__ == "__main__":  # ponytail self-check: friction gate + de-correlation
    def cand(sid, net, pbo, stab, trades, n=10):
        return {"spec_id": sid, "metrics": {"expectancy_net": net},
                "scorecard": {"pbo": pbo, "parameter_stability": stab},
                "trades": trades, "n_bars": n}
    A = [{"exit_i": i, "pnl_r": 1.0} for i in range(5)]          # edge A
    Ac = [{"exit_i": i, "pnl_r": 1.0} for i in range(5)]          # clone of A (corr 1)
    B = [{"exit_i": i, "pnl_r": (-1.0 if i % 2 else 1.0)} for i in range(5)]  # different
    book = build_book([
        cand("A", 0.15, 0.3, 0.9, A),
        cand("clone", 0.10, 0.3, 0.9, Ac),   # eligible but correlated -> dropped
        cand("B", 0.12, 0.3, 0.9, B),        # uncorrelated -> kept
        cand("costneg", -0.05, 0.3, 0.9, A), # friction hard-gate -> rejected
        cand("overfit", 0.20, 0.9, 0.9, A),  # pbo blown -> rejected
        {"spec_id": "nonet", "metrics": {}, "scorecard": {}, "trades": A, "n_bars": 10},
    ])
    ids = [e["spec_id"] for e in book["edges"]]
    assert ids == ["A", "B"], ids                       # A + uncorrelated B, clone dropped
    assert abs(sum(e["weight"] for e in book["edges"]) - 1.0) < 1e-9
    rej = {r["spec_id"] for r in book["rejected"]}
    assert {"costneg", "overfit", "nonet", "clone"} <= rej, rej
    print("OK", book["n_selected"], "selected;", book["combined"])
