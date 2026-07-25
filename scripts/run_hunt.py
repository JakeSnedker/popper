"""Time-boxed research runner — tonight's "watch it fill" run AND the container's
cron workhorse. Loops run_research until --minutes elapses, writing every artifact
to whatever POPPER_STORE points at (Supabase = the shared map). Prints one line per
batch so you can watch the counts climb in the dashboard.

    # LLM discovery run on the shared DB (needs ANTHROPIC_API_KEY), ~1 hour:
    .venv/Scripts/python.exe scripts/run_hunt.py --minutes 60

    # $0 deterministic smoke (seeded example specs, single batch, no API, no cost):
    .venv/Scripts/python.exe scripts/run_hunt.py --seeded

Cost note: LLM mode spends Anthropic API per EXPLORE iteration (sonnet + haiku +
opus). Exploit iterations are non-LLM/$0. Seeded mode is fully $0 but its alpha
space is finite (explore never mutates an alpha) — good for a smoke, not for weeks
of novelty. See docs for the cost/throughput trade.
"""
import argparse
import time
import uuid

from popper.config import settings
from popper.memory import get_memory
from popper.orchestrator import run_research
from popper.portfolio import book_from_mem


def _seeded_llm():
    """Reuse the finished seeded-hunt scaffolding for a $0 smoke."""
    import os
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from us30_seeded_hunt import ScriptedLLM, _load
    return ScriptedLLM(*_load())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=60, help="wall-clock budget (LLM mode)")
    ap.add_argument("--iterations", type=int, default=12, help="iterations per batch")
    ap.add_argument("--seeded", action="store_true", help="$0 scripted run, single batch, no API")
    ap.add_argument("--explore-heavy", action="store_true",
                    help="favor NEW alphas over refinement (fills the map with distinct points faster)")
    args = ap.parse_args()

    # explore-max: get raw data ON THE BOARD first (distinct alphas across the map)
    # before exploiting anything — no point refining crap data. ~90/10, exploit~0.
    budget = {"explore": 0.90, "wildcard": 0.10, "exploit": 0.0} if args.explore_heavy else None

    mem = get_memory()
    target = settings.supabase_url if settings.store == "supabase" else "local sqlite"
    print(f"store={settings.store}  target={target}")

    if args.seeded:
        rid = f"seed-{uuid.uuid4().hex[:6]}"
        s = run_research(mem=mem, llm=_seeded_llm(), iterations=16, run_id=rid)
        print(f"[seeded {rid}] proposed={s.proposed} survived={s.survived} "
              f"killed={s.killed} worker_runs={s.worker_runs} spent={s.budget_spent}")
    else:
        start = time.time()
        deadline = start + args.minutes * 60
        batch = 0
        while time.time() < deadline:
            batch += 1
            rid = f"R-{uuid.uuid4().hex[:8]}"
            s = run_research(mem=mem, iterations=args.iterations, run_id=rid, budget=budget)  # real LLM
            c = mem.stats()["counts"]
            print(f"[batch {batch} {rid}] +proposed={s.proposed} survived={s.survived} "
                  f"killed={s.killed} worker_runs={s.worker_runs} | totals={c} "
                  f"| {(time.time() - start) / 60:.1f}/{args.minutes:.0f}m", flush=True)

    book = book_from_mem(mem)
    edges = book.get("edges", []) if isinstance(book, dict) else []
    print(f"\nbook: {len(edges)} member(s); rejected={len(book.get('rejected', [])) if isinstance(book, dict) else '?'}")
    print(f"final counts: {mem.stats()['counts']}")


if __name__ == "__main__":
    main()
