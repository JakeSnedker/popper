"""Overlapping forward returns must not buy free significance.

The L1 gate computes an h-bar forward return at EVERY bar, so consecutive
observations share h-1 of their window. The plain t treats them as independent.
"""

import numpy as np

from popper.falsify.overlap import block_bootstrap_lower, effective_n, newey_west_t


def _plain_t(r):
    return float(r.mean() / (r.std(ddof=1) / np.sqrt(r.size)))


def _overlapping(n=4000, h=60, drift=0.0002, seed=1):
    """Independent bar returns, then the h-bar rolling sums the gate actually sees."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, 0.01, n + h)
    return np.convolve(steps, np.ones(h), mode="valid")[:n]


def test_overlap_inflates_the_plain_t_and_the_hac_t_corrects_it():
    r = _overlapping()
    plain, hac = _plain_t(r), newey_west_t(r, 59)
    assert plain > hac > 0, "HAC t must be smaller but keep the sign"
    assert plain / hac > 2.0, f"expected material deflation, got {plain / hac:.2f}x"


def test_independent_data_is_barely_touched():
    """With no overlap there is nothing to correct, so the two should agree."""
    rng = np.random.default_rng(2)
    r = rng.normal(0.001, 0.01, 4000)
    assert abs(newey_west_t(r, 0) - _plain_t(r)) < 0.1


def test_a_skewed_but_real_edge_clears_the_bootstrap():
    """30% hit rate, 5R winners: a positive mean that a t-test punishes."""
    rng = np.random.default_rng(3)
    wins = rng.random(6000) < 0.30
    r = np.where(wins, 5.0, -1.0) + rng.normal(0, 0.1, 6000)
    assert r.mean() > 0
    assert block_bootstrap_lower(r, block=1) > 0, "a real edge must clear the bound"


def test_pure_noise_does_not_clear_the_bootstrap():
    rng = np.random.default_rng(4)
    assert block_bootstrap_lower(rng.normal(0.0, 0.01, 4000), block=20) <= 0


def test_bootstrap_is_deterministic():
    r = _overlapping(n=1500, h=20)
    assert block_bootstrap_lower(r, 20) == block_bootstrap_lower(r, 20)


def test_effective_n_shrinks_with_overlap():
    assert effective_n(6000, 59) == 100
    assert effective_n(6000, 0) == 6000


def test_degenerate_inputs_are_safe():
    for fn in (lambda r: newey_west_t(r, 5), lambda r: block_bootstrap_lower(r, 5)):
        assert fn(np.array([])) == 0.0
        assert fn(np.array([1.0])) == 0.0
    assert newey_west_t(np.zeros(500), 10) == 0.0     # no variance at all
