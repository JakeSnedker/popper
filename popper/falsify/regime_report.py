"""Per-regime performance breakdown (Phase C2 of SELF_LEARNING_ROADMAP).

Buckets trades by the regime label at their ENTRY bar (entry_i is positional,
matching the compiler's trade dicts; regime labels are already PIT-correct, so
bucketing by entry bar is causal). An edge that only works in one regime gets
TAGGED here — the caller decides what to do with that, nothing is discarded.

Metric definitions per bucket match experiments/runner.compute_metrics exactly
(expectancy = win_rate*avg_win - loss_rate*avg_loss, exact-0 R counts as
neither win nor loss, profit_factor = inf when no losses but some profit).

Standalone reporting only — not wired into the Scorecard/runner yet.
"""

import math

import numpy as np

from popper.dsl.features.regime import (
    TREND_LABELS,
    VOL_LABELS,
    trend_regime,
    vol_regime,
)

UNKNOWN = "unknown"  # entry bar before the regime's rolling window has formed


def _bucket_metrics(rs: list) -> dict:
    """Same formulas as runner.compute_metrics, on a bucket's R-multiples."""
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    win_rate = len(wins) / n if n else 0.0
    loss_rate = len(losses) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    expectancy = win_rate * avg_win - loss_rate * avg_loss
    profit_factor = gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0)
    return {
        "n_trades": n,
        "expectancy": round(expectancy, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4) if math.isfinite(profit_factor) else profit_factor,
    }


def _labels_at_entries(codes: np.ndarray, labels: dict, entries: list) -> list:
    return [UNKNOWN if np.isnan(codes[i]) else labels[codes[i]] for i in entries]


def _group(trades: list, keys: list) -> dict:
    buckets: dict = {}
    for t, k in zip(trades, keys):
        buckets.setdefault(k, []).append(t["pnl_r"])
    return {k: _bucket_metrics(rs) for k, rs in buckets.items()}


def _strongest_weakest(buckets: dict) -> dict:
    known = {k: m for k, m in buckets.items() if k != UNKNOWN and m["n_trades"] > 0}
    if not known:
        return {"strongest": None, "weakest": None}
    return {
        "strongest": max(known, key=lambda k: known[k]["expectancy"]),
        "weakest": min(known, key=lambda k: known[k]["expectancy"]),
    }


def _concentration(buckets: dict) -> float:
    """regime_concentration in [0, 1]: how much of the edge's positive
    expectancy sits in a single regime bucket.

    FORMULA: contribution_b = max(expectancy_b, 0) * n_trades_b  (total positive
    R attributable to bucket b); concentration = max_b(contribution) / sum_b(contribution).
    1.0 = all positive expectancy in one regime (fragile); ~1/k across k
    positive buckets = spread (robust); 0.0 = no bucket has positive expectancy.
    Unknown-regime trades are excluded — they carry no regime information.
    """
    contribs = [m["expectancy"] * m["n_trades"]
                for k, m in buckets.items() if k != UNKNOWN and m["expectancy"] > 0]
    total = sum(contribs)
    return round(max(contribs) / total, 4) if total > 0 else 0.0


def regime_report(trades: list, bars) -> dict:
    """Per-regime breakdown of a trade list.

    trades: compiler trade dicts (needs entry_i + pnl_r). bars: OHLC DataFrame
    the trades were generated on (regime labels are recomputed from it).
    """
    vol_codes = vol_regime(bars).to_numpy()
    trend_codes = trend_regime(bars).to_numpy()
    entries = [t["entry_i"] for t in trades]
    vol_keys = _labels_at_entries(vol_codes, VOL_LABELS, entries)
    trend_keys = _labels_at_entries(trend_codes, TREND_LABELS, entries)

    vol_buckets = _group(trades, vol_keys)
    trend_buckets = _group(trades, trend_keys)
    combo_keys = [f"{v}/{t}" if UNKNOWN not in (v, t) else UNKNOWN
                  for v, t in zip(vol_keys, trend_keys)]
    combo_buckets = _group(trades, combo_keys)

    return {
        "n_trades": len(trades),
        "vol": vol_buckets,
        "trend": trend_buckets,
        "vol_x_trend": combo_buckets,
        "summary": {
            "vol": _strongest_weakest(vol_buckets),
            "trend": _strongest_weakest(trend_buckets),
            # concentration on the combined view — each trade in exactly one bucket
            "regime_concentration": _concentration(combo_buckets),
        },
    }
