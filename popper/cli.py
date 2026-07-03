"""Popper CLI.

    popper run --hypothesis x.yaml [--start YYYY-MM-DD --end YYYY-MM-DD]
                                  # the full loop: store, backtest, falsify, judge
    popper research --iterations N # the unattended Director loop (autonomy demo)
    popper seed                   # populate alpha memory with example hypotheses
    popper stats                  # print current counts
    popper serve                  # run the API for the dashboard
"""

import sys

from popper.config import settings


def _run(argv: list[str]) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="popper run", description="Run one hypothesis through the full loop.")
    p.add_argument("--hypothesis", required=True, help="combined hypothesis+spec YAML (examples/hypotheses/)")
    p.add_argument("--start", help="period start YYYY-MM-DD (default: all available data)")
    p.add_argument("--end", help="period end YYYY-MM-DD")
    a = p.parse_args(argv)
    period = (a.start, a.end) if a.start and a.end else None

    from popper.dsl import DSLValidationError
    from popper.research import run_loop

    try:
        s = run_loop(a.hypothesis, period=period)
    except (DSLValidationError, ValueError) as e:
        sys.exit(f"hypothesis file rejected: {e}")
    _print_summary(s)


def _print_summary(s: dict) -> None:
    bar = "=" * 64
    print(bar)
    print(f"HYPOTHESIS {s['hypothesis_id']}")
    print(f'  "{s["claim"]}"')
    if s["stale"]:
        print("  ! STALE — near-duplicate of a prior idea (ran anyway, human's call)")
    print(bar)
    print(f"VERDICT: {s['verdict'].upper()}   (hypothesis -> {s['status']})")
    for r in s["reasons"]:
        print(f"  x {r}")
    print("-" * 64)
    st = s["stats"]
    rows = [
        ("trades", st["n_trades"]), ("profit_factor", st["profit_factor"]),
        ("expected_edge", st["expected_edge"]), ("pbo", st["pbo"]),
        ("deflated_sharpe", st["deflated_sharpe"]), ("robustness", st["robustness"]),
        ("param_stability", st["param_stability"]), ("regime_stability", st["regime_stability"]),
        ("novelty", st["novelty"]), ("correlation", st["correlation"]),
    ]
    for name, val in rows:
        print(f"  {name:<18} {val}")
    print("-" * 64)
    print("stored to alpha memory: "
          f"{s['hypothesis_id']} -> {s['spec_id']} -> {s['experiment_id']} "
          f"-> {s['scorecard_id']} -> {s['verdict_id']}")
    print(bar)


def _research(argv: list[str]) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="popper research", description="Unattended Director research loop.")
    p.add_argument("--iterations", type=int, default=10, help="loop iterations (default 10)")
    a = p.parse_args(argv)

    from popper.orchestrator import run_research
    _print_run(run_research(iterations=a.iterations))


def _print_run(s) -> None:
    bar = "=" * 64
    print(bar)
    print(f"POPPER RESEARCH RUN — {s.iterations} iterations")
    print(f"  budget spent (engine: iters): {s.budget_spent}")
    print(bar)
    print(f"proposed {s.proposed}  |  killed {s.killed}  |  survived {s.survived}  |  worker refinements {s.worker_runs}")
    print("-" * 64)
    print("categories explored:")
    for et, c in s.category_report().items():
        flag = " DEAD (pruned)" if c["dead"] else ""
        print(f"  {et:<16} survivors={c['survivors']} kills={c['kills']}{flag}")
    print("-" * 64)
    if s.survivors:
        print(f"SURVIVORS ({len(s.survivors)}):")
        for sv in s.survivors:
            st = sv["stats"]
            print(f"  [{sv['verdict'].upper()}] {sv['hypothesis_id']}  \"{sv['claim'][:70]}\"")
            print(f"     trades={st['n_trades']} PF={st['profit_factor']} pbo={st['pbo']} "
                  f"dsr={st['deflated_sharpe']} novelty={st['novelty']}")
    else:
        print("SURVIVORS (0): the gauntlet killed every candidate this run.")
    print(bar)


def main() -> None:
    # ponytail: Windows stdout defaults to cp1252 and mangles the summary's — / arrows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "run":
        _run(sys.argv[2:])
    elif cmd == "research":
        _research(sys.argv[2:])
    elif cmd == "seed":
        from popper.seed import seed
        seed()
    elif cmd == "stats":
        from popper.memory import get_memory
        import json
        print(json.dumps(get_memory().stats(), indent=2))
    elif cmd == "serve":
        import uvicorn
        uvicorn.run("popper.api.main:app", host=settings.api_host, port=settings.api_port, reload=True)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
