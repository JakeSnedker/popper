"""Research loop: hypothesis file -> stored, scored, judged (Phase 5)."""

from popper.research.loop import (
    anchor_experiment,
    evaluate,
    falsify_stored,
    load_hypothesis_file,
    run_loop,
)

__all__ = ["run_loop", "load_hypothesis_file", "evaluate", "anchor_experiment", "falsify_stored"]
