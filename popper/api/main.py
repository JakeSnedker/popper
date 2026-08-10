"""FastAPI app serving the dashboard: build phases + the alpha-memory trail."""

from collections import Counter, defaultdict
from datetime import datetime
from functools import wraps
from statistics import fmean, median
from time import monotonic
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

from popper import __version__
from popper.agents.views import BlindView
from popper.api.extensions import install_extensions
from popper.architecture import architecture
from popper.artifacts import Hypothesis, HypothesisStatus
from popper.config import settings
from popper.falsify import GauntletConfig
from popper.falsify.stats import bootstrap_cone
from popper.memory import get_memory
from popper.memory.map import edge_type
from popper.orchestrator import run_research
from popper.phases import PHASES, progress

app = FastAPI(title="Popper API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
install_extensions(app)


class ResearchRunRequest(BaseModel):
    iterations: int = Field(default=3, ge=1, le=50)
    signal_edge_mode: Literal["loose", "balanced", "strict"] = "balanced"
    exit_menu: bool = True


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "store": settings.store}


@app.get("/api/phases")
def get_phases() -> dict:
    return {"phases": PHASES, "progress": progress()}


@app.get("/api/architecture")
def get_architecture() -> dict:
    return architecture()


@app.get("/api/data-sources")
def data_sources() -> dict:
    """The market-data parquets THIS machine actually has — the only (symbol,
    timeframe) pairs a run can legitimately use. The dashboard offers exactly
    these so nobody points a run at data that isn't here (which otherwise fails
    deep in the runner). Shows what each Popper instance has access to."""
    from popper.data.bars import CANONICAL_DATASET_DIR, DATA_DIR
    indexed = {}
    if DATA_DIR.exists():
        for p in sorted(DATA_DIR.glob("*/*.parquet")):
            stem = p.stem  # e.g. "US30_M5"
            if "_" not in stem:
                continue
            symbol, timeframe = stem.rsplit("_", 1)
            rows = None
            try:  # footer-only read; a corrupt file must not 500 the whole list
                import pyarrow.parquet as pq
                rows = pq.ParquetFile(p).metadata.num_rows
            except Exception:  # noqa: BLE001
                pass
            indexed[(symbol, timeframe)] = {
                "symbol": symbol, "timeframe": timeframe, "rows": rows,
            }
    manifest_path = CANONICAL_DATASET_DIR / "dataset-manifest.json"
    if manifest_path.is_file():
        try:
            import json
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for timeframe in ("M1", "M5", "M15"):
                indexed[("US30", timeframe)] = {
                    "symbol": "US30",
                    "timeframe": timeframe,
                    "rows": manifest["bars"][timeframe]["total_rows"],
                }
        except Exception:  # noqa: BLE001
            pass
    return {"data_dir": str(DATA_DIR), "sources": [indexed[key] for key in sorted(indexed)]}


@app.get("/api/stats")
def get_stats() -> dict:
    return get_memory().stats()


def _cached(seconds: float):
    """Serve a zero-argument endpoint from memory for `seconds`.

    ponytail: one entry per decorated function, no eviction, no key -- these are
    whole-dashboard snapshots, not a user-keyed cache. Process-local, so each
    worker warms its own copy. If this ever needs per-user results or shared
    state across workers, it needs a real cache, not a bigger version of this.
    """
    def decorate(fn):
        cell: dict = {}

        @wraps(fn)
        def wrapper(*args, **kwargs):
            now = monotonic()
            if "at" in cell and (now - cell["at"]) < seconds:
                return cell["value"]
            value = fn(*args, **kwargs)
            cell["at"], cell["value"] = now, value
            return value

        wrapper.cache_clear = cell.clear  # type: ignore[attr-defined]
        return wrapper

    return decorate


