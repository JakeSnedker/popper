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
from collections import deque
from dataclasses import dataclass, field
from types import SimpleNamespace

from popper.agents import audit_fidelity, propose_batch, screen, synthesize, view
from popper.agents.llm import LLMProviderError
from popper.artifacts import StrategySpec
from popper.config import settings
from popper.dsl import DSLValidationError
from popper.experiments.runner import bars_for_spec, run
from popper.feature_gaps import record_feature_gaps
from popper.falsify import GauntletConfig
from popper.memory import complete_scorecard, get_memory
from popper.memory.map import edge_type
from popper.orchestrator.convergence import cluster_status, update_cluster
from popper.orchestrator.selection import rank_targets
from popper.research import anchor_experiment, evaluate

DEFAULT_BUDGET = {"exploit": 0.70, "explore": 0.20, "wildcard": 0.10}
K_DEAD = 2          # a category KILLing K times with no survivor is pruned dead (explore-side)
SURVIVE = ("survive", "promote")


def _candidates(c: dict) -> list:
    """Refine targets for one category: survivors (tier-0) + captured near-misses."""
    cands = [{"spec": sp, "metrics": {}, "verdict": "survive"} for sp in c.get("survivors", [])]
    return cands + c.get("near_misses", [])


def _ranked_available(c: dict, cfg) -> list:
    """Ranked refine targets whose spec has NOT yet been taken to convergence.
    A refined spec's id lives in c['refined_ids']; a freshly-captured near-miss (new
    id) is therefore always available — that's what revives a quiet category."""
    done = c.get("refined_ids", set())
    return [t for t in rank_targets(_candidates(c), cfg=cfg) if t["spec"].id not in done]


def _live(categories: dict, cfg) -> list:
    """Categories still worth an exploit worker: no keeper has been PROMOTED (locked
    in), and at least one plausible target (survivor or rescuable near-miss) is still
    UNREFINED. A category only goes quiet once EVERY alpha it has found is refined —
    not after an arbitrary kill count — and any new near-miss revives it (B2/B4).
    ponytail: explore kill-count no longer prunes exploit; a truly-dead category has
    no rankable target left, so _ranked_available empties out and it drops naturally."""
    return [et for et, c in categories.items()
            if cluster_status(c) != "promote" and _ranked_available(c, cfg)]


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
    llm_usage: dict = field(default_factory=dict)
    provider_paused: bool = False
    retry_after: int = 0
    stopped_reason: str = ""

    def category_report(self) -> dict:
        return {et: {"survivors": len(c["survivors"]), "kills": c["kills"], "dead": c["dead"]}
                for et, c in self.categories.items()}


def _event(mem, run_id: str, iteration: int, engine: str, role: str, stage: str,
           status: str, message: str, artifact_ids: dict | None = None,
           payload: dict | None = None) -> None:
    if not run_id:
        return
    try:
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
    except Exception as e:  # noqa: BLE001 — the timeline is observability, not research:
        # a transient event-log insert failure must NEVER kill an unattended run.
        print(f"[warn] research-event log dropped ({type(e).__name__}: {e})", flush=True)


def _llm_mark(llm) -> int:
    return llm.mark() if hasattr(llm, "mark") else 0


def _llm_trace(llm, mark: int) -> list[dict]:
    return llm.records_since(mark) if hasattr(llm, "records_since") else []


def _pick(budget: dict, spent: dict) -> str:
    """Largest-deficit allocation — honors the split deterministically.
    ponytail: a counter, not a scheduler; swap for a bandit if the dial goes adaptive."""
    total = sum(spent.values()) or 1
    return max(budget, key=lambda e: budget[e] - spent[e] / total)


