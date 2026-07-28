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
        "status": "done",
        "done_when": "Schemas import; a dummy artifact round-trips to the store and back; dashboard renders it.",
        "process": [
            "Define the five immutable artifact contracts.",
            "Persist artifacts in SQLite or shared Supabase memory.",
            "Expose the same records through the API and GUI.",
        ],
        "output": "A replayable research ledger and stable interfaces between every subsystem.",
        "inspect": "Open any hypothesis and confirm its hypothesis, spec, experiment, scorecard, and verdict trail is intact.",
        "current_gap": "The local store works; restoring and operating the shared Supabase project is still an operational step.",
    },
    {
        "id": 1,
        "name": "Strategy DSL + Compiler",
        "goal": "A safe, structured strategy representation that validates and compiles to a runnable object.",
        "status": "done",
        "done_when": "3 hand-authored specs validate and compile.",
        "process": [
            "Synthesis expresses an idea as Alpha x Wrapper x Context.",
            "The validator rejects unknown features, unsafe expressions, and incoherent exits.",
            "The compiler turns the accepted spec into point-in-time backtest logic.",
        ],
        "output": "A versioned StrategySpec that is safe to test and can be diffed or replayed.",
        "inspect": "Check that the signal uses the claimed features and that the parameter grid tests a neighbourhood, not one magic value.",
        "current_gap": "Python DSL v0.3 is live; full Pine/MQL5 parity for structural exits remains open.",
    },
    {
        "id": 2,
        "name": "Data layer + Experiment Runner",
        "goal": "A DSL strategy produces real PnL on real MT5 data (US30/NAS100).",
        "status": "done",
        "done_when": "run(spec) returns a populated ExperimentResult on US30.",
        "process": [
            "Load the exact symbol/timeframe parquet available on this machine.",
            "Compute causal features and execute compiled entries and exits.",
            "Record trades, equity, gross metrics, and cost metrics when configured.",
        ],
        "output": "ExperimentResult: the unjudged facts from one historical test.",
        "inspect": "Review dataset, period, trade count, expectancy, drawdown, costs, and the equity path before trusting summary metrics.",
        "current_gap": "US30 is the proven base. More instruments need schema-matched data; final every-tick MT5 sign-off is still pre-deployment work.",
    },
    {
        "id": 3,
        "name": "Falsification Gauntlet v0",
        "goal": "Automatically kill false alpha: purged CV, Deflated Sharpe, PBO, parameter stability.",
        "status": "done",
        "done_when": "An overfit spec is KILLED; a robust one SURVIVES.",
        "process": [
            "Screen predictive edge across coherent holding-period families.",
            "Fail fast on leakage, then test walk-forward transfer and parameter stability.",
            "Stress trade ordering with Monte Carlo and correct for repeated trials with DSR/PBO.",
        ],
        "output": "A per-gate diagnostic scorecard and a SURVIVE or KILL verdict with reasons.",
        "inspect": "Find the first failed gate, compare its measured value with the threshold, then check which expensive gates were never reached.",
        "current_gap": "The gauntlet is operational; thresholds still need calibration as the real sample grows, and execution realism is not yet a promotion gate.",
    },
    {
        "id": 4,
        "name": "Alpha Memory + Scorecard",
        "goal": "Nothing is forgotten; strategies scored as vectors; re-exploration blocked by novelty.",
        "status": "done",
        "done_when": "Re-submitting a known idea is flagged stale; every experiment has a scorecard.",
        "process": [
            "Embed and categorise each hypothesis before testing.",
            "Reject near-duplicates and expose sparse categories as research whitespace.",
            "Attach behaviour, regime, correlation, and falsification evidence after testing.",
        ],
        "output": "A searchable alpha map containing wins, failures, lineage, and learned constraints.",
        "inspect": "Use novelty and edge-type filters to see what is crowded, what repeatedly fails, and where the map has not explored.",
        "current_gap": "The 2D map works, but normalized kill categories and stronger lineage navigation are still needed at 10k+ scale.",
    },
    {
        "id": 5,
        "name": "Manual full loop",
        "goal": "Hand-written hypothesis -> system does the rest end-to-end (TANGIBILITY milestone).",
        "status": "done",
        "done_when": "One CLI command takes a hypothesis to a stored, scored, judged result.",
        "process": [
            "Load a human-authored hypothesis and StrategySpec.",
            "Validate and block stale ideas unless explicitly overridden.",
            "Run, falsify, persist, and print the complete result trail.",
        ],
        "output": "A reproducible end-to-end test that proves the subsystem contracts connect.",
        "inspect": "Confirm every artifact ID links correctly and the final status matches the latest verdict.",
        "current_gap": "The manual path is reliable, but the GUI only launches Director runs rather than curated single-example runs.",
    },
    {
        "id": 6,
        "name": "Research agents",
        "goal": "Claude generates hypotheses and DSL specs (novelty-gated, compiler-repair loop).",
        "status": "done",
        "done_when": "Agents produce specs that pass validation without human edits.",
        "process": [
            "Hypothesis and Wildcard agents see coverage and whitespace, never performance.",
            "The deterministic Gatekeeper rejects duplicates before compute is spent.",
            "Synthesis compiles accepted claims and repairs exact validator failures.",
        ],
        "output": "Novel Hypothesis and valid StrategySpec artifacts, plus feature-gap requests when the DSL is insufficient.",
        "inspect": "Check model/provider telemetry, novelty, synthesis fidelity, repair count, and whether unsupported ideas became visible feature gaps.",
        "current_gap": "The loop works, but prompts and model routing are still being tuned; historical hypotheses predate proposer-model attribution.",
    },
    {
        "id": 7,
        "name": "Orchestrator",
        "goal": "The loop runs itself (AUTONOMY milestone — thin full loop alive).",
        "status": "done",
        "done_when": "Unattended run produces hypotheses, kills most, surfaces >=1 survivor.",
        "process": [
            "The Director allocates a hard budget across exploit, explore, and wildcard engines.",
            "Explore creates new candidates while Workers refine viable survivors and near-misses.",
            "Every decision and failure is appended to the research event ledger.",
        ],
        "output": "RunSummary, research trace, category health, and newly tested hypotheses.",
        "inspect": "Watch skipped candidates, provider failures, engine allocation, category pruning, survivor count, and worker outcomes.",
        "current_gap": "The Director is a working draft: GUI requests are blocking, jobs are not resumable, and the budget policy is fixed rather than adaptive.",
    },
    {
        "id": 8,
        "name": "Deepen",
        "goal": "CPCV, regime discovery, capacity/execution realism, promotion pipeline, live adapter.",
        "status": "in_progress",
        "done_when": "Survivor promoted to paper/live with drift monitoring.",
        "process": [
            "Improve survivors with wrapper-aware local optimisation and regime conditioning.",
            "Construct a cost-positive, low-correlation candidate portfolio.",
            "Export approved strategies, paper trade them, then monitor live drift and risk.",
        ],
        "output": "A cost-surviving candidate book and, eventually, monitored paper/live strategies.",
        "inspect": "Demand cost-adjusted expectancy, deployment parity, forward performance, drift limits, and a human promotion approval.",
        "current_gap": "Optimizer, regimes, costs, and portfolio construction exist; export parity, paper/live promotion, drift monitoring, and kill switches are not complete.",
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
