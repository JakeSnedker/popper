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
from dataclasses import dataclass, field

from popper.agents import propose, screen, synthesize, view
from popper.artifacts import StrategySpec
from popper.dsl import DSLValidationError
from popper.experiments.runner import run
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
    proposed: int = 0
    killed: int = 0
    survived: int = 0
    worker_runs: int = 0
    survivors: list = field(default_factory=list)       # explore summary dicts
    categories: dict = field(default_factory=dict)      # etype -> {kills, survivors, dead}
    budget_spent: dict = field(default_factory=dict)    # engine -> iterations

    def category_report(self) -> dict:
        return {et: {"survivors": len(c["survivors"]), "kills": c["kills"], "dead": c["dead"]}
                for et, c in self.categories.items()}


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


def _explore(mem, llm, run_fn, period, cfg, wildcard: bool) -> dict:
    """One explore/wildcard candidate through the blind pipeline. Returns a result
    dict; on gatekeeper reject or synthesis failure, {'ok': False}."""
    h = propose(view("hypothesis", mem), llm, wildcard=wildcard)
    gate = screen(h, view("gatekeeper", mem))          # blind: novelty + category
    if not gate["accepted"]:
        return {"ok": False, "reason": gate["reason"]}
    try:
        spec = synthesize(h, llm)                       # blind: hypothesis + DSL only
    except DSLValidationError as e:
        return {"ok": False, "reason": f"synthesis failed: {e}"}
    summary = evaluate(h, spec, mem=mem, period=period, cfg=cfg, run_fn=run_fn)
    stored = _stored_spec(mem, summary["hypothesis_id"])
    return {"ok": True, "verdict": summary["verdict"],
            "category": gate["category"]["edge_type"], "summary": summary, "spec": stored}


def _worker_refine(mem, spec: StrategySpec, run_fn, period, cfg) -> str:
    """A Worker (FULL view — sees its category's results, intentional tunnelling)
    refines one survivor spec and re-falsifies. Records a new experiment/scorecard/
    verdict under the same hypothesis; does not mutate the parent's status."""
    _ = view("worker", mem)  # the full view is the worker's grant (spec/02); read-through below
    variant = mem.add_spec(_mutate(spec))
    from popper.falsify import judge  # local import avoids a heavy import at module load
    sc, v = judge(variant, run_fn=run_fn, period=period, cfg=cfg, mem=mem)
    exp = anchor_experiment(mem, variant.hypothesis_id, sc.experiment_id)
    h = mem.get_hypothesis(variant.hypothesis_id)
    complete_scorecard(sc, mem, h, exp)
    mem.add_scorecard(sc)
    mem.add_verdict(v)
    return v.type.value


def _stored_spec(mem, hypothesis_id: str) -> StrategySpec:
    return StrategySpec(**mem.record(hypothesis_id)["specs"][0])


async def _run(mem, llm, run_fn, period, cfg, iterations, budget) -> RunSummary:
    s = RunSummary(iterations=iterations, budget_spent={k: 0 for k in budget})

    for _ in range(iterations):
        engine = _pick(budget, s.budget_spent)
        s.budget_spent[engine] += 1
        live = [et for et, c in s.categories.items() if c["survivors"] and not c["dead"]]

        if engine == "exploit" and live:
            # Workers fan out, one per live category. ponytail: sqlite ids come from a
            # COUNT (race-prone under true parallel inserts); safe at 1 worker/category
            # here — give ids an atomic source before scaling worker fan-out.
            outcomes = await asyncio.gather(*[
                asyncio.to_thread(_worker_refine, mem, s.categories[et]["survivors"][0], run_fn, period, cfg)
                for et in live])
            s.worker_runs += len(outcomes)
            continue

        # explore, wildcard, or exploit with nothing live yet -> generate a candidate
        res = await asyncio.to_thread(_explore, mem, llm, run_fn, period, cfg, engine == "wildcard")
        if not res["ok"]:
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

    return s


def run_research(mem=None, llm=None, run_fn=run, period=None, cfg: GauntletConfig | None = None,
                 iterations: int = 10, budget: dict | None = None) -> RunSummary:
    """Unattended research run. Injectable for offline/deterministic testing;
    defaults to the real alpha memory + Anthropic client + US30 runner."""
    from popper.agents import AnthropicClient
    mem = mem or get_memory()
    llm = llm or AnthropicClient()
    budget = budget or dict(DEFAULT_BUDGET)
    return asyncio.run(_run(mem, llm, run_fn, period, cfg, iterations, budget))
