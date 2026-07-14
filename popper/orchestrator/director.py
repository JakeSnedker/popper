"""The Director — the unattended research loop (spec/02, Phase 7 DRAFT).

Runs N iterations. Each iteration the Director picks an engine by a HARD budget
split (default 70 exploit / 20 explore / 10 wildcard) and acts:

  explore  — Hypothesis(blind view) -> Gatekeeper -> Synthesis -> gauntlet -> map
  wildcard — same, but the Hypothesis agent is steered OFF-map (whitespace)
  exploit  — Workers (one per LIVE category, async) refine a survivor in-place

It holds the explore/exploit dial as the single `budget` tunable, prunes dead
categories (all candidates KILL after K attempts, no survivor), and returns a
RunSummary. Everything is injectable (mem, llm, run_fn) so a run is offline and
deterministic under a stub.

Substrate: plain asyncio + the artifact contracts — no framework (spec DECISION).
"""

import asyncio
import uuid
from dataclasses import dataclass, field

from popper.agents import propose, screen, synthesize, view
from popper.artifacts import StrategySpec
from popper.dsl import DSLValidationError
from popper.experiments.runner import run
from popper.feature_gaps import record_feature_gaps
from popper.falsify import GauntletConfig
from popper.memory import complete_scorecard, get_memory
from popper.memory.map import edge_type
from popper.research import anchor_experiment, evaluate

DEFAULT_BUDGET = {"exploit": 0.70, "explore": 0.20, "wildcard": 0.10}
K_DEAD = 2          # a category KILLing K times with no survivor is pruned dead
SURVIVE = ("survive", "promote")


@dataclass
class RunSummary:
    iterations: int
    run_id: str = ""
    proposed: int = 0
    killed: int = 0
    survived: int = 0
    worker_runs: int = 0
    skipped: int = 0
    failures: list = field(default_factory=list)        # non-fatal candidate/worker failures
    survivors: list = field(default_factory=list)       # explore summary dicts
    categories: dict = field(default_factory=dict)      # etype -> {kills, survivors, dead}
    budget_spent: dict = field(default_factory=dict)    # engine -> iterations

    def category_report(self) -> dict:
        return {et: {"survivors": len(c["survivors"]), "kills": c["kills"], "dead": c["dead"]}
                for et, c in self.categories.items()}


def _event(mem, run_id: str, iteration: int, engine: str, role: str, stage: str,
           status: str, message: str, artifact_ids: dict | None = None,
           payload: dict | None = None) -> None:
    if not run_id:
        return
    mem.add_research_event({
        "run_id": run_id,
        "iteration": iteration,
        "engine": engine,
        "role": role,
        "stage": stage,
        "status": status,
        "message": message,
        "artifact_ids": artifact_ids or {},
        "payload": payload or {},
    })


def _pick(budget: dict, spent: dict) -> str:
    """Largest-deficit allocation — honors the split deterministically.
    ponytail: a counter, not a scheduler; swap for a bandit if the dial goes adaptive."""
    total = sum(spent.values()) or 1
    return max(budget, key=lambda e: budget[e] - spent[e] / total)


def _mutate(spec: StrategySpec) -> StrategySpec:
    """Worker refinement: nudge numeric param grids ±10% (intentional local search).
    ponytail: a flat param nudge; richer wrapper/param mutation is Jake's to tune."""
    ps = {k: [round(x * 1.1, 4) for x in g] if all(isinstance(x, (int, float)) for x in g) else g
          for k, g in spec.param_space.items()}
    return spec.model_copy(update={"id": None, "param_space": ps, "name": f"{spec.name}_r"})


