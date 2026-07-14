"""Near-miss cluster selection (docs/SELF_LEARNING_ROADMAP.md B2).

The exploit engine refines survivors; B2 also feeds it KILLED edges worth
rescuing. Top tier of the rescued: COST-KILLED edges (positive gross
expectancy, wiped out only by friction) — the optimizer's objective is
expectancy_net, so widening stops/RR can often recover them. Second tier:
near-misses that sit within a band of the PBO / parameter-stability gates.
Pure functions, no I/O — the Director calls these from its exploit loop.
"""

from popper.falsify import GauntletConfig

SURVIVE = ("survive", "promote")
TIERS = ("survivor", "cost_killed", "near_miss")


def classify(metrics, verdict, reasons=(), scorecard=None, cfg=None, band=0.1) -> str:
    """One of 'survivor' | 'cost_killed' | 'near_miss' | 'dead'.

    cost_killed = real gross edge, killed only by cost (the prime rescue).
    near_miss = failed but within `band` of BOTH the PBO and stability gates.
    """
    if verdict in SURVIVE:
        return "survivor"
    if (metrics.get("expectancy", 0) > 0
            and "expectancy_net" in metrics and metrics["expectancy_net"] <= 0):
        return "cost_killed"
    if scorecard is not None:
        t = (cfg or GauntletConfig()).thresholds
        if (scorecard.pbo <= t.max_pbo + band
                and scorecard.parameter_stability >= t.min_profitable_fraction - band):
            return "near_miss"
    return "dead"


def rank_targets(candidates, cfg=None, band=0.1, limit=None) -> list:
    """Order refine targets: survivor -> cost_killed -> near_miss, dead dropped.
    Within a tier, best net expectancy first (gross as fallback). Annotates
    each kept candidate with its `kind`. Truncates to `limit`."""
    cfg = cfg or GauntletConfig()  # resolve once, not per candidate
    kept = []
    for c in candidates:
        kind = classify(c["metrics"], c["verdict"], c.get("reasons", ()),
                        c.get("scorecard"), cfg=cfg, band=band)
        if kind == "dead":
            continue
        c["kind"] = kind  # ponytail: annotate in place; copy if callers ever need pristine dicts
        kept.append(c)
    kept.sort(key=lambda c: (
        TIERS.index(c["kind"]),
        -c["metrics"].get("expectancy_net", c["metrics"].get("expectancy", 0.0)),
    ))
    return kept[:limit]
