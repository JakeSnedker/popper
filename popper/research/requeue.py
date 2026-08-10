"""Re-test hypotheses stranded between synthesis and verdict.

`evaluate` marks a hypothesis TESTING before running the gauntlet. A clean
failure now releases that claim (loop.falsify_stored), but a hard kill — power
loss, SIGKILL, a node yanked mid-hunt — cannot. Those rows keep a TESTING status
no run will ever revisit: the Director only proposes NEW hypotheses, and the
exploit backlog scan only looks at killed/survived. Left alone they accumulate
silently and read as "48 untested" on the memory map forever.

This sweeps them back through the gauntlet using the spec already in memory.
"""

from __future__ import annotations

from popper.artifacts import Hypothesis, HypothesisStatus, StrategySpec
from popper.falsify import GauntletConfig
from popper.memory import get_memory
from popper.research.loop import falsify_stored

STRANDED = (HypothesisStatus.TESTING, HypothesisStatus.PROPOSED)


def find_stranded(mem=None) -> tuple[list[tuple[Hypothesis, StrategySpec]], list[Hypothesis]]:
    """Split stranded hypotheses into (re-runnable with their stored spec, no spec).

    A hypothesis with no spec never got through synthesis; its claim survives but
    there is nothing to backtest, so it needs re-synthesis, not a re-run.
    """
    mem = mem or get_memory()
    stranded = [h for status in STRANDED for h in mem.list_hypotheses(status)]
    if not stranded:
        return [], []

    records = mem.analysis_records([h.id for h in stranded], with_metrics=False)
    runnable, orphaned = [], []
    for hypothesis in stranded:
        record = records.get(hypothesis.id) or {}
        # A stranded hypothesis has no verdict by definition; if one exists the
        # status is simply stale, so re-running it would duplicate a real test.
        if record.get("verdicts"):
            continue
        specs = sorted(record.get("specs") or [], key=lambda row: row["created_at"])
        if not specs:
            orphaned.append(hypothesis)
            continue
        # The stored analysis row is trimmed to id/hypothesis_id/created_at, so
        # the full spec has to come back off the record trail.
        full = next(
            (row for row in mem.record(hypothesis.id).get("specs", [])
             if row["id"] == specs[-1]["id"]), None,
        )
        if full is None:
            orphaned.append(hypothesis)
        else:
            runnable.append((hypothesis, StrategySpec(**full)))
    return runnable, orphaned


def requeue(mem=None, cfg: GauntletConfig | None = None, limit: int | None = None,
            period: tuple[str, str] | None = None, dry_run: bool = False) -> dict:
    """Re-run every stranded hypothesis that still has a spec. Returns a summary."""
    mem = mem or get_memory()
    runnable, orphaned = find_stranded(mem)
    if limit is not None:
        runnable = runnable[:limit]

    summary = {
        "runnable": len(runnable),
        "orphaned": len(orphaned),
        "orphaned_ids": [h.id for h in orphaned],
        "verdicts": {},
        "failed": [],
        "dry_run": dry_run,
    }
    if dry_run:
        summary["runnable_ids"] = [h.id for h, _ in runnable]
        return summary

    for hypothesis, spec in runnable:
        try:
            _, verdict, _ = falsify_stored(
                hypothesis, spec, mem, period=period, cfg=cfg or GauntletConfig(),
            )
        except Exception as exc:  # noqa: BLE001 - one bad spec must not stop the sweep
            summary["failed"].append({"id": hypothesis.id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        key = verdict.type.value
        summary["verdicts"][key] = summary["verdicts"].get(key, 0) + 1
    return summary


def demo() -> None:
    """Self-check with a fake memory: routing, verdict tally, and failure isolation."""
    from popper.artifacts.enums import VerdictType

    class _Verdict:
        def __init__(self, value):
            self.type = VerdictType(value)

    spec_row = {"id": "S-1", "hypothesis_id": "H-1", "created_at": "2026-01-01T00:00:00+00:00"}

    class _Mem:
        def __init__(self):
            self.hyps = {
                "H-1": Hypothesis(id="H-1", claim="a", mechanism="m",
                                  status=HypothesisStatus.TESTING),
                "H-2": Hypothesis(id="H-2", claim="b", mechanism="m",
                                  status=HypothesisStatus.PROPOSED),
                "H-3": Hypothesis(id="H-3", claim="c", mechanism="m",
                                  status=HypothesisStatus.TESTING),
            }

        def list_hypotheses(self, status=None):
            return [h for h in self.hyps.values() if status is None or h.status == status]

        def analysis_records(self, ids, with_metrics=True):
            return {
                "H-1": {"specs": [spec_row], "verdicts": []},   # re-runnable
                "H-2": {"specs": [], "verdicts": []},           # never synthesised
                "H-3": {"specs": [spec_row], "verdicts": [{"id": "V-1"}]},  # stale status
            }

        def record(self, hypothesis_id):
            full = dict(spec_row, name="s", alpha={"signal": "close > open"})
            return {"specs": [full]}

    mem = _Mem()
    runnable, orphaned = find_stranded(mem)
    assert [h.id for h, _ in runnable] == ["H-1"], runnable
    assert [h.id for h in orphaned] == ["H-2"], orphaned  # H-3 skipped: already has a verdict

    plan = requeue(mem, dry_run=True)
    assert plan["runnable"] == 1 and plan["orphaned"] == 1, plan

    calls = []

    def _ok(h, spec, m, **kw):
        calls.append(h.id)
        return None, _Verdict("kill"), None

    def _boom(h, spec, m, **kw):
        raise RuntimeError("runner exploded")

    # Patch this module's own globals: under `python -m`, an `import` of this
    # module would bind a SECOND copy and the patch would miss.
    here = globals()
    original = here["falsify_stored"]
    try:
        here["falsify_stored"] = _ok
        assert requeue(mem)["verdicts"] == {"kill": 1}, "verdict tally"
        assert calls == ["H-1"]
        here["falsify_stored"] = _boom
        result = requeue(mem)
        assert result["verdicts"] == {} and len(result["failed"]) == 1, result
    finally:
        here["falsify_stored"] = original
    print("requeue demo OK")


if __name__ == "__main__":
    demo()