def _explore(mem, llm, run_fn, period, cfg, wildcard: bool,
             run_id: str = "", iteration: int = 0, engine: str = "explore") -> dict:
    """One explore/wildcard candidate through the blind pipeline. Returns a result
    dict; on gatekeeper reject or synthesis failure, {'ok': False}."""
    try:
        _event(mem, run_id, iteration, engine, "hypothesis", "propose", "running",
               "asking the hypothesis agent for a new claim")
        h = propose(view("hypothesis", mem), llm, wildcard=wildcard)
        _event(mem, run_id, iteration, engine, "hypothesis", "propose", "ok",
               h.claim, payload={"required_features": h.required_features, "wildcard": wildcard})
    except Exception as e:
        reason = f"hypothesis failed: {type(e).__name__}: {e}"
        _event(mem, run_id, iteration, engine, "hypothesis", "propose", "error", reason)
        return {"ok": False, "reason": reason}

    gate = screen(h, view("gatekeeper", mem))          # blind: novelty + category
    gate_payload = {"novelty": gate["novelty"], "category": gate["category"]}
    if not gate["accepted"]:
        _event(mem, run_id, iteration, engine, "gatekeeper", "screen", "rejected",
               gate["reason"], payload=gate_payload)
        return {"ok": False, "reason": gate["reason"]}
    _event(mem, run_id, iteration, engine, "gatekeeper", "screen", "ok",
           "hypothesis accepted as novel", payload=gate_payload)

    try:
        _event(mem, run_id, iteration, engine, "synthesis", "compile", "running",
               "translating hypothesis into Strategy DSL")
        spec = synthesize(h, llm, enforce_local_context=(run_fn is run))  # blind: hypothesis + DSL only
        _event(mem, run_id, iteration, engine, "synthesis", "compile", "ok",
               f"compiled DSL spec {spec.name}", payload={"features": spec.alpha.features})
    except Exception as e:
        if isinstance(e, DSLValidationError):
            record_feature_gaps(mem, e, source="synthesis", hypothesis=h)
        reason = f"synthesis failed: {type(e).__name__}: {e}"
        _event(mem, run_id, iteration, engine, "synthesis", "compile", "error", reason)
        return {"ok": False, "reason": reason}

    try:
        _event(mem, run_id, iteration, engine, "gauntlet", "evaluate", "running",
               "running backtest and falsification gauntlet")
        summary = evaluate(h, spec, mem=mem, period=period, cfg=cfg, run_fn=run_fn)
    except Exception as e:
        reason = f"evaluation failed: {type(e).__name__}: {e}"
        _event(mem, run_id, iteration, engine, "gauntlet", "evaluate", "error", reason)
        return {"ok": False, "reason": reason}
    stored = _stored_spec(mem, summary["hypothesis_id"])
    _event(mem, run_id, iteration, engine, "gauntlet", "evaluate", summary["verdict"],
           f"verdict {summary['verdict'].upper()}: {', '.join(summary['reasons']) or 'passed gates'}",
           artifact_ids={
               "hypothesis_id": summary["hypothesis_id"],
               "spec_id": summary["spec_id"],
               "experiment_id": summary["experiment_id"],
               "scorecard_id": summary["scorecard_id"],
               "verdict_id": summary["verdict_id"],
           }, payload={"stats": summary["stats"]})
    return {"ok": True, "verdict": summary["verdict"],
            "category": gate["category"]["edge_type"], "summary": summary, "spec": stored}


def _worker_refine(mem, spec: StrategySpec, run_fn, period, cfg,
                   run_id: str = "", iteration: int = 0, engine: str = "exploit") -> str:
    """A Worker (FULL view — sees its category's results, intentional tunnelling)
    refines one survivor spec and re-falsifies. Records a new experiment/scorecard/
    verdict under the same hypothesis; does not mutate the parent's status."""
    _event(mem, run_id, iteration, engine, "worker", "refine", "running",
           f"refining survivor spec {spec.id}", artifact_ids={"spec_id": spec.id})
    _ = view("worker", mem)  # the full view is the worker's grant (spec/02); read-through below
    variant = mem.add_spec(_mutate(spec))
    from popper.falsify import judge  # local import avoids a heavy import at module load
    sc, v = judge(variant, run_fn=run_fn, period=period, cfg=cfg, mem=mem)
    exp = anchor_experiment(mem, variant.hypothesis_id, sc.experiment_id)
    h = mem.get_hypothesis(variant.hypothesis_id)
    complete_scorecard(sc, mem, h, exp)
    mem.add_scorecard(sc)
    mem.add_verdict(v)
    _event(mem, run_id, iteration, engine, "worker", "refine", v.type.value,
           f"worker variant {variant.id} returned {v.type.value.upper()}",
           artifact_ids={"hypothesis_id": variant.hypothesis_id, "spec_id": variant.id,
                         "experiment_id": sc.experiment_id, "verdict_id": v.id})
    return v.type.value


