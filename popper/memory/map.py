"""Faceted categorisation + post-test behaviour + Scorecard map-metrics (spec/04).

Features first (pre-test) -> behaviour corrects (post-test): the only causally
possible order. Categories are points in tag-space; whitespace = sparse facet
combinations. A new category is warranted when nearest similarity < NEW_CATEGORY.
"""

from statistics import fmean, pstdev

# tunable thresholds (spec/04)
NEW_CATEGORY = 0.70   # nearest cosine below this -> spawn a new category
STALE = 0.90          # nearest cosine at/above this -> re-exploration (stale)

_EDGE_KEYWORDS = {
    "mean_reversion": ("revert", "mean", "reversion", "reversal", "vwap", "fade", "sweep"),
    "breakout": ("break", "breakout", "opening", "range", "gap", "expansion"),
    "momentum": ("momentum", "trend", "continue", "drive", "persist"),
    "liquidity": ("liquidity", "stop-run", "stop run", "order", "imbalance"),
}

# the known edge-type vocabulary — the map's coverage axis (spec/02 whitespace)
EDGE_TYPES = tuple(_EDGE_KEYWORDS)


def edge_type(hypothesis) -> str:
    """Pre-test facet from the claim/features. Provisional — behaviour may relocate."""
    text = f"{hypothesis.claim} {' '.join(hypothesis.required_features)}".lower()
    for etype, words in _EDGE_KEYWORDS.items():
        if any(w in text for w in words):
            return etype
    return "unknown"


def context_tag(spec) -> str:
    return f"{spec.context.universe[0]}:{spec.context.timeframe}"


def categorize(hypothesis, spec=None) -> dict:
    """Pre-test faceted tags: edge_type (from alpha) + context (from spec)."""
    tags = {"edge_type": edge_type(hypothesis)}
    if spec is not None:
        tags["context"] = context_tag(spec)
    return tags


def behaviour_signature(experiment) -> dict:
    """Post-test facet: how the alpha actually behaved (when it makes/loses money)."""
    m = experiment.raw_metrics
    wr = m.get("win_rate", 0.0)
    return {
        "profitable": m.get("pnl", 0.0) > 0,
        "win_rate_band": "high" if wr >= 0.5 else "mid" if wr >= 0.33 else "low",
        "avg_R": round(m.get("expectancy", 0.0), 3),
        "regime_stable": regime_stability(_trades_r(experiment)) >= 0.5,
    }


def _trades_r(experiment) -> list[float]:
    return [t["pnl_r"] for t in experiment.raw_metrics.get("trades", [])]


def regime_stability(trades_r: list[float], k: int = 4) -> float:
    """v0: split trades into k ordered chunks, fraction of chunks with PF>1.
    A steady edge scores ~1.0; a lumpy one (all profit in one period) scores low."""
    if len(trades_r) < k:
        return 0.0
    size = len(trades_r) // k
    ok = 0
    for i in range(k):
        chunk = trades_r[i * size:(i + 1) * size] if i < k - 1 else trades_r[i * size:]
        gp = sum(r for r in chunk if r > 0)
        gl = abs(sum(r for r in chunk if r < 0))
        if gp > gl:
            ok += 1
    return ok / k


def _resample(curve: list[float], n: int = 50) -> list[float]:
    if len(curve) < 2:
        return [0.0] * n
    out = []
    for i in range(n):
        pos = i * (len(curve) - 1) / (n - 1)
        lo = int(pos)
        hi = min(lo + 1, len(curve) - 1)
        out.append(curve[lo] + (curve[hi] - curve[lo]) * (pos - lo))
    return out


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = fmean(a), fmean(b)
    sa, sb = pstdev(a), pstdev(b)
    if sa == 0 or sb == 0:
        return 0.0
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
    return cov / (sa * sb)


def correlation_to_pool(equity_curve: list[float], pool_curves: list[list[float]]) -> float:
    """Max |Pearson correlation| of this equity curve against each validated-pool
    curve (resampled to a common length). 0.0 if the pool is empty -> maximally
    diversifying by default. The portfolio engine (late phase) minimises this."""
    if not pool_curves:
        return 0.0
    mine = _resample(equity_curve)
    return round(max(abs(_pearson(mine, _resample(c))) for c in pool_curves), 4)


def complete_scorecard(sc, mem, hypothesis, experiment, *, bars=None):
    """Fill the map-derived Scorecard fields in place: novelty (distance to the
    nearest prior alpha), correlation (vs the validated pool), regime_stability,
    behaviour_signature. Returns the same Scorecard.

    bars: optional OHLC DataFrame the trades ran on. When given, regime_stability
    = 1 - regime_concentration from the REAL vol/trend regime report (C3);
    without it, the crude temporal-chunk v0 stands. Non-gating either way."""
    sc.novelty = round(mem.novelty(hypothesis), 4)
    sc.regime_stability = _regime_stability_score(experiment, bars)
    sc.correlation = correlation_to_pool(experiment.equity_curve, mem.validated_equity_curves())
    sc.behaviour_signature = behaviour_signature(experiment)
    return sc


def _regime_stability_score(experiment, bars) -> float:
    """Real-regime stability when bars are available, crude v0 otherwise."""
    trades = experiment.raw_metrics.get("trades", [])
    if bars is not None and trades:
        try:
            from popper.falsify.regime_report import regime_report  # local: avoid import cycle
            conc = regime_report(trades, bars)["summary"]["regime_concentration"]
            if conc > 0.0:  # 0.0 = no positive bucket -> nothing to spread -> degenerate
                return round(1.0 - conc, 4)
        except (KeyError, TypeError):
            pass  # malformed trades/report -> crude fallback below
    # ponytail: crude temporal-chunk v0 stays as the no-bars/degenerate fallback
    return round(regime_stability(_trades_r(experiment)), 4)
