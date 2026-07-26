"""Time-boxed Popper hunt runner with provider-aware circuit breaking.

Subscription mode uses the official Claude and Codex CLIs. Provider quota
failures return exit 75 immediately, allowing the outer scheduler to sleep rather
than spending the rest of the window launching doomed repairs.
"""

import argparse
import sys
import time
import uuid

from popper.agents import get_llm
from popper.config import settings
from popper.memory import get_memory
from popper.orchestrator import run_research
from popper.portfolio import book_from_mem


def _seeded_llm():
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    from us30_seeded_hunt import ScriptedLLM, _load
    return ScriptedLLM(*_load())


def _book_summary(mem) -> None:
    book = book_from_mem(mem)
    edges = book.get("edges", []) if isinstance(book, dict) else []
    rejected = len(book.get("rejected", [])) if isinstance(book, dict) else "?"
    print(f"\nbook: {len(edges)} member(s); rejected={rejected}")
    print(f"final counts: {mem.stats()['counts']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=60, help="wall-clock budget")
    parser.add_argument("--iterations", type=int, default=12, help="candidate iterations per batch")
    parser.add_argument("--seeded", action="store_true", help="scripted no-LLM smoke")
    parser.add_argument("--explore-heavy", action="store_true",
                        help="explore-heavy: 90/10 explore/wildcard, no exploit")
    args = parser.parse_args()

    budget = {"explore": 0.90, "wildcard": 0.10, "exploit": 0.0} if args.explore_heavy else None
    mem = get_memory()
    target = settings.supabase_url if settings.store == "supabase" else "local sqlite"
    print(f"store={settings.store} target={target} llm_backend={settings.llm_backend}")

    if args.seeded:
        run_id = f"seed-{uuid.uuid4().hex[:6]}"
        summary = run_research(mem=mem, llm=_seeded_llm(), iterations=16, run_id=run_id)
        print(f"[seeded {run_id}] proposed={summary.proposed} survived={summary.survived} "
              f"killed={summary.killed} worker_runs={summary.worker_runs} "
              f"spent={summary.budget_spent}")
        _book_summary(mem)
        return 0

    llm = get_llm()  # one router/circuit survives every batch in this process
    started = time.time()
    deadline = started + args.minutes * 60
    batch = 0
    while time.time() < deadline:
        batch += 1
        run_id = f"R-{uuid.uuid4().hex[:8]}"
        summary = run_research(
            mem=mem, llm=llm, iterations=args.iterations, run_id=run_id, budget=budget,
        )
        counts = mem.stats()["counts"]
        usage = summary.llm_usage
        print(
            f"[batch {batch} {run_id}] +proposed={summary.proposed} survived={summary.survived} "
            f"killed={summary.killed} worker_runs={summary.worker_runs} "
            f"llm_calls={usage.get('calls', 0)} tokens="
            f"{usage.get('input_tokens', 0)}/{usage.get('output_tokens', 0)} "
            f"providers={usage.get('providers', {})} totals={counts} "
            f"| {(time.time() - started) / 60:.1f}/{args.minutes:.0f}m",
            flush=True,
        )
        if summary.provider_paused:
            print(f"PROVIDER_PAUSED retry_after={summary.retry_after} reason={summary.stopped_reason}")
            _book_summary(mem)
            return 75

    _book_summary(mem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