_GATE_ORDER = [
    "gate_signal_edge",
    "gate0_leakage",
    "gate1_walkforward",
    "gate2_robustness",
    "gate3_mc_ruin",
    "gate4_multiple_testing",
]
_GATE_LABELS = {
    "gate_signal_edge": "Signal edge",
    "gate0_leakage": "Leakage / look-ahead",
    "gate1_walkforward": "Walk-forward OOS",
    "gate2_robustness": "Parameter robustness",
    "gate3_mc_ruin": "Monte Carlo ruin",
    "gate4_multiple_testing": "Multiple testing",
    "other": "Other / legacy reason",
}


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _duration_summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg_seconds": None, "median_seconds": None, "p90_seconds": None}
    ordered = sorted(values)
    p90 = ordered[round(0.9 * (len(ordered) - 1))]
    return {
        "count": len(values),
        "avg_seconds": round(fmean(values), 2),
        "median_seconds": round(median(values), 2),
        "p90_seconds": round(p90, 2),
    }


def _proposer_attribution(mem, events: list[dict] | None = None) -> tuple[dict, list[dict]]:
    """Link proposal-call telemetry to the hypothesis stored later in the same iteration.

    Older runs did not persist call metadata, so missing attribution stays explicitly unknown.
    """
    events = events if events is not None else mem.list_research_events(limit=50_000)
    by_iteration = {}
    hypothesis_iterations = {}
    for event in events:
        key = (event.get("run_id"), event.get("iteration"), event.get("engine"))
        if (event.get("role") == "hypothesis" and event.get("stage") == "propose"
                and event.get("status") == "ok"):
            calls = (event.get("payload") or {}).get("llm_calls") or []
            call = next((row for row in reversed(calls) if row.get("status") == "ok"), None)
            if call:
                by_iteration[key] = {
                    "provider": call.get("provider"),
                    "model": call.get("model"),
                    "role": call.get("role"),
                    "operator_id": event.get("operator_id"),
                    "machine_id": event.get("machine_id"),
                    "release_id": event.get("release_id"),
                    "dataset_id": event.get("dataset_id"),
                }
        if event.get("role") == "gauntlet" and event.get("stage") == "evaluate":
            hypothesis_id = (event.get("artifact_ids") or {}).get("hypothesis_id")
            if hypothesis_id:
                hypothesis_iterations[hypothesis_id] = key
    return {
        hypothesis_id: by_iteration[key]
        for hypothesis_id, key in hypothesis_iterations.items()
        if key in by_iteration
    }, events


def _failed_gate(diagnostics: dict, reasons: list[str]) -> str | None:
    for key in _GATE_ORDER:
        value = diagnostics.get(key)
        if isinstance(value, dict) and value.get("passed") is False:
            return key
    reason = " ".join(reasons).lower()
    if "signal edge" in reason:
        return "gate_signal_edge"
    if "leak" in reason or "look-ahead" in reason:
        return "gate0_leakage"
    if "forward" in reason or "out-of-sample" in reason or "walk-forward" in reason:
        return "gate1_walkforward"
    if "parameter" in reason or "surface" in reason:
        return "gate2_robustness"
    if "ruin" in reason or "drawdown" in reason:
        return "gate3_mc_ruin"
    if "pbo" in reason or "sharpe" in reason or "overfit" in reason:
        return "gate4_multiple_testing"
    return "other" if reasons else None


