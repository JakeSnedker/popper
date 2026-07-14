"""Tests for popper.portfolio.correlation (Phase D1)."""

import numpy as np

from popper.portfolio import correlation_matrix, diversification_score, return_stream

N = 100


def _trades(pnls: dict[int, float]) -> list[dict]:
    return [{"exit_i": i, "pnl_r": r, "entry_i": max(i - 2, 0), "dir": "long", "risk": 10.0}
            for i, r in pnls.items()]


def test_return_stream_keys_r_to_exit_bar():
    s = return_stream(_trades({5: 2.0, 20: -1.0}), N)
    assert s[5] == 2.0 and s[20] == -1.0
    assert s.sum() == 1.0  # zeros everywhere else


def test_identical_streams_corr_one_div_zero():
    a = return_stream(_trades({3: 1.5, 10: -1.0, 40: 2.0}), N)
    b = a.copy()
    m = correlation_matrix([a, b])
    assert abs(m[0, 1] - 1.0) < 1e-9
    assert diversification_score([a], b) < 1e-9  # adds nothing


def test_anticorrelated_streams_corr_minus_one_high_div():
    a = return_stream(_trades({3: 1.5, 10: -1.0, 40: 2.0}), N)
    b = -a
    m = correlation_matrix([a, b])
    assert abs(m[0, 1] - (-1.0)) < 1e-9
    assert diversification_score([a], b) > 0.9  # hedge = top diversifier


def test_uncorrelated_candidate_ranks_above_correlated():
    rng = np.random.default_rng(42)
    book = [rng.normal(size=N)]
    correlated = book[0] + 0.1 * rng.normal(size=N)  # near-clone of the book
    uncorrelated = rng.normal(size=N)  # independent
    s_corr = diversification_score(book, correlated)
    s_uncorr = diversification_score(book, uncorrelated)
    assert s_uncorr > s_corr


def test_flat_stream_is_neutral_no_nan():
    flat = np.zeros(N)
    live = return_stream(_trades({7: 1.0, 30: -0.5}), N)
    m = correlation_matrix([flat, live])
    assert not np.isnan(m).any()
    assert m[0, 1] == 0.0
    assert diversification_score([live], flat) == 0.5  # neutral
    assert diversification_score([], live) == 0.5  # empty book
