"""Correlation between edges' return streams.

Turns each edge's trade list (compiler trade dicts: dir/entry_i/exit_i/pnl_r/risk)
into a per-bar R series on a common integer bar index, so any set of edges run
on the same dataset becomes a set of alignable vectors. From there: pairwise
Pearson correlation, and a diversification score for building a book that is
the best PORTFOLIO, not the best single alpha.
"""

import numpy as np


def return_stream(trades: list[dict], n_bars: int) -> np.ndarray:
    """Per-bar PnL_R series on [0..n_bars): each trade's pnl_r keyed to its exit_i.

    Bars with no trade exit are 0. Multiple exits on one bar accumulate.
    """
    stream = np.zeros(n_bars)
    for t in trades:
        i = t["exit_i"]
        if 0 <= i < n_bars:
            stream[i] += t["pnl_r"]
    return stream


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson r; 0.0 for degenerate (flat/all-zero) streams instead of NaN."""
    sa, sb = np.std(a), np.std(b)
    if sa == 0.0 or sb == 0.0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def correlation_matrix(streams: list[np.ndarray]) -> np.ndarray:
    """Pairwise Pearson correlation across aligned return streams.

    Non-overlapping trade times are handled naturally by the zero-filled
    common index. Flat streams get 0 correlation to everything (diagonal 1).
    """
    n = len(streams)
    m = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            m[i, j] = m[j, i] = _pearson(streams[i], streams[j])
    return m


def diversification_score(book_streams: list[np.ndarray], candidate_stream: np.ndarray) -> float:
    """How much a candidate ADDS to an existing book. Higher = better diversifier.

    Defined as (1 - max signed correlation to any book member) / 2, in [0, 1]:
      identical to a member  -> 0.0  (adds nothing)
      uncorrelated           -> 0.5
      anti-correlated        -> 1.0  (negative correlation actively reduces
                                      portfolio variance, so signed corr — not
                                      |corr| — is the right penalty)
    Worst-case (max) member correlation is used because one redundant overlap
    is enough to make the candidate redundant. Flat streams correlate 0 →
    neutral 0.5. Empty book → 0.5 (nothing to diversify against).
    """
    if not book_streams:
        return 0.5
    max_corr = max(_pearson(s, candidate_stream) for s in book_streams)
    return (1.0 - max_corr) / 2.0