def _test_rows(mem, hypotheses: list, attribution: dict,
               with_metrics: bool = True) -> list[dict]:
    rows = []
    records = mem.analysis_records(
        [hypothesis.id for hypothesis in hypotheses], with_metrics=with_metrics,
    )
    for hypothesis in hypotheses:
        record = records.get(hypothesis.id, {})
        specs = {row["id"]: row for row in record.get("specs", [])}
        experiments = {row["id"]: row for row in record.get("experiments", [])}
        scorecards = {row["experiment_id"]: row for row in record.get("scorecards", [])}
        proposer = attribution.get(hypothesis.id, {
            "provider": None, "model": None, "role": None,
            "operator_id": None, "machine_id": None,
            "release_id": None, "dataset_id": None,
        })
        for verdict in record.get("verdicts", []):
            experiment = experiments.get(verdict["experiment_id"], {})
            spec = specs.get(experiment.get("spec_id"), {})
            scorecard = scorecards.get(verdict["experiment_id"], {})
            raw = experiment.get("raw_metrics") or {}
            diagnostics = scorecard.get("diagnostics") or {}
            duration = None
            if spec.get("created_at") and verdict.get("created_at"):
                duration = max(0.0, (
                    _timestamp(verdict["created_at"]) - _timestamp(spec["created_at"])
                ).total_seconds())
            rows.append({
                "hypothesis_id": hypothesis.id,
                "claim": hypothesis.claim,
                "mechanism": hypothesis.mechanism,
                "rationale": hypothesis.rationale,
                "status": hypothesis.status.value,
                "edge_type": hypothesis.tags.get("edge_type") or edge_type(hypothesis),
                "expected_regime": hypothesis.expected_regime.value,
                "novelty": hypothesis.novelty_score,
                "parent_id": hypothesis.parent_id,
                "created_at": hypothesis.created_at.isoformat(),
                "verdict": verdict["type"],
                "verdict_id": verdict["id"],
                "reasons": verdict.get("reasons") or [],
                "completed_at": verdict["created_at"],
                "duration_seconds": round(duration, 2) if duration is not None else None,
                "experiment_id": experiment.get("id"),
                "dataset": experiment.get("dataset"),
                "trades_n": experiment.get("trades_n", 0),
                "metrics": {
                    "profit_factor": raw.get("profit_factor"),
                    "profit_factor_net": raw.get("profit_factor_net"),
                    "expectancy": raw.get("expectancy"),
                    "expectancy_net": raw.get("expectancy_net"),
                    "sharpe": raw.get("sharpe"),
                    "win_rate": raw.get("win_rate"),
                    "max_drawdown": raw.get("max_drawdown"),
                    "deflated_sharpe": scorecard.get("deflated_sharpe"),
                    "pbo": scorecard.get("pbo"),
                    "robustness": scorecard.get("robustness"),
                    "regime_stability": scorecard.get("regime_stability"),
                    "correlation": scorecard.get("correlation"),
                },
                "failed_gate": (
                    _failed_gate(diagnostics, verdict.get("reasons") or [])
                    if verdict["type"] == "kill" else None
                ),
                "evidence": {
                    "diagnostics_recorded": bool(diagnostics),
                    "gates_recorded": sum(key in diagnostics for key in _GATE_ORDER),
                    "costs_recorded": any(
                        raw.get(key) is not None
                        for key in ("expectancy_net", "profit_factor_net", "avg_cost_r")
                    ),
                },
                "proposer": proposer,
            })
    return rows


@app.get("/api/survivors")
def survivors() -> dict:
    mem = get_memory()
    hypotheses = [
        *mem.list_hypotheses(HypothesisStatus.PROMOTED),
        *mem.list_hypotheses(HypothesisStatus.SURVIVED),
    ]
    attribution, _ = _proposer_attribution(mem)
    tests = _test_rows(mem, hypotheses, attribution)
    latest = {}
    for row in sorted(tests, key=lambda item: item["completed_at"], reverse=True):
        latest.setdefault(row["hypothesis_id"], row)
    ordered = sorted(
        latest.values(),
        key=lambda row: (row["status"] != "promoted", row["completed_at"]),
    )
    return {"survivors": ordered}


