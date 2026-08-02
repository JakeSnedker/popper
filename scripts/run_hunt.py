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
from popper.orchestrator import research_budget, run_research
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


def _auto_exploit(mem, deadline: float, resume_at: float) -> bool:
    """Use local compute during provider backoff. False means no work remains."""
    batch = 0
    limit = max(1, settings.auto_exploit_batch_size)
    while time.time() < deadline and time.monotonic() < resume_at:
        batch += 1
        run_id = f"X-{uuid.uuid4().hex[:8]}"
        summary = run_research(
            mem=mem,
            llm=None,
            iterations=1,
            run_id=run_id,
            budget=research_budget("exploit"),
            backlog_limit=limit,
            exploit_max_evals=max(0, settings.auto_exploit_max_evals),
        )
        print(
            f"[auto exploit {batch} {run_id}] worker_runs={summary.worker_runs} "
            f"skipped={summary.skipped} stopped={summary.stopped_reason or '-'}",
            flush=True,
        )
        if summary.stopped_reason == "exploit backlog exhausted":
            return False
        if summary.worker_runs == 0 and summary.failures:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=60, help="wall-clock budget")
    parser.add_argument("--iterations", type=int, default=12, help="candidate iterations per batch")
    parser.add_argument("--seeded", action="store_true", help="scripted no-LLM smoke")
    parser.add_argument(
        "--mode", choices=("explore", "balanced", "exploit", "auto"),
        default=settings.research_mode,
        help="explore, balanced, exploit-only, or explore with exploit during LLM backoff",
    )
    parser.add_argument(
        "--exploit-max-evals", type=int, default=settings.exploit_max_evals,
        help="optimizer candidate evaluations per explicit exploit target",
    )
    args = parser.parse_args()

    active_mode = "explore" if args.mode == "auto" else args.mode
    budget = research_budget(active_mode)
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

    # Exploit is fully local and must not even initialize a subscription router.
    llm = None if active_mode == "exploit" else get_llm()
    started = time.time()
    deadline = started + args.minutes * 60
    batch = 0
    while time.time() < deadline:
        batch += 1
        run_id = f"R-{uuid.uuid4().hex[:8]}"
        summary = run_research(
            mem=mem, llm=llm, iterations=args.iterations, run_id=run_id, budget=budget,
            exploit_max_evals=max(0, args.exploit_max_evals),
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
            retry = summary.retry_after
            resume_at = time.monotonic() + retry
            print(f"PROVIDER_PAUSED retry_after={retry} reason={summary.stopped_reason}")
            if args.mode == "auto":
                print("AUTO_EXPLOIT local rescue started; no LLM calls", flush=True)
                _auto_exploit(mem, deadline, resume_at)
                remaining = max(0, int(resume_at - time.monotonic()))
                if remaining == 0 and time.time() < deadline:
                    continue
                print(f"PROVIDER_PAUSED retry_after={remaining} reason=creative routes cooling down")
            _book_summary(mem)
            return 75
        if summary.stopped_reason == "exploit backlog exhausted":
            print("EXPLOIT_BACKLOG_EXHAUSTED")
            break
        if active_mode == "exploit" and summary.worker_runs == 0:
            print(f"EXPLOIT_FAILED failures={summary.failures}")
            _book_summary(mem)
            return 1

    _book_summary(mem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