def _explore(mem, llm, run_fn, period, cfg, wildcard: bool,
             run_id: str = "", iteration: int = 0, engine: str = "explore",
             hypothesis=None, batch_size: int = 1) -> dict:
    """One candidate through the blind pipeline; surplus batch items are queued."""
    pending = []
    mark = _llm_mark(llm)
    try:
        if hypothesis is None:
            _event(mem, run_id, iteration, engine, "hypothesis", "propose", "running",
                   f"asking for a batch of {batch_size} blind claims")
            generated = propose_batch(
                view("hypothesis", mem), llm, count=batch_size, wildcard=wildcard,
            )
            hypothesis, pending = generated[0], generated[1:]
        _event(mem, run_id, iteration, engine, "hypothesis", "propose", "ok",
               hypothesis.claim,
               payload={"required_features": hypothesis.required_features,
                        "wildcard": wildcard, "batch_surplus": len(pending),
                        "llm_calls": _llm_trace(llm, mark)})
    except LLMProviderError as e:
        reason = f"hypothesis provider unavailable: {e}"
        _event(mem, run_id, iteration, engine, "hypothesis", "propose", "provider_error",
               reason, payload={"llm_calls": _llm_trace(llm, mark)})
        raise
    except Exception as e:
        reason = f"hypothesis failed: {type(e).__name__}: {e}"
        _event(mem, run_id, iteration, engine, "hypothesis", "propose", "error", reason,
               payload={"llm_calls": _llm_trace(llm, mark)})
        return {"ok": False, "reason": reason, "pending": pending}

    gate = screen(hypothesis, view("gatekeeper", mem))
    hypothesis = gate["hypothesis"]  # reuse its one embedding through evaluate/store
    gate_payload = {"novelty": gate["novelty"], "nearest_id": gate["nearest_id"],
                    "category": gate["category"]}
    if not gate["accepted"]:
        _event(mem, run_id, iteration, engine, "gatekeeper", "screen", "rejected",
               gate["reason"], payload=gate_payload)
        return {"ok": False, "reason": gate["reason"], "pending": pending}
    _event(mem, run_id, iteration, engine, "gatekeeper", "screen", "ok",
           "hypothesis accepted as novel", payload=gate_payload)

    mark = _llm_mark(llm)
    try:
        _event(mem, run_id, iteration, engine, "synthesis", "compile", "running",
               "translating hypothesis into faithful Strategy DSL")
        spec = synthesize(
            hypothesis, llm, enforce_local_context=(run_fn is run),
        )
        _event(mem, run_id, iteration, engine, "synthesis", "compile", "ok",
               f"compiled DSL spec {spec.name}",
               payload={
                   "features": spec.alpha.features,
                   "higher_timeframe_features": spec.alpha.higher_timeframe_features,
                   "llm_calls": _llm_trace(llm, mark),
               })
    except LLMProviderError as e:
        reason = f"synthesis provider unavailable: {e}"
        _event(mem, run_id, iteration, engine, "synthesis", "compile", "provider_error",
               reason, payload={"llm_calls": _llm_trace(llm, mark)})
        raise
    except Exception as e:
        if isinstance(e, DSLValidationError):
            record_feature_gaps(mem, e, source="synthesis", hypothesis=hypothesis)
        reason = f"synthesis failed: {type(e).__name__}: {e}"
        _event(mem, run_id, iteration, engine, "synthesis", "compile", "error", reason,
               payload={"llm_calls": _llm_trace(llm, mark)})
        return {"ok": False, "reason": reason, "pending": pending}

    try:
        _event(mem, run_id, iteration, engine, "gauntlet", "evaluate", "running",
               "running backtest and falsification gauntlet")
        summary = evaluate(hypothesis, spec, mem=mem, period=period, cfg=cfg, run_fn=run_fn)
    except Exception as e:
        reason = f"evaluation failed: {type(e).__name__}: {e}"
        _event(mem, run_id, iteration, engine, "gauntlet", "evaluate", "error", reason)
        return {"ok": False, "reason": reason, "pending": pending}
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

    if (summary["verdict"] in SURVIVE and settings.llm_survivor_audit
            and hasattr(llm, "supports_role") and llm.supports_role("audit")):
        mark = _llm_mark(llm)
        try:
            summary["semantic_audit"] = audit_fidelity(hypothesis, stored, llm)
            status = "ok" if summary["semantic_audit"]["faithful"] else "warning"
            _event(mem, run_id, iteration, engine, "audit", "semantic_fidelity", status,
                   summary["semantic_audit"]["reason"],
                   artifact_ids={"hypothesis_id": summary["hypothesis_id"],
                                 "spec_id": summary["spec_id"]},
                   payload={"audit": summary["semantic_audit"],
                            "llm_calls": _llm_trace(llm, mark)})
        except Exception as e:  # audit is advisory; statistics remain authoritative
            summary["semantic_audit"] = {"faithful": None, "reason": str(e)[:1000]}
            _event(mem, run_id, iteration, engine, "audit", "semantic_fidelity", "unavailable",
                   str(e), payload={"llm_calls": _llm_trace(llm, mark)})

    return {"ok": True, "verdict": summary["verdict"],
            "category": gate["category"]["edge_type"], "summary": summary,
            "spec": stored, "pending": pending}