@app.get("/api/analytics")
@_cached(seconds=120)
def analytics() -> dict:
    mem = get_memory()
    hypotheses = mem.list_hypotheses()
    attribution, events = _proposer_attribution(mem)
    # Aggregates below read verdicts, gates, categories and timings only — never
    # per-test metrics — so the expensive raw_metrics blobs stay in the database.
    tests = _test_rows(mem, hypotheses, attribution, with_metrics=False)

    outcomes = Counter(row["verdict"] for row in tests)
    passed = outcomes["survive"] + outcomes["promote"]
    tested_hypotheses = {row["hypothesis_id"] for row in tests}
    # Two different questions, two different answers, both correct:
    #   outcomes[...]  = passing VERDICTS ever recorded (history; a hypothesis
    #                    that passed once and failed a later retest counts here)
    #   current[...]   = hypotheses whose status is passing RIGHT NOW
    # The dashboard funnel and the survivors page both show `current`, so
    # analytics must publish it too or the three views look like they disagree.
    current = Counter(hypothesis.status.value for hypothesis in hypotheses)
    durations = [row["duration_seconds"] for row in tests if row["duration_seconds"] is not None]

    categories = defaultdict(lambda: {
        "generated": 0, "tests": 0, "killed": 0, "survived": 0, "promoted": 0,
    })
    for hypothesis in hypotheses:
        category = hypothesis.tags.get("edge_type") or edge_type(hypothesis)
        categories[category]["generated"] += 1
    for row in tests:
        item = categories[row["edge_type"]]
        item["tests"] += 1
        item[{"kill": "killed", "survive": "survived", "promote": "promoted"}[row["verdict"]]] += 1

    category_rows = []
    for name, item in categories.items():
        item = dict(item)
        item["name"] = name
        item["pass_rate"] = round(
            100 * (item["survived"] + item["promoted"]) / item["tests"], 2
        ) if item["tests"] else 0.0
        category_rows.append(item)
    category_rows.sort(key=lambda item: (-item["tests"], item["name"]))

    model_groups = defaultdict(lambda: {"tests": 0, "killed": 0, "passed": 0})
    for row in tests:
        proposer = row["proposer"]
        key = (proposer.get("provider"), proposer.get("model"))
        item = model_groups[key]
        item["tests"] += 1
        if row["verdict"] == "kill":
            item["killed"] += 1
        else:
            item["passed"] += 1
    model_rows = []
    for (provider, model), item in model_groups.items():
        model_rows.append({
            "provider": provider,
            "model": model,
            **item,
            "pass_rate": round(100 * item["passed"] / item["tests"], 2) if item["tests"] else 0.0,
        })
    model_rows.sort(key=lambda item: (-item["tests"], item["provider"] or ""))

    failures = Counter(row["failed_gate"] for row in tests if row["verdict"] == "kill")
    failure_rows = [{
        "key": key,
        "label": _GATE_LABELS.get(key, key.replace("_", " ")),
        "count": count,
        "pct_of_failures": round(100 * count / outcomes["kill"], 2) if outcomes["kill"] else 0.0,
    } for key, count in failures.most_common()]

    stage_defs = {
        ("hypothesis", "propose"): ("Hypothesis generation", "LLM creates a batch of falsifiable claims."),
        ("synthesis", "compile"): ("DSL synthesis", "LLM translates a claim and repairs validator failures."),
        ("gauntlet", "evaluate"): ("Backtest + gauntlet", "Runner and fail-fast falsification tests execute."),
        ("worker", "refine"): ("Worker refinement", "Optimizer searches a survivor or near-miss locally."),
    }
    event_groups = defaultdict(list)
    for event in events:
        stage_key = (event.get("role"), event.get("stage"))
        if stage_key in stage_defs:
            event_groups[(event.get("run_id"), event.get("iteration"), event.get("engine"), *stage_key)].append(event)
    stage_values = defaultdict(list)
    for key, group in event_groups.items():
        started = [_timestamp(row["created_at"]) for row in group if row.get("status") == "running"]
        ended = [_timestamp(row["created_at"]) for row in group if row.get("status") != "running"]
        if started and ended:
            duration = (max(ended) - min(started)).total_seconds()
            if duration >= 0:
                stage_values[key[-2:]].append(duration)
    stages = [{
        "key": f"{role}.{stage}",
        "label": label,
        "description": description,
        **_duration_summary(stage_values[(role, stage)]),
    } for (role, stage), (label, description) in stage_defs.items()]

    return {
        "totals": {
            "generated": len(hypotheses),
            "tests": len(tests),
            "tested_hypotheses": len(tested_hypotheses),
            "awaiting_verdict": len(hypotheses) - len(tested_hypotheses),
            "killed": outcomes["kill"],
            "survived": outcomes["survive"],
            "promoted": outcomes["promote"],
            "pass_rate": round(100 * passed / len(tests), 2) if tests else 0.0,
            "fail_rate": round(100 * outcomes["kill"] / len(tests), 2) if tests else 0.0,
            # Current status, not verdict history — matches the funnel + survivors page.
            "current_survived": current["survived"],
            "current_promoted": current["promoted"],
            "current_passing": current["survived"] + current["promoted"],
            "in_flight": current["proposed"] + current["testing"],
        },
        "test_duration": _duration_summary(durations),
        "stages": stages,
        "failure_gates": failure_rows,
        "categories": category_rows,
        "models": model_rows,
        "attribution": {
            "attributed_tests": sum(bool(row["proposer"].get("model")) for row in tests),
            "unattributed_tests": sum(not row["proposer"].get("model") for row in tests),
            "operators": dict(Counter(
                row["proposer"].get("operator_id") or "unknown" for row in tests
            )),
            "machines": dict(Counter(
                row["proposer"].get("machine_id") or "unknown" for row in tests
            )),
            "releases": dict(Counter(
                row["proposer"].get("release_id") or "unknown" for row in tests
            )),
            "datasets": dict(Counter(
                row["proposer"].get("dataset_id") or "unknown" for row in tests
            )),
            "note": "Full attribution is available only for authenticated attributed runs.",
        },
        "event_sample_size": len(events),
    }


