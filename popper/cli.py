"""Popper CLI.

    popper run --hypothesis x.yaml [--start YYYY-MM-DD --end YYYY-MM-DD]
                                  # the full loop: store, backtest, falsify, judge
    popper research --iterations N # the unattended Director loop (autonomy demo)
    popper seed                   # populate alpha memory with example hypotheses
    popper stats                  # print current counts
    popper requeue [--dry-run] [--limit N]
                                  # re-test hypotheses stranded mid-run (status
                                  # TESTING/PROPOSED with no verdict)
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
    p.add_argument("--signal-edge-mode", choices=("loose", "balanced", "strict"), default="balanced",
                   help="Layer-1 signal-edge strictness (default: balanced)")
    p.add_argument("--allow-stale", action="store_true",
                   help="run even if alpha memory finds a near-duplicate")
    p.add_argument("--costs", action="store_true",
                   help="apply realistic friction (spread from data + slippage + commission) "
                        "instead of frictionless fills — re-test edges that passed clean")
    p.add_argument("--point-value", type=float, default=0.01,
                   help="price per MT5 spread-point, the cost calibration knob (default 0.01)")
    p.add_argument("--slippage-pts", type=float, default=0.0, help="per-side slippage in points")
    p.add_argument("--commission-pts", type=float, default=0.0, help="round-trip commission in points")
    a = p.parse_args(argv)
    period = (a.start, a.end) if a.start and a.end else None
    costs = ({"point_value": a.point_value, "slippage_pts": a.slippage_pts,
              "commission_pts": a.commission_pts} if a.costs else None)

    from popper.dsl import DSLValidationError
    from popper.falsify import GauntletConfig
    from popper.research import run_loop

    try:
        s = run_loop(a.hypothesis, period=period, cfg=GauntletConfig(signal_edge_mode=a.signal_edge_mode),
                     allow_stale=a.allow_stale, costs=costs)
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
    p.add_argument("--no-exit-menu", action="store_true", help="skip the v0.3 exit-menu search")
    p.add_argument("--signal-edge-mode", choices=("loose", "balanced", "strict"), default="balanced",
                   help="Layer-1 signal-edge strictness (default: balanced)")
    p.add_argument("--mode", choices=("explore", "balanced", "exploit", "auto"),
                   default="balanced", help="research allocation (default: balanced)")
    a = p.parse_args(argv)

    from popper.falsify import GauntletConfig
    from popper.orchestrator import research_budget, run_research
    # v0.3 §5: autonomous discovery runs the staged exit menu by default (agents
    # propose alphas; the gauntlet searches each one's exit). --no-exit-menu opts out.
    cfg = GauntletConfig(exit_menu=not a.no_exit_menu, signal_edge_mode=a.signal_edge_mode)
    mode = "explore" if a.mode == "auto" else a.mode
    _print_run(run_research(
        iterations=a.iterations, cfg=cfg, budget=research_budget(mode),
    ))


def _print_run(s) -> None:
    bar = "=" * 64
    print(bar)
    print(f"POPPER RESEARCH RUN — {s.iterations} iterations")
    print(f"  budget spent (engine: iters): {s.budget_spent}")
    if s.stopped_reason:
        print(f"  stopped: {s.stopped_reason}")
    print(bar)
    print(f"proposed {s.proposed}  |  killed {s.killed}  |  survived {s.survived}  |  worker refinements {s.worker_runs}  |  skipped {s.skipped}")
    if s.failures:
        print("non-fatal failures:")
        for reason in s.failures[:8]:
            print(f"  ! {reason}")
        if len(s.failures) > 8:
            print(f"  ... {len(s.failures) - 8} more")
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


def _requeue(argv: list[str]) -> None:
    import argparse

    p = argparse.ArgumentParser(
        prog="popper requeue",
        description="Re-test hypotheses stranded between synthesis and verdict.",
    )
    p.add_argument("--dry-run", action="store_true", help="list what would run, test nothing")
    p.add_argument("--limit", type=int, help="re-test at most N (default: all)")
    p.add_argument("--start", help="period start YYYY-MM-DD")
    p.add_argument("--end", help="period end YYYY-MM-DD")
    a = p.parse_args(argv)

    from popper.research.requeue import requeue

    s = requeue(
        limit=a.limit, dry_run=a.dry_run,
        period=(a.start, a.end) if a.start and a.end else None,
    )
    print(f"stranded, re-runnable : {s['runnable']}")
    print(f"stranded, never synthesised: {s['orphaned']}  {', '.join(s['orphaned_ids'][:5])}"
          + (" ..." if len(s["orphaned_ids"]) > 5 else ""))
    if a.dry_run:
        print("\n--dry-run: nothing was tested.")
        return
    for verdict, count in sorted(s["verdicts"].items()):
        print(f"  {verdict:8s} {count}")
    if s["failed"]:
        print(f"\n{len(s['failed'])} failed to re-run:")
        for row in s["failed"][:10]:
            print(f"  {row['id']}: {row['error']}")


def main() -> None:
    # ponytail: Windows stdout defaults to cp1252 and mangles the summary's — / arrows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "run":
        try:
            _run(sys.argv[2:])
        except FileNotFoundError as e:
            sys.exit(str(e))
    elif cmd == "research":
        _research(sys.argv[2:])
    elif cmd == "seed":
        from popper.seed import seed
        try:
            seed("--force" in sys.argv[2:])
        except RuntimeError as e:
            sys.exit(str(e))
    elif cmd == "stats":
        from popper.memory import get_memory
        import json
        print(json.dumps(get_memory().stats(), indent=2))
    elif cmd == "requeue":
        _requeue(sys.argv[2:])
    elif cmd == "serve":
        import uvicorn
        uvicorn.run("popper.api.main:app", host=settings.api_host, port=settings.api_port, reload=True)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
