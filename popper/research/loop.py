"""The manual full loop (spec/06 phase 5 — the tangibility milestone).

run_loop(path) takes ONE hand-written hypothesis file through the whole chain:

  YAML -> Hypothesis + StrategySpec -> DSL validate -> alpha memory (novelty/stale)
       -> falsification gauntlet -> map-completed Scorecard -> Verdict -> status

The file is a single YAML with two blocks:

  hypothesis:           # the falsifiable claim
    claim: "..."
    mechanism: "..."    # WHY the edge should exist
    rationale: "..."    # optional
  spec:                 # the strategy-spec schema (popper/dsl/examples/),
    name: ...           # minus hypothesis_id — linked to the stored H-#### here
    alpha: {...}
    ...

`required_features` defaults to the spec's alpha features when omitted.
A stale hypothesis (near-duplicate of a prior idea) still runs — the human
asked — but the summary flags it.
"""

from pathlib import Path

import yaml

from popper.artifacts import ExperimentResult, Hypothesis, StrategySpec
from popper.artifacts.enums import HypothesisStatus, VerdictType
from popper.dsl import DSLValidationError, validate_spec
from popper.dsl.validator import validate_local_data_context
from popper.experiments.runner import run
from popper.feature_gaps import record_feature_gaps
from popper.falsify import GauntletConfig, calculate_edge, judge
from popper.memory import complete_scorecard, get_memory
from popper.memory.map import STALE

_STATUS = {
    VerdictType.KILL: HypothesisStatus.KILLED,
    VerdictType.SURVIVE: HypothesisStatus.SURVIVED,
    VerdictType.PROMOTE: HypothesisStatus.PROMOTED,
}


def load_hypothesis_file(path: str | Path) -> tuple[Hypothesis, StrategySpec]:
    """Parse the combined hypothesis+spec YAML. Raises ValueError on bad shape;
    pydantic/DSL errors surface from the models themselves."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "hypothesis" not in data or "spec" not in data:
        raise ValueError(f"{path}: expected top-level 'hypothesis:' and 'spec:' blocks")
    spec = StrategySpec.model_validate({"hypothesis_id": "", **data["spec"]})
    h = Hypothesis.model_validate({"required_features": spec.alpha.features, **data["hypothesis"]})
    return h, spec


def run_loop(
    path: str | Path,
    period: tuple[str, str] | None = None,
    cfg: GauntletConfig | None = None,
    mem=None,
    run_fn=run,
    bars=None,
    allow_stale: bool = False,
    costs: dict | None = None,
) -> dict:
    """Execute the full chain for one hypothesis file; return a summary dict.
    Everything is persisted to alpha memory (default: the configured store)."""
    mem = mem or get_memory()
    h, spec = load_hypothesis_file(path)
    return evaluate(h, spec, mem=mem, period=period, cfg=cfg, run_fn=run_fn, bars=bars,
                    allow_stale=allow_stale, costs=costs)


def evaluate(
    h: Hypothesis,
    spec: StrategySpec,
    mem=None,
    period: tuple[str, str] | None = None,
    cfg: GauntletConfig | None = None,
    run_fn=run,
    bars=None,
    allow_stale: bool = False,
    costs: dict | None = None,
) -> dict:
    """Store + falsify one (hypothesis, spec) pair and return the summary dict.
    The shared core of the manual loop AND the Director's explore engine: a bad
    spec is rejected before anything is stored (validate first)."""
    mem = mem or get_memory()
    try:
        validate_spec(spec)  # DSLValidationError surfaces to the caller before anything is stored
        if run_fn is run:
            validate_local_data_context(spec)  # fail before storage if local parquet cannot serve it
    except DSLValidationError as e:
        record_feature_gaps(mem, e, source="manual/evaluate", hypothesis=h, spec=spec)
        raise

    nearest_id, nearest_sim = mem.nearest(h)
    stale = nearest_sim >= STALE
    if stale and not allow_stale:
        raise ValueError(
            f"stale hypothesis: nearest prior {nearest_id} similarity {nearest_sim:.3f} >= {STALE}; "
            "use --allow-stale to run anyway"
        )

    h = mem.add_hypothesis(h)  # enriches: embedding, tags, novelty_score
    spec.hypothesis_id = h.id
    spec = mem.add_spec(spec)

    mem.set_status(h.id, HypothesisStatus.TESTING)
    sc, v = judge(spec, run_fn=run_fn, period=period, cfg=cfg, mem=mem, bars=bars, costs=costs)

    exp = anchor_experiment(mem, h.id, sc.experiment_id)
    if bars is None and run_fn is run:
        # thread the SAME frame the anchor ran on so trade entry_i/exit_i align and
        # C3 regime conditioning fires on real runs. load_bars is lru_cached on these
        # exact args -> the anchor already loaded it -> cache hit, no second read.
        from popper.data import load_bars
        from popper.experiments.runner import _tf_to_mt5
        start, end = period or (None, None)
        bars = load_bars(spec.context.universe[0], _tf_to_mt5(spec.context.timeframe), start, end)
    complete_scorecard(sc, mem, h, exp, bars=bars)  # novelty/correlation/regime/behaviour in place
    sc = mem.add_scorecard(sc)
    v = mem.add_verdict(v)

    mem.set_status(h.id, _STATUS[v.type])
    mem.set_behaviour(h.id, sc.behaviour_signature)

    m = exp.raw_metrics
    pf = m.get("profit_factor", 0.0)
    return {
        "hypothesis_id": h.id,
        "claim": h.claim,
        "stale": stale,
        "spec_id": spec.id,
        "experiment_id": exp.id,
        "scorecard_id": sc.id,
        "verdict_id": v.id,
        "verdict": v.type.value,
        "reasons": v.reasons,
        "status": _STATUS[v.type].value,
        "stats": {
            "pbo": sc.pbo,
            "deflated_sharpe": sc.deflated_sharpe,
            "robustness": sc.robustness,
            "param_stability": sc.parameter_stability,
            "regime_stability": sc.regime_stability,
            "novelty": sc.novelty,
            "correlation": sc.correlation,
            "n_trades": m.get("trades_n", 0),
            "profit_factor": pf,
            "expectancy": m.get("expectancy", 0.0),
            "expectancy_net": m.get("expectancy_net"),  # None unless a cost model ran
            "expected_edge": round(calculate_edge(min(pf, 5.0), m.get("trades_n", 0)), 2),
        },
    }


def anchor_experiment(mem, hypothesis_id: str, experiment_id: str) -> ExperimentResult:
    """The full-period experiment judge persisted, from the hypothesis trail."""
    for e in mem.record(hypothesis_id).get("experiments", []):
        if e["id"] == experiment_id:
            return ExperimentResult(**e)
    raise RuntimeError(f"anchor experiment {experiment_id!r} not found for {hypothesis_id}")