def _worker_refine(mem, spec: StrategySpec, run_fn, period, cfg,
                   run_id: str = "", iteration: int = 0, engine: str = "exploit"):
    """A Worker (FULL view — sees its category's results, intentional tunnelling)
    refines one survivor via the guided local optimizer (roadmap B1) and
    re-falsifies. Records a new experiment/scorecard/verdict under the same
    hypothesis; does not mutate the parent's status."""
    _event(mem, run_id, iteration, engine, "worker", "refine", "running",
           f"refining survivor spec {spec.id}", artifact_ids={"spec_id": spec.id})
    _ = view("worker", mem)  # the full view is the worker's grant (spec/02); read-through below
    from popper.falsify import judge  # local import avoids a heavy import at module load
    from popper.orchestrator.optimizer import optimize
    best, _, _, history = optimize(spec, run_fn=run_fn, period=period, cfg=cfg)
    variant = mem.add_spec(best.model_copy(update={"id": None, "name": f"{spec.name}_r"}))
    # ponytail: one extra judge of the winner so its anchor experiment persists
    # under the stored variant id (search candidates are judged memory-free)
    sc, v = judge(variant, run_fn=run_fn, period=period, cfg=cfg, mem=mem)
    sc.diagnostics["refine_kind"] = history[-1]["refine_kind"]  # book prefers wrapper_only
    exp = anchor_experiment(mem, variant.hypothesis_id, sc.experiment_id)
    h = mem.get_hypothesis(variant.hypothesis_id)
    bars = bars_for_spec(variant, period) if run_fn is run else None  # real regime score on the exploit path too
    complete_scorecard(sc, mem, h, exp, bars=bars)
    mem.add_scorecard(sc)
    mem.add_verdict(v)
    _event(mem, run_id, iteration, engine, "worker", "refine", v.type.value,
           f"worker variant {variant.id} returned {v.type.value.upper()}",
           artifact_ids={"hypothesis_id": variant.hypothesis_id, "spec_id": variant.id,
                         "experiment_id": sc.experiment_id, "verdict_id": v.id},
           payload={"optimizer": history[-1]})
    # (verdict, optimizer done-entry for B4 convergence, the stored refined variant)
    return v.type.value, history[-1], variant


def _stored_spec(mem, hypothesis_id: str) -> StrategySpec:
    return StrategySpec(**mem.record(hypothesis_id)["specs"][0])