def _stored_spec(mem, hypothesis_id: str) -> StrategySpec:
    return StrategySpec(**mem.record(hypothesis_id)["specs"][0])


async def _run(mem, llm, run_fn, period, cfg, iterations, budget, run_id: str) -> RunSummary:
    s = RunSummary(iterations=iterations, run_id=run_id, budget_spent={k: 0 for k in budget})
    _event(mem, run_id, 0, "director", "director", "run", "running",
           f"starting research run with {iterations} iterations", payload={"budget": budget})

    for i in range(1, iterations + 1):
        engine = _pick(budget, s.budget_spent)
        s.budget_spent[engine] += 1
        _event(mem, run_id, i, engine, "director", "budget", "ok",
               f"selected {engine} engine", payload={"budget_spent": dict(s.budget_spent)})
        live = [et for et, c in s.categories.items() if c["survivors"] and not c["dead"]]

        if engine == "exploit" and live:
            # Workers fan out, one per live category. ponytail: sqlite ids come from a
            # COUNT (race-prone under true parallel inserts); safe at 1 worker/category
            # here — give ids an atomic source before scaling worker fan-out.
            outcomes = await asyncio.gather(*[
                asyncio.to_thread(_worker_refine, mem, s.categories[et]["survivors"][0],
                                  run_fn, period, cfg, run_id, i, engine)
                for et in live], return_exceptions=True)
            for out in outcomes:
                if isinstance(out, Exception):
                    s.skipped += 1
                    reason = f"worker failed: {type(out).__name__}: {out}"
                    s.failures.append(reason)
                    _event(mem, run_id, i, engine, "worker", "refine", "error", reason)
                else:
                    s.worker_runs += 1
            continue

        # explore, wildcard, or exploit with nothing live yet -> generate a candidate
        try:
            res = await asyncio.to_thread(_explore, mem, llm, run_fn, period, cfg,
                                          engine == "wildcard", run_id, i, engine)
        except Exception as e:
            res = {"ok": False, "reason": f"candidate crashed boundary: {type(e).__name__}: {e}"}
        if not res["ok"]:
            s.skipped += 1
            reason = res.get("reason", "unknown candidate failure")
            s.failures.append(reason)
            _event(mem, run_id, i, engine, "director", "candidate", "skipped", reason)
            continue
        s.proposed += 1
        cat = s.categories.setdefault(res["category"], {"kills": 0, "survivors": [], "dead": False})
        if res["verdict"] in SURVIVE:
            s.survived += 1
            cat["survivors"].append(res["spec"])
            s.survivors.append(res["summary"])
        else:
            s.killed += 1
            cat["kills"] += 1
            if cat["kills"] >= K_DEAD and not cat["survivors"]:
                cat["dead"] = True  # pruned: workers are never allocated here
                _event(mem, run_id, i, engine, "director", "prune", "ok",
                       f"pruned dead category {res['category']}", payload={"category": res["category"]})

    _event(mem, run_id, iterations, "director", "director", "run", "done",
           "research run complete", payload={"proposed": s.proposed, "killed": s.killed,
                                             "survived": s.survived, "worker_runs": s.worker_runs,
                                             "skipped": s.skipped})
    return s


def run_research(mem=None, llm=None, run_fn=run, period=None, cfg: GauntletConfig | None = None,
                 iterations: int = 10, budget: dict | None = None, run_id: str | None = None) -> RunSummary:
    """Unattended research run. Injectable for offline/deterministic testing;
    defaults to the real alpha memory + Anthropic client + US30 runner."""
    from popper.agents import AnthropicClient
    mem = mem or get_memory()
    llm = llm or AnthropicClient()
    budget = budget or dict(DEFAULT_BUDGET)
    run_id = run_id or f"R-{uuid.uuid4().hex[:8]}"
    return asyncio.run(_run(mem, llm, run_fn, period, cfg, iterations, budget, run_id))
