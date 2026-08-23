"""Forge coverage is a ratchet: it may rise, never fall silently.

The DSL was meant to be one language that translates to every target. It drifted
because nothing failed when a feature was added and the emitters did not
implement it. These baselines are deliberately awkward to lower -- if you are
here because a number went down, that is the test doing its job.

Raise a baseline in the SAME commit that raises the coverage.
"""

from popper.dsl.features import FEATURES
from popper.dsl.features.levels import LEVEL_FEATURES
from popper.forge.coverage import TARGETS, coverage, coverage_for, report

# measured 2026-08-23, at DSL v0.4 (level-feature vocabulary landed in both targets)
BASELINE = {
    "pine": {"features": 19, "levels": 13, "capabilities": 10},
    "mql5": {"features": 19, "levels": 13, "capabilities": 11},
}


def test_coverage_never_regresses():
    for target, floor in BASELINE.items():
        cov = coverage_for(target)
        assert len(cov.features_supported) >= floor["features"], (
            f"{target} feature coverage fell from {floor['features']} to "
            f"{len(cov.features_supported)}"
        )
        assert len(cov.levels_supported) >= floor["levels"]
        supported_caps = sum(1 for v in cov.capabilities.values() if v)
        assert supported_caps >= floor["capabilities"]


def test_every_dsl_feature_appears_in_the_register():
    """A new feature cannot be invisible: it lands in supported or missing."""
    for target in TARGETS:
        cov = coverage_for(target)
        assert set(cov.features_supported) | set(cov.features_missing) == set(FEATURES)
        assert set(cov.levels_supported) | set(cov.levels_missing) == set(LEVEL_FEATURES)


def test_the_register_renders():
    text = report()
    for target in TARGETS:
        assert target.upper() in text
    assert "concepts" in text


def test_a_target_claiming_full_coverage_would_have_to_prove_it():
    """Guards against a capability dict quietly marked all-True."""
    for target in TARGETS:
        cov = coverage_for(target)
        if cov.ratio == 1.0:
            # legitimate only when the emitter really implements everything
            assert not cov.features_missing and not cov.levels_missing
