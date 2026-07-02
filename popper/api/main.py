"""FastAPI app serving the dashboard: build phases + the alpha-memory trail."""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from popper import __version__
from popper.architecture import architecture
from popper.artifacts import Hypothesis, HypothesisStatus
from popper.config import settings
from popper.memory import get_memory
from popper.phases import PHASES, progress

app = FastAPI(title="Popper API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "store": settings.store}


@app.get("/api/phases")
def get_phases() -> dict:
    return {"phases": PHASES, "progress": progress()}


@app.get("/api/architecture")
def get_architecture() -> dict:
    return architecture()


@app.get("/api/stats")
def get_stats() -> dict:
    return get_memory().stats()


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