@app.get("/api/llm-config")
def llm_config() -> dict:
    """Public routing shape only; credentials are never returned."""
    routes = {}
    if settings.llm_backend in {"subscriptions", "hybrid"}:
        routes = {
            "hypothesis": settings.llm_hypothesis_route,
            "wildcard": settings.llm_wildcard_route,
            "synthesis": settings.llm_synthesis_route,
            "synthesis_strong": settings.llm_synthesis_strong_route,
            "audit": settings.llm_audit_route,
        }
    return {
        "backend": settings.llm_backend,
        "policy": settings.llm_route_policy,
        "batch_size": settings.llm_batch_size,
        "fidelity_mode": settings.llm_fidelity_mode,
        "survivor_audit": settings.llm_survivor_audit,
        "routes": routes,
    }


@app.get("/api/feature-gaps")
def list_feature_gaps(status: str | None = None, limit: int = 20) -> dict:
    return {"feature_gaps": get_memory().list_feature_gaps(status=status, limit=limit)}


@app.post("/api/research")
def run_research_once(req: ResearchRunRequest) -> dict:
    cfg = GauntletConfig(exit_menu=req.exit_menu, signal_edge_mode=req.signal_edge_mode)
    s = run_research(iterations=req.iterations, cfg=cfg)
    return {
        "run_id": s.run_id,
        "iterations": s.iterations,
        "budget_spent": s.budget_spent,
        "proposed": s.proposed,
        "killed": s.killed,
        "survived": s.survived,
        "worker_runs": s.worker_runs,
        "skipped": s.skipped,
        "failures": s.failures,
        "survivors": s.survivors,
        "categories": s.category_report(),
        "llm_usage": s.llm_usage,
        "provider_paused": s.provider_paused,
        "retry_after": s.retry_after,
        "stopped_reason": s.stopped_reason,
    }


@app.get("/api/research-events")
def research_events(run_id: str | None = None, limit: int = 100) -> dict:
    return {"events": get_memory().list_research_events(run_id=run_id, limit=limit)}


@app.get("/api/hypotheses")
def list_hypotheses(status: str | None = None) -> dict:
    st = HypothesisStatus(status) if status else None
    items = get_memory().list_hypotheses(st)
    return {"hypotheses": [h.model_dump(mode="json") for h in items]}


@app.get("/api/hypotheses/{hypothesis_id}")
def get_record(hypothesis_id: str) -> dict:
    rec = get_memory().record(hypothesis_id)
    if not rec:
        raise HTTPException(status_code=404, detail="hypothesis not found")
    return rec


@app.post("/api/hypotheses")
def create_hypothesis(h: Hypothesis) -> dict:
    mem = get_memory()
    if mem.is_stale(h):
        raise HTTPException(status_code=409, detail="stale: this idea has already been explored")
    stored = mem.add_hypothesis(h)
    return stored.model_dump(mode="json")


# --- Phase 8 observability: see inside the loop ---

