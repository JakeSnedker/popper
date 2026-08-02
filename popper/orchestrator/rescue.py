"""Reconstruct the exploit engine's rescue backlog from stored memory.

Without this the Director only ever sees the kills produced by the run it is
currently executing: `RunSummary.categories` is built in-process, so a node
started with a high exploit budget has nothing to refine and silently falls
through to explore. Everything the fleet killed on previous days — including
cost-killed edges with a real gross edge that friction wiped out — is invisible.

This module reads those kills back, tiers them with the same `classify()` the
live exploit loop uses, and returns categories shaped exactly like the ones the
Director builds itself, so seeding is a dict merge rather than a new code path.

Scale: artifacts are fetched in bulk pages (a handful of queries total), not one
network trail per hypothesis, so the cost is driven by the size of the backlog
rather than by the number of hypotheses ever stored.
"""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from popper.artifacts import StrategySpec
from popper.dsl import DSLValidationError
from popper.dsl.validator import validate_local_data_context
from popper.falsify import GauntletConfig
from popper.memory.map import edge_type
from popper.orchestrator.selection import classify

# Tiers worth handing to the optimizer, best first.
RESCUABLE = ("survivor", "cost_killed", "near_miss")
_TIER_RANK = {name: rank for rank, name in enumerate(RESCUABLE)}
_SPEC_CHUNK = 100


def _latest(rows: list[dict]) -> dict | None:
    return max(rows, key=lambda r: str(r.get("created_at") or ""), default=None)


def _metrics(experiment: dict) -> dict:
    raw = experiment.get("raw_metrics") or {}
    metrics = {"expectancy": raw.get("expectancy", 0.0)}
    if raw.get("expectancy_net") is not None:
        metrics["expectancy_net"] = raw["expectancy_net"]
    return metrics


def _scorecard(row: dict) -> SimpleNamespace:
    return SimpleNamespace(
        pbo=row.get("pbo") if row.get("pbo") is not None else 1.0,
        parameter_stability=(row.get("parameter_stability")
                             if row.get("parameter_stability") is not None else 0.0),
    )


def _full_specs(mem, hypotheses, spec_ids: set[str]) -> dict[str, StrategySpec]:
    """Load only the exact specs referenced by the candidate evaluations."""
    if not spec_ids:
        return {}
    specs: dict[str, StrategySpec] = {}
    client = getattr(mem, "client", None)
    if client is None:
        for hypothesis in hypotheses:
            for row in mem.record(hypothesis.id).get("specs", []):
                if row.get("id") in spec_ids:
                    specs[row["id"]] = StrategySpec(**row)
        return specs

    wanted = list(spec_ids)
    for start in range(0, len(wanted), _SPEC_CHUNK):
        chunk = wanted[start:start + _SPEC_CHUNK]
        rows = (client.table("specs").select("*").in_("id", chunk)
                .order("id").execute().data)
        for row in rows:
            specs[row["id"]] = StrategySpec(**row)
    return specs