async def _run(mem, llm, run_fn, period, cfg, iterations, budget, run_id: str) -> RunSummary:
    s = RunSummary(iterations=iterations, run_id=run_id, budget_spent={k: 0 for k in budget})
    pending = {"explore": deque(), "wildcard": deque()}
    run_llm_mark = _llm_mark(llm)
    _event(mem, run_id, 0, "director", "director", "run", "running",
           f"starting research run with {iterations} iterations", payload={"budget": budget})

    for i in range(1, iterations + 1):
        engine = _pick(budget, s.budget_spent)
        s.budget_spent[engine] += 1
        _event(mem, run_id, i, engine, "director", "budget", "ok",
               f"selected {engine} engine", payload={"budget_spent": dict(s.budget_spent)})
        live = _live(s.categories, cfg)

        if engine == "exploit" and live:
            # Workers fan out, one per live category, each refining that category's
            # TOP-RANKED still-UNREFINED target (survivor first, else a rescuable
            # cost-killed/near-miss edge — roadmap B2). ponytail: sqlite ids come from a
            # COUNT (race-prone under true parallel inserts); safe at 1 worker/category.
            targets = {et: _ranked_available(s.categories[et], cfg)[0]["spec"] for et in live}
            outcomes = await asyncio.gather(*[
                asyncio.to_thread(_worker_refine, mem, targets[et],
                                  run_fn, period, cfg, run_id, i, engine)
                for et in live], return_exceptions=True)
            for et, out in zip(live, outcomes):
                # every ATTEMPTED target is exhausted (its local optimum is deterministic);
                # don't re-select it — the category stays live only while OTHER/NEW targets remain.
                s.categories[et].setdefault("refined_ids", set()).add(targets[et].id)
                if isinstance(out, Exception):
                    s.skipped += 1
                    reason = f"worker failed: {type(out).__name__}: {out}"
                    s.failures.append(reason)
                    _event(mem, run_id, i, engine, "worker", "refine", "error", reason)
                else:
                    verdict, done_entry, variant = out
                    s.worker_runs += 1
                    update_cluster(s.categories[et], verdict, done_entry)  # B4 bookkeeping
                    if verdict in SURVIVE:
                        # a rescued/-improved variant is a NEW, unrefined target -> keeps the
                        # loop going until the optimizer stops producing survivors
                        s.categories[et].setdefault("survivors", []).append(variant)
            continue

        # Explore, wildcard, or exploit with nothing live yet. A creative call
        # fills a small queue; each queued idea still gates/evaluates independently.
        queue_name = "wildcard" if engine == "wildcard" else "explore"
        queued = pending[queue_name].popleft() if pending[queue_name] else None
        try:
            res = await asyncio.to_thread(
                _explore, mem, llm, run_fn, period, cfg,
                engine == "wildcard", run_id, i, engine, queued,
                settings.llm_batch_size if queued is None else 1,
            )
        except LLMProviderError as e:
            s.provider_paused = True
            s.retry_after = e.retry_after or settings.llm_provider_backoff_seconds
            s.stopped_reason = str(e)
            s.failures.append(s.stopped_reason)
            _event(mem, run_id, i, engine, "director", "provider_circuit", "paused",
                   s.stopped_reason, payload={"retry_after": s.retry_after})
            break
        except Exception as e:
            res = {"ok": False,
                   "reason": f"candidate crashed boundary: {type(e).__name__}: {e}",
                   "pending": []}
        pending[queue_name].extend(res.get("pending", []))
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
            # capture the killed spec as a rescue target — the exploit engine will
            # re-rank it (cost-killed edges rank high, roadmap B2) and try to recover it.
            st = res["summary"]["stats"]
            metrics = {"expectancy": st.get("expectancy", 0.0)}
            if st.get("expectancy_net") is not None:
                metrics["expectancy_net"] = st["expectancy_net"]
            cat.setdefault("near_misses", []).append({
                "spec": res["spec"], "metrics": metrics,
                "verdict": res["verdict"], "reasons": res["summary"]["reasons"],
                # ponytail: SimpleNamespace exposes just the two fields classify() reads;
                # swap for the real Scorecard if selection ever needs more of it.
                "scorecard": SimpleNamespace(pbo=st["pbo"], parameter_stability=st["param_stability"]),
            })
            if cat["kills"] >= K_DEAD and not cat["survivors"]:
                cat["dead"] = True  # explore-side prune (no more NEW candidates here);
                _event(mem, run_id, i, engine, "director", "prune", "ok",  # exploit may still rescue a near-miss
                       f"pruned dead category {res['category']}", payload={"category": res["category"]})

    if hasattr(llm, "stats"):
        s.llm_usage = llm.stats(run_llm_mark)
    final_status = "paused" if s.provider_paused else "done"
    final_message = s.stopped_reason or "research run complete"
    _event(mem, run_id, iterations, "director", "director", "run", final_status,
           final_message, payload={"proposed": s.proposed, "killed": s.killed,
                                   "survived": s.survived, "worker_runs": s.worker_runs,
                                   "skipped": s.skipped, "llm_usage": s.llm_usage,
                                   "retry_after": s.retry_after})
    return s


def run_research(mem=None, llm=None, run_fn=run, period=None, cfg: GauntletConfig | None = None,
                 iterations: int = 10, budget: dict | None = None, run_id: str | None = None) -> RunSummary:
    """Unattended research run. Injectable for offline/deterministic testing;
    defaults to the real alpha memory + Anthropic client + US30 runner."""
    from popper.agents import get_llm
    mem = mem or get_memory()
    llm = llm or get_llm()
    budget = budget or dict(DEFAULT_BUDGET)
    run_id = run_id or f"R-{uuid.uuid4().hex[:8]}"
    return asyncio.run(_run(mem, llm, run_fn, period, cfg, iterations, budget, run_id))
