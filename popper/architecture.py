"""The architecture as data — the single source of truth the Control Center renders.

Defines every subsystem, the artifact that flows out of it, its build status, and the
roadmap phase that owns it. The GUI draws the whole map (and the navigable workspaces)
from this, so 'architecting the flow' means editing this file.
"""

from popper.memory import get_memory
from popper.phases import progress

# status: "built" | "partial" | "next" | "planned"
SUBSYSTEMS: list[dict] = [
    {
        "id": "data", "name": "Data & Features", "group": "input",
        "role": "Ingest MT5 (US30/NAS100) into clean OHLCV with session + regime tags; build the feature store.",
        "consumes": "MT5 raw feed", "produces": "Feature store", "phase": 2, "status": "planned",
    },
    {
        "id": "hypothesis", "name": "Hypothesis Engine", "group": "loop",
        "role": "Propose a falsifiable claim about a market mechanism. Novelty-gated against alpha memory.",
        "consumes": "Alpha memory", "produces": "Hypothesis", "phase": 6, "status": "planned",
    },
    {
        "id": "synthesis", "name": "Synthesis Agent", "group": "loop",
        "role": "Translate a hypothesis into a valid, structured Strategy DSL spec.",
        "consumes": "Hypothesis", "produces": "StrategySpec", "phase": 6, "status": "planned",
    },
    {
        "id": "dsl", "name": "Strategy DSL + Compiler", "group": "loop",
        "role": "Safe structured strategy representation; compiles to backtest, simulation, and live targets.",
        "consumes": "StrategySpec", "produces": "Compiled strategy", "phase": 1, "status": "next",
    },
    {
        "id": "runner", "name": "Experiment Runner", "group": "loop",
        "role": "Execute the compiled strategy over historical data and record the raw result.",
        "consumes": "Compiled strategy", "produces": "ExperimentResult", "phase": 2, "status": "planned",
    },
    {
        "id": "scorecard", "name": "Scorecard", "group": "loop",
        "role": "Evaluate beyond PnL: robustness, regime stability, capacity, execution realism, novelty.",
        "consumes": "ExperimentResult", "produces": "Scorecard", "phase": 4, "status": "partial",
    },
    {
        "id": "falsify", "name": "Falsification Gauntlet", "group": "loop",
        "role": "Try to disprove the alpha. Purged CV, Deflated Sharpe, PBO, parameter stability. The moat.",
        "consumes": "Scorecard", "produces": "Verdict", "phase": 3, "status": "planned",
    },
    {
        "id": "promote", "name": "Promotion / Production", "group": "output",
        "role": "Survivors go to paper, then live, with drift monitoring and a kill switch.",
        "consumes": "Verdict (promote)", "produces": "Live strategy", "phase": 8, "status": "planned",
    },
    {
        "id": "memory", "name": "Alpha Memory", "group": "cross",
        "role": "Record every hypothesis, experiment, and verdict. Score novelty; block re-exploration.",
        "consumes": "All artifacts", "produces": "History + novelty", "phase": 0, "status": "built",
    },
    {
        "id": "orchestrator", "name": "Orchestrator", "group": "cross",
        "role": "Conduct the loop: schedule cycles, route artifacts, manage budget/stop, wire feedback.",
        "consumes": "All subsystems", "produces": "Research cycles", "phase": 7, "status": "planned",
    },
]

# left-to-right flow of the core research loop (the rest are cross-cutting)
PIPELINE = ["data", "hypothesis", "synthesis", "dsl", "runner", "scorecard", "falsify", "promote"]
CROSS = ["memory", "orchestrator"]

ARTIFACTS: list[dict] = [
    {"name": "Hypothesis", "desc": "A falsifiable claim about a market mechanism + the features it needs."},
    {"name": "StrategySpec", "desc": "The hypothesis expressed in the Strategy DSL (signals/entry/exit/sizing)."},
    {"name": "ExperimentResult", "desc": "Raw output of a backtest: trades, equity curve, metrics."},
    {"name": "Scorecard", "desc": "Multi-metric evaluation vector — not a single PnL number."},
    {"name": "Verdict", "desc": "SURVIVE / KILL(reasons) / PROMOTE from the falsification gauntlet."},
]


def _metric(node_id: str, stats: dict) -> str | None:
    c = stats["counts"]
    bs = stats["hypotheses_by_status"]
    total = c["hypotheses"]
    table = {
        "hypothesis": f"{total} hypotheses",
        "synthesis": f"{c['specs']} specs",
        "dsl": f"{c['specs']} specs compiled",
        "runner": f"{c['experiments']} runs",
        "scorecard": f"{c['experiments']} scored",
        "falsify": f"{c['verdicts']} verdicts · {bs['killed']} killed",
        "promote": f"{bs['promoted']} promoted",
        "memory": f"{total + c['specs'] + c['experiments'] + c['verdicts']} records",
    }
    return table.get(node_id)


def architecture() -> dict:
    stats = get_memory().stats()
    nodes = []
    for s in SUBSYSTEMS:
        node = dict(s)
        node["metric"] = _metric(s["id"], stats)
        nodes.append(node)
    return {
        "subsystems": nodes,
        "pipeline": PIPELINE,
        "cross": CROSS,
        "artifacts": ARTIFACTS,
        "progress": progress(),
    }
