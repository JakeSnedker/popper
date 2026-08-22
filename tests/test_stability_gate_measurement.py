"""Gate 2 must not report a robustness verdict it never measured.

A single-point parameter grid has no neighbourhood to vary, so profitable_frac
is 1.0 or 0.0 and always clears the 50% bar. The gate used to pass quietly while
reporting parameter_stability 0.0 -- which read as "maximally unstable" to
anything ranking on that field, and was indistinguishable from a genuine 0.0.
"""

from statistics import mean, pstdev


def gate2(grid_pfs, min_frac=0.50, max_cv=0.30):
    """The arithmetic of gauntlet.py gate 2, isolated for assertion."""
    measured = len(grid_pfs) > 1
    profitable_frac = mean(1.0 if p > 1.0 else 0.0 for p in grid_pfs)
    pf_cv = (pstdev(grid_pfs) / mean(grid_pfs)) if measured and mean(grid_pfs) > 0 else None
    stability = (
        max(0.0, min(profitable_frac, 1.0 - min(pf_cv, 1.0))) if pf_cv is not None else None
    )
    reasons = []
    if not measured:
        reasons.append("not measured")
    elif profitable_frac < min_frac:
        reasons.append("unprofitable grid")
    if pf_cv is not None and pf_cv > max_cv:
        reasons.append("knife-edge")
    return stability, reasons


def test_a_single_point_grid_reports_not_measured_and_does_not_pass():
    stability, reasons = gate2([1.80])
    assert stability is None, "an unmeasured neighbourhood must not report a number"
    assert reasons == ["not measured"]


def test_a_real_grid_still_measures_and_passes():
    stability, reasons = gate2([1.80, 1.75, 1.85])
    assert reasons == []
    assert stability > 0.9


def test_a_genuine_knife_edge_is_still_killed():
    stability, reasons = gate2([1.80, 0.60, 0.55])
    assert "knife-edge" in reasons
    assert stability is not None and stability < 0.5


def test_zero_now_means_only_one_thing():
    """A measured 0.0 and an unmeasured grid are no longer the same value."""
    unmeasured, _ = gate2([1.80])
    measured, _ = gate2([0.5, 0.4, 0.6])       # every point unprofitable
    assert unmeasured is None
    assert measured == 0.0


def test_scorecard_accepts_the_unmeasured_case():
    from popper.artifacts.scorecard import Scorecard
    sc = Scorecard(experiment_id="E-1", parameter_stability=None)
    assert sc.parameter_stability is None
