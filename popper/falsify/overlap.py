"""Honest significance for OVERLAPPING forward returns.

The L1 signal-edge gate measures forward return over h bars, computed at EVERY
bar the signal fired. At h=60 two consecutive observations share 59/60 of their
window, so they are not independent and the plain

    t = mean / (sd / sqrt(n))

overstates significance by up to ~sqrt(h) when signals are dense -- H-0010 fired
on 73% of bars. Pulling the other way, that same t punishes the payoff shape
worth finding: fat right tails inflate `sd`, so a 30%-hit-rate strategy with 5R
winners scores badly on a statistic that assumes thin tails. The two errors do
not cancel, and which dominates depends on signal density.

Two corrections, deliberately separate so each can be read on its own:

  newey_west_t   -- the same t with a HAC standard error, so overlap no longer
                    buys free significance.
  block_bootstrap_lower -- a lower confidence bound on the mean that makes no
                    normality assumption at all, so skew is judged on whether
                    the average is reliably positive rather than on how noisy
                    it is. Moving blocks, because resampling single observations
                    would destroy the very autocorrelation being corrected for.
"""

from __future__ import annotations

import numpy as np


def newey_west_t(r: np.ndarray, lag: int) -> float:
    """t-statistic for mean(r) > 0 with a Bartlett-kernel HAC variance.

    `lag` is the overlap length: h-1 for an h-bar forward return, since exactly
    that many neighbours share window with any given observation.
    """
    n = r.size
    if n < 3:
        return 0.0
    mu = float(r.mean())
    d = r - mu
    gamma0 = float(d @ d) / n
    if gamma0 <= 0:
        return 0.0
    total = gamma0
    for l in range(1, min(lag, n - 1) + 1):
        w = 1.0 - l / (lag + 1.0)          # Bartlett weight, guarantees positivity
        gamma_l = float(d[l:] @ d[:-l]) / n
        total += 2.0 * w * gamma_l
    if total <= 0:                          # HAC can go non-positive on short samples
        return 0.0
    se = np.sqrt(total / n)
    return float(mu / se) if se > 0 else 0.0


def block_bootstrap_lower(
    r: np.ndarray,
    block: int,
    alpha: float = 0.05,
    n_boot: int = 400,
    seed: int = 0,
) -> float:
    """Lower `1-alpha` bound on mean(r) via a moving block bootstrap.

    Blocks preserve the local dependence that overlapping windows create. A
    bound above zero says the average return is reliably positive WITHOUT
    assuming normality -- which is the whole point for a skewed payoff.
    """
    n = r.size
    block = max(1, min(int(block), n))
    if n < 3:
        return 0.0
    n_blocks = max(1, n // block)
    starts_hi = n - block + 1
    rng = np.random.default_rng(seed)
    # (n_boot, n_blocks) block starts -> index matrix -> per-resample mean
    starts = rng.integers(0, starts_hi, size=(n_boot, n_blocks))
    idx = starts[:, :, None] + np.arange(block)[None, None, :]
    means = r[idx.reshape(n_boot, -1)].mean(axis=1)
    return float(np.quantile(means, alpha))


def effective_n(n: int, lag: int) -> int:
    """Roughly how many independent observations `n` overlapping ones are worth.

    Reported alongside `n` so a large sample that is mostly redundant is visible
    rather than reassuring.
    """
    return max(1, int(n / max(1, lag + 1)))
