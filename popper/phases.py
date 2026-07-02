"""The roadmap as data, so the GUI can render build progress.

Single source of truth for phase status. Update STATUS here as phases complete;
the dashboard reads it via /api/phases. Mirrors docs/ROADMAP.md.
"""

# status: "done" | "in_progress" | "todo"
PHASES: list[dict] = [
    {
        "id": 0,
        "name": "Foundations & contracts",
        "goal": "Interfaces exist before logic: artifact schemas, alpha-memory store, config, API, dashboard.",
        "status": "in_progress",
        "done_when": "Schemas import; a dummy artifact round-trips to the store and back; dashboard renders it.",
    },
    {
        "id": 1,
        "name": "Strategy DSL + Compiler",
        "goal": "A safe, structured strategy representation that validates and compiles to a runnable object.",
        "status": "done",
        "done_when": "3 hand-authored specs validate and compile.",
    },
    {
        "id": 2,
        "name": "Data layer + Experiment Runner",
        "goal": "A DSL strategy produces real PnL on real MT5 data (US30/NAS100).",
        "status": "todo",
        "done_when": "run(spec) returns a populated ExperimentResult on US30.",
    },
    {
        "id": 3,
        "name": "Falsification Gauntlet v0",
        "goal": "Automatically kill false alpha: purged CV, Deflated Sharpe, PBO, parameter stability.",
        "status": "todo",
        "done_when": "An overfit spec is KILLED; a robust one SURVIVES.",
    },
    {
        "id": 4,
        "name": "Alpha Memory + Scorecard",
        "goal": "Nothing is forgotten; strategies scored as vectors; re-exploration blocked by novelty.",
        "status": "todo",
        "done_when": "Re-submitting a known idea is flagged stale; every experiment has a scorecard.",
    },
    {
        "id": 5,
        "name": "Manual full loop",
        "goal": "Hand-written hypothesis -> system does the rest end-to-end (TANGIBILITY milestone).",
        "status": "todo",
        "done_when": "One CLI command takes a hypothesis to a stored, scored, judged result.",
    },
    {
        "id": 6,
        "name": "Research agents",
        "goal": "Claude generates hypotheses and DSL specs (novelty-gated, compiler-repair loop).",
        "status": "todo",
        "done_when": "Agents produce specs that pass validation without human edits.",
    },
    {
        "id": 7,
        "name": "Orchestrator",
        "goal": "The loop runs itself (AUTONOMY milestone — thin full loop alive).",
        "status": "todo",
        "done_when": "Unattended run produces hypotheses, kills most, surfaces >=1 survivor.",
    },
    {
        "id": 8,
        "name": "Deepen",
        "goal": "CPCV, regime discovery, capacity/execution realism, promotion pipeline, live adapter.",
        "status": "todo",
        "done_when": "Survivor promoted to paper/live with drift monitoring.",
    },
]


def progress() -> dict:
    done = sum(1 for p in PHASES if p["status"] == "done")
    in_progress = sum(1 for p in PHASES if p["status"] == "in_progress")
    total = len(PHASES)
    return {
        "done": done,
        "in_progress": in_progress,
        "total": total,
        "pct": round(100 * done / total),
    }
