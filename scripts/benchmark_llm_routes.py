"""Canary benchmark for real LLM routes without writing to alpha memory.

Examples:
  python scripts/benchmark_llm_routes.py --route claude=claude_cli:sonnet \
      --route openai=codex_cli:default --task hypothesis
  python scripts/benchmark_llm_routes.py --route cheap=claude_cli:haiku --task synthesis

The report measures valid outputs, uniqueness, DSL/fidelity pass rate, tokens, and
latency. It intentionally never backtests or persists benchmark generations.
"""

import argparse
import json
import tempfile
from pathlib import Path

from popper.agents import LLMRouter, propose_batch, synthesize, view
from popper.agents.llm import parse_route
from popper.artifacts import Hypothesis
from popper.dsl.features import resolve_feature
from popper.memory import LocalEmbedder
from popper.memory.sqlite_repo import SqliteMemory

_SYNTHESIS_CASES = [
    Hypothesis(claim="US30 NY opening expansion continues with rising volatility",
               mechanism="opening auction imbalance attracts momentum flow",
               required_features=["session_ny", "opening_range", "atr_14"]),
    Hypothesis(claim="US30 liquidity sweeps reverse during low participation",
               mechanism="stop exhaustion is not followed by committed flow",
               required_features=["liquidity_sweep", "rel_volume"]),
    Hypothesis(claim="US30 trend pullbacks resume after EMA alignment",
               mechanism="delayed participants enter an established directional auction",
               required_features=["ema_20", "ema_50", "trend_regime"]),
]


def _router(route: str) -> LLMRouter:
    targets = parse_route(route)
    return LLMRouter({
        "hypothesis": targets, "hypothesis_strong": targets, "wildcard": targets,
        "synthesis": targets, "synthesis_strong": targets, "audit": targets,
    })


def _hypothesis_case(router, samples: int, batch_size: int) -> dict:
    memory = SqliteMemory(str(Path(tempfile.mkdtemp()) / "benchmark.db"), embedder=LocalEmbedder())
    generated = []
    errors = []
    for _ in range(samples):
        try:
            generated.extend(propose_batch(view("hypothesis", memory), router, count=batch_size))
        except Exception as exc:  # report route reliability; do not hide failures
            errors.append(f"{type(exc).__name__}: {exc}")
    normalized = {" ".join(h.claim.lower().split()) for h in generated}
    feature_total = sum(len(h.required_features) for h in generated)
    supported = sum(resolve_feature(f) is not None for h in generated for f in h.required_features)
    return {
        "requested": samples * batch_size,
        "generated": len(generated),
        "unique_claims": len(normalized),
        "supported_feature_fraction": round(supported / feature_total, 4) if feature_total else 1.0,
        "errors": errors,
    }


def _synthesis_case(router, samples: int) -> dict:
    passed, errors = 0, []
    cases = (_SYNTHESIS_CASES * samples)[:samples]
    for hypothesis in cases:
        try:
            synthesize(hypothesis, router, enforce_local_context=False)
            passed += 1
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    return {"requested": len(cases), "dsl_fidelity_passed": passed, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", action="append", required=True, metavar="NAME=PROVIDER:MODEL")
    parser.add_argument("--task", choices=("hypothesis", "synthesis"), default="hypothesis")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--output", help="optional JSON report path")
    args = parser.parse_args()

    report = {"task": args.task, "routes": {}}
    for item in args.route:
        if "=" not in item:
            parser.error("--route must be NAME=PROVIDER:MODEL")
        name, route = item.split("=", 1)
        router = _router(route)
        result = (_hypothesis_case(router, args.samples, args.batch_size)
                  if args.task == "hypothesis" else _synthesis_case(router, args.samples))
        result["usage"] = router.stats()
        result["latency_ms"] = sum(record["latency_ms"] for record in router.records)
        report["routes"][name] = result

    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