def _pca_2d(embeddings: list[list[float]]) -> list[tuple[float, float]]:
    """Project n embeddings onto their first 2 principal components (center + SVD)."""
    if not embeddings:
        return []
    x = np.asarray(embeddings, dtype=float)
    if x.shape[0] == 1:
        return [(0.0, 0.0)]  # a single point has no spread to project
    centered = x - x.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    for i in range(min(2, vt.shape[0])):
        # SVD component signs are arbitrary; orient each axis by its largest
        # loading so refreshes don't randomly mirror the map.
        j = int(np.argmax(np.abs(vt[i])))
        if vt[i, j] < 0:
            vt[i] *= -1
    proj = centered @ vt[:2].T
    if proj.shape[1] < 2:  # <2-dim embedding space: pad the missing axis with 0
        proj = np.pad(proj, ((0, 0), (0, 2 - proj.shape[1])))
    return [(round(float(px), 4), round(float(py), 4)) for px, py in proj]


@app.get("/api/map")
@_cached(seconds=300)
def get_map() -> dict:
    """The alpha map. CACHED, because it is the single most expensive read in the
    system and the dashboard polls it on a timer: every call pulls every
    hypothesis WITH its 256-dim embedding (~3.3 MB from the database at 1.2k
    hypotheses, 64% of it embeddings the PCA genuinely needs). Uncached at the
    old 10s poll that is ~1.2 GB/hour of egress -- a free-tier month in four
    hours, which is exactly how this project blew its quota. New hypotheses
    arrive minutes apart at best, so a 2-minute-stale map costs nothing real.
    """
    mem = get_memory()
    view = BlindView(mem)
    hyps = [h for h in mem.list_hypotheses() if h.embedding]
    verdicts = mem.latest_verdict_types()
    coords = _pca_2d([h.embedding for h in hyps])
    points = [
        {
            "id": h.id,
            "claim": h.claim,
            "edge_type": edge_type(h),
            "novelty": h.novelty_score,
            "verdict": verdicts.get(h.id),
            "x": x,
            "y": y,
        }
        for h, (x, y) in zip(hyps, coords)
    ]
    return {"categories": view.coverage(), "whitespace": view.whitespace(), "points": points}


@app.get("/api/verdicts")
def recent_verdicts(limit: int = 20) -> dict:
    return {"verdicts": get_memory().recent_verdicts(limit)}


@app.get("/api/hypotheses/{hypothesis_id}/mc-cone")
def get_mc_cone(hypothesis_id: str) -> dict:
    """Bootstrap Monte Carlo risk cone for an alpha's best experiment. Picks the
    experiment with the longest usable equity curve (some legacy runs stored a
    truncated curve; the bootstrap needs the real per-trade path)."""
    rec = get_memory().record(hypothesis_id)
    if not rec:
        raise HTTPException(status_code=404, detail="hypothesis not found")
    best = None  # (experiment, curve)
    for e in rec["experiments"]:
        curve = e.get("equity_curve") or []
        if len(curve) > (len(best[1]) if best else 30):
            best = (e, curve)
    if not best:
        raise HTTPException(status_code=404, detail="no experiment with a usable equity curve yet")
    exp, curve = best
    cone = bootstrap_cone(curve)
    if cone is None:
        raise HTTPException(status_code=404, detail="equity curve unusable (non-positive or too short)")
    verdict = next((v["type"] for v in rec["verdicts"] if v["experiment_id"] == exp["id"]), None)
    cone.update({"experiment_id": exp["id"], "verdict": verdict, "trades_n": exp["trades_n"]})
    return cone


@app.get("/api/timeline")
def get_timeline(hypothesis_id: str) -> dict:
    rec = get_memory().record(hypothesis_id)
    if not rec:
        raise HTTPException(status_code=404, detail="hypothesis not found")
    scs = {sc["experiment_id"]: sc for sc in rec["scorecards"]}
    vds = {v["experiment_id"]: v for v in rec["verdicts"]}
    experiments = [
        {
            "profit_factor": e["raw_metrics"].get("profit_factor"),
            "deflated_sharpe": scs.get(e["id"], {}).get("deflated_sharpe"),
            "robustness": scs.get(e["id"], {}).get("robustness"),
            "verdict": vds.get(e["id"], {}).get("type"),
        }
        for e in sorted(rec["experiments"], key=lambda e: e["created_at"])
    ]
    return {"experiments": experiments}