def scan_backlog(mem, cfg: GauntletConfig | None = None, band: float = 0.1,
                 statuses=("killed", "survived")) -> dict:
    """Classify the latest coherent evaluation of every historical spec.

    Verdict -> experiment -> scorecard -> spec relationships are followed by ID;
    timestamps are used only to choose the latest verdict for the same spec.
    """
    cfg = cfg or GauntletConfig()
    hypotheses = [
        hypothesis for hypothesis in mem.list_hypotheses()
        if (hypothesis.status.value if hasattr(hypothesis.status, "value")
            else hypothesis.status) in statuses
    ]
    if not hypotheses:
        return {"entries": [], "tiers": {}, "excluded": {}, "missing": {}}

    records = mem.analysis_records([hypothesis.id for hypothesis in hypotheses])
    spec_ids = {
        row["id"] for record in records.values()
        for row in record.get("specs", []) if row.get("id")
    }
    specs = _full_specs(mem, hypotheses, spec_ids)

    exhausted: set[str] = set()
    for record in records.values():
        for row in record.get("scorecards", []):
            source = (row.get("diagnostics") or {}).get("refined_from_spec_id")
            if source:
                exhausted.add(source)

    tiers: Counter = Counter()
    excluded: Counter = Counter()
    missing: Counter = Counter()
    entries: list[dict] = []

    for hypothesis in hypotheses:
        record = records.get(hypothesis.id) or {}
        experiments = {row["id"]: row for row in record.get("experiments", [])}
        scorecards: dict[str, list[dict]] = {}
        for row in record.get("scorecards", []):
            scorecards.setdefault(row.get("experiment_id", ""), []).append(row)

        seen_specs: set[str] = set()
        verdicts = sorted(
            record.get("verdicts", []),
            key=lambda row: str(row.get("created_at") or ""),
            reverse=True,
        )
        for verdict in verdicts:
            experiment = experiments.get(verdict.get("experiment_id"))
            if experiment is None:
                missing["experiment"] += 1
                continue
            spec_id = experiment.get("spec_id")
            if not spec_id or spec_id in seen_specs:
                continue
            seen_specs.add(spec_id)
            scorecard = _latest(scorecards.get(experiment["id"], []))
            spec = specs.get(spec_id)
            if scorecard is None or spec is None:
                missing["scorecard" if scorecard is None else "spec"] += 1
                continue

            diagnostics = scorecard.get("diagnostics") or {}
            metrics = _metrics(experiment)
            kind = classify(
                metrics,
                verdict.get("type", "kill"),
                reasons=verdict.get("reasons", ()) or (),
                scorecard=_scorecard(scorecard),
                cfg=cfg,
                band=band,
                diagnostics=diagnostics,
            )
            tiers[kind] += 1
            reason = None
            try:
                validate_local_data_context(spec)
            except DSLValidationError:
                reason = "unavailable_local_data"
            if spec_id in exhausted:
                reason = "already_refined"
            elif diagnostics.get("refined_from_spec_id") and kind != "survivor":
                reason = "failed_refinement"
            if reason:
                excluded[reason] += 1

            target = {
                "spec": spec,
                "metrics": metrics,
                "verdict": verdict.get("type", "kill"),
                "reasons": verdict.get("reasons", ()) or (),
                "scorecard": _scorecard(scorecard),
                "diagnostics": diagnostics,
                "kind": kind,
                "rescued_from": hypothesis.id,
                "source_spec_id": spec_id,
            }
            entries.append({
                "hypothesis": hypothesis,
                "target": target,
                "eligible": reason is None and kind in RESCUABLE,
                "excluded_reason": reason,
            })

    return {
        "entries": entries,
        "tiers": dict(tiers),
        "excluded": dict(excluded),
        "missing": dict(missing),
    }


def load_backlog(mem, cfg: GauntletConfig | None = None, band: float = 0.1,
                 limit: int | None = None,
                 statuses=("killed", "survived")) -> dict[str, dict]:
    """Director categories seeded with restart-safe historical targets."""
    scan = scan_backlog(mem, cfg=cfg, band=band, statuses=statuses)
    eligible = [entry for entry in scan["entries"] if entry["eligible"]]
    eligible.sort(key=lambda entry: (
        _TIER_RANK[entry["target"]["kind"]],
        -entry["target"]["metrics"].get(
            "expectancy_net", entry["target"]["metrics"].get("expectancy", 0.0),
        ),
    ))
    if limit is not None:
        eligible = eligible[:limit]

    categories: dict[str, dict] = {}
    for entry in eligible:
        target = entry["target"]
        category = edge_type(entry["hypothesis"])
        bucket = categories.setdefault(
            category, {"kills": 0, "survivors": [], "dead": False, "near_misses": []},
        )
        if target["kind"] == "survivor":
            bucket["survivors"].append(target["spec"])
        else:
            bucket["near_misses"].append(target)
    return categories


def summarise(categories: dict[str, dict]) -> dict:
    """Counts for logging and the research event, without dumping whole specs."""
    rescued = [target for category in categories.values()
               for target in category.get("near_misses", [])]
    survivors = sum(len(category.get("survivors", [])) for category in categories.values())
    return {
        "categories": len(categories),
        "targets": len(rescued) + survivors,
        "survivor": survivors,
        "cost_killed": sum(1 for target in rescued if target["kind"] == "cost_killed"),
        "near_miss": sum(1 for target in rescued if target["kind"] == "near_miss"),
    }
