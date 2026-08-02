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


def gate_ran(diagnostics, name: str) -> bool:
    """True only when `name` actually executed and produced a real result.

    The gauntlet short-circuits: a spec killed at the Layer-1 signal-edge gate
    returns `scorecard(0, 0, 0, 1.0)`, where pbo=1.0 and parameter_stability=0.0
    are SENTINELS meaning "never computed" — not measurements of a bad strategy.
    Only gates present in `diagnostics` ran, so that is the thing to test.
    """
    if not isinstance(diagnostics, dict):
        return False
    entry = diagnostics.get(name)
    return isinstance(entry, dict) and entry.get("passed") is not None


def _has_signal_edge(diagnostics) -> bool:
    """True only when the exit-neutral signal-edge gate actually passed."""
    return gate_ran(diagnostics, "gate_signal_edge") and (
        diagnostics["gate_signal_edge"]["passed"] is True
    )


def classify(metrics, verdict, reasons=(), scorecard=None, cfg=None, band=0.1,
             diagnostics=None) -> str:
    """One of 'survivor' | 'cost_killed' | 'near_miss' | 'dead'.

    cost_killed = real gross edge, killed only by cost (the prime rescue).
    near_miss = failed but within `band` of BOTH the PBO and stability gates.

    `diagnostics` is the scorecard's per-gate glass box. A killed row without
    those measurements is unclassifiable rather than optimistically rescuable.
    """
    if verdict in SURVIVE:
        return "survivor"
    if not isinstance(diagnostics, dict) or not diagnostics:
        return "unclassifiable"
    if not _has_signal_edge(diagnostics):
        return "dead"

    measured = (gate_ran(diagnostics, "gate2_robustness")
                and gate_ran(diagnostics, "gate4_multiple_testing"))
    if scorecard is None or not measured:
        return "unclassifiable"

    t = (cfg or GauntletConfig()).thresholds
    stable = scorecard.parameter_stability >= t.min_profitable_fraction
    pbo_ok = scorecard.pbo <= t.max_pbo
    if (stable and pbo_ok and metrics.get("expectancy", 0) > 0
            and "expectancy_net" in metrics and metrics["expectancy_net"] <= 0):
        return "cost_killed"
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
                        c.get("scorecard"), cfg=cfg, band=band,
                        diagnostics=c.get("diagnostics"))
        if kind not in TIERS:  # dead, or unclassifiable: nothing measured to rank it by
            continue
        c["kind"] = kind  # ponytail: annotate in place; copy if callers ever need pristine dicts
        kept.append(c)
    kept.sort(key=lambda c: (
        TIERS.index(c["kind"]),
        -c["metrics"].get("expectancy_net", c["metrics"].get("expectancy", 0.0)),
    ))
    return kept[:limit]
