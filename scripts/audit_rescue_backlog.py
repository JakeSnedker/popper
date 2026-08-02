"""Classify every killed hypothesis already in shared memory as a rescue target.

Answers one question with real numbers: of everything the fleet has killed so
far, how much is genuinely dead and how much is recoverable by a later
exploit-heavy campaign? Uses the SAME `classify()` the Director's exploit loop
uses, so the tiers reported here are the tiers a rescue run would act on.

Read-only. Writes nothing to Supabase.
"""

import argparse
import collections
import json

from popper.falsify import GauntletConfig
from popper.memory import get_memory
from popper.orchestrator.rescue import scan_backlog


def audit(band: float = 0.1, limit: int | None = None) -> dict:
    scan = scan_backlog(get_memory(), cfg=GauntletConfig(), band=band)
    entries = scan["entries"][:limit] if limit else scan["entries"]
    tiers: collections.Counter = collections.Counter(
        entry["target"]["kind"] for entry in entries
    )
    rescuable: list[dict] = []
    for entry in entries:
        if not entry["eligible"]:
            continue
        target = entry["target"]
        scorecard = target["scorecard"]
        metrics = target["metrics"]
        rescuable.append({
            "hypothesis_id": target["rescued_from"],
            "spec_id": target["source_spec_id"],
            "kind": target["kind"],
            "expectancy": round(metrics.get("expectancy", 0.0), 4),
            "expectancy_net": round(metrics.get("expectancy_net", 0.0), 4),
            "pbo": round(scorecard.pbo, 3),
            "parameter_stability": round(scorecard.parameter_stability, 3),
        })

    rescuable.sort(key=lambda row: (
        ("survivor", "cost_killed", "near_miss").index(row["kind"]),
        -row["expectancy_net"],
    ))
    return {
        "tiers": dict(tiers),
        "excluded": scan["excluded"],
        "missing_data": scan["missing"],
        "rescuable": rescuable,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--band", type=float, default=0.1,
                        help="how far outside the PBO/stability gates still counts as a near miss")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    result = audit(band=args.band, limit=args.limit)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    tiers = result["tiers"]
    total = sum(tiers.values())
    print(f"\nRescue backlog (band={args.band}, {total} classified)")
    print("=" * 72)
    for tier in ("survivor", "cost_killed", "near_miss", "dead", "unclassifiable"):
        count = tiers.get(tier, 0)
        share = f"{100 * count / total:.1f}%" if total else "-"
        print(f"{tier:>12}: {count:>5}  {share:>6}")
    if result["excluded"]:
        print(f"\nexcluded: {result['excluded']}")
    if result["missing_data"]:
        print(f"missing relationships: {result['missing_data']}")

    recoverable = len(result["rescuable"])
    print("=" * 72)
    print(f"Eligible exploit targets: {recoverable}")

    if result["rescuable"]:
        print(f"\nTop {args.top} rescue targets:")
        print(f"{'hypothesis':<28}{'kind':<13}{'exp':>9}{'exp_net':>10}{'pbo':>7}{'stab':>7}")
        for row in result["rescuable"][:args.top]:
            print(f"{row['hypothesis_id']:<28}{row['kind']:<13}{row['expectancy']:>9}"
                  f"{row['expectancy_net']:>10}{row['pbo']:>7}{row['parameter_stability']:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
