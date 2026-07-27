"""FastAPI app serving the dashboard: build phases + the alpha-memory trail."""

from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

from popper import __version__
from popper.agents.views import BlindView
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
    from popper.data.bars import DATA_DIR
    sources = []
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
            sources.append({"symbol": symbol, "timeframe": timeframe, "rows": rows})
    return {"data_dir": str(DATA_DIR), "sources": sources}


@app.get("/api/stats")
def get_stats() -> dict:
    return get_memory().stats()


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
def get_map() -> dict:
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
