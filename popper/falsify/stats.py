"""Statistical falsification primitives.

PORTED from the JJC validation-framework (monte_carlo.py, analyze_edge.py):
  monte_carlo_ruin  — trade-order shuffle -> P(profit), P(ruin), DD distribution
  calculate_edge    — Expected Edge = (PF - 1) * trades

ADDED (not in JJC — from Marcos López de Prado, *Advances in Financial ML*):
  deflated_sharpe   — Sharpe corrected for #trials + skew/kurtosis (DSR)
  pbo_cscv          — Probability of Backtest Overfitting via CSCV
  purged_kfold      — purged & embargoed CV fold indices (leakage-safe splits)

All trade inputs are per-trade R-multiples (Popper's native unit)."""

import math
import random
from itertools import combinations
from statistics import NormalDist

_N = NormalDist()


# --- PORT: analyze_edge.calculate_edge ---
def calculate_edge(pf: float, trades: int) -> float:
    """Expected Edge = (PF - 1) * trades. Opportunity-aware: a moderate PF with
    many trades can beat a high PF with few."""
    return (pf - 1.0) * trades


# --- PORT: monte_carlo.py (shuffle), reduced to the metrics the gauntlet needs ---
def monte_carlo_ruin(
    trades: list[float], n_sims: int = 2000, ruin_dd: float = 0.10,
    risk_frac: float = 0.005, seed: int = 42,
) -> dict:
    """Shuffle trade order n_sims times; equity compounds each trade by
    risk_frac * pnl_r (matches the runner's sizing). Returns P(profit), P(ruin =
    max DD > ruin_dd), and the DD distribution mean/95th/worst."""
    if not trades:
        return {"p_profit": 0.0, "p_ruin": 1.0, "dd_mean": 1.0, "dd_95": 1.0, "dd_worst": 1.0}
    rng = random.Random(seed)
    profits, dds = 0, []
    seq = list(trades)
    for _ in range(n_sims):
        rng.shuffle(seq)
        eq, peak, mdd = 1.0, 1.0, 0.0
        for r in seq:
            eq *= 1.0 + risk_frac * r
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak)
        if eq > 1.0:
            profits += 1
        dds.append(mdd)
    dds.sort()
    return {
        "p_profit": profits / n_sims,
        "p_ruin": sum(1 for d in dds if d > ruin_dd) / n_sims,
        "dd_mean": sum(dds) / len(dds),
        "dd_95": dds[int(0.95 * len(dds))],
        "dd_worst": dds[-1],
    }


# --- ADD: Deflated Sharpe Ratio (LdP) ---
def _sharpe(returns: list[float]) -> float:
    n = len(returns)
    if n < 2:
        return 0.0
    m = sum(returns) / n
    var = sum((r - m) ** 2 for r in returns) / (n - 1)
    return m / math.sqrt(var) if var > 0 else 0.0


def _skew_kurt(returns: list[float]) -> tuple[float, float]:
    """Sample skewness and (non-excess) kurtosis."""
    n = len(returns)
    m = sum(returns) / n
    s2 = sum((r - m) ** 2 for r in returns) / n
    if s2 == 0:
        return 0.0, 3.0
    s = math.sqrt(s2)
    skew = sum(((r - m) / s) ** 3 for r in returns) / n
    kurt = sum(((r - m) / s) ** 4 for r in returns) / n
    return skew, kurt


def expected_max_sharpe(sr_std: float, n_trials: int) -> float:
    """E[max SR] over n_trials independent strategies with SR dispersion sr_std
    (LdP false-strategy theorem). The benchmark a real edge must beat."""
    if n_trials < 2 or sr_std <= 0:
        return 0.0
    euler = 0.5772156649
    z1 = _N.inv_cdf(1 - 1.0 / n_trials)
    z2 = _N.inv_cdf(1 - 1.0 / (n_trials * math.e))
    return sr_std * ((1 - euler) * z1 + euler * z2)


def deflated_sharpe(returns: list[float], n_trials: int, sr_std: float) -> float:
    """DSR: probability the strategy's Sharpe exceeds the expected-max Sharpe of
    n_trials random trials, correcting for sample skew/kurtosis. In [0,1];
    higher = more likely a real (non-fluke) edge. Needs >=2 trials to deflate."""
    n = len(returns)
    if n < 2:
        return 0.0
    sr = _sharpe(returns)
    sr0 = expected_max_sharpe(sr_std, n_trials)
    skew, kurt = _skew_kurt(returns)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return 0.0
    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom)
    return _N.cdf(z)


# --- ADD: PBO via CSCV (LdP) ---
def pbo_cscv(perf_matrix: list[list[float]], s: int = 8) -> float:
    """Probability of Backtest Overfitting via Combinatorially-Symmetric CV.

    perf_matrix[t][c] = performance of param combo c in time-slice t (higher is
    better). Split the T slices into s groups; for every way to choose s/2 groups
    as IS (rest OOS), pick the best combo IS and read its OOS rank. PBO = fraction
    of splits where the IS winner lands in the bottom half OOS. High PBO => the
    in-sample winner is overfit noise. Needs >=2 combos and >=s slices."""
    T = len(perf_matrix)
    if T < s or not perf_matrix or len(perf_matrix[0]) < 2:
        return 1.0  # cannot certify -> treat as fully overfit (fail-safe)
    n_combos = len(perf_matrix[0])
    if s % 2:
        s -= 1
    groups = [list(range(i * T // s, (i + 1) * T // s)) for i in range(s)]

    def agg(rows, c):
        return sum(perf_matrix[t][c] for t in rows)

    logits = []
    for is_groups in combinations(range(s), s // 2):
        is_rows = [t for g in is_groups for t in groups[g]]
        oos_rows = [t for g in range(s) if g not in is_groups for t in groups[g]]
        is_perf = [agg(is_rows, c) for c in range(n_combos)]
        best = max(range(n_combos), key=lambda c: is_perf[c])
        oos_perf = [agg(oos_rows, c) for c in range(n_combos)]
        rank = sum(1 for v in oos_perf if v <= oos_perf[best])  # 1..n_combos
        w = rank / (n_combos + 1)  # relative OOS rank in (0,1)
        w = min(max(w, 1e-6), 1 - 1e-6)
        logits.append(math.log(w / (1 - w)))
    return sum(1 for lam in logits if lam <= 0) / len(logits)


# --- ADD: purged & embargoed K-fold (LdP) ---
def purged_kfold(n: int, k: int = 5, embargo: float = 0.01) -> list[tuple[list[int], list[int]]]:
    """Yield (train_idx, test_idx) for k contiguous test folds, PURGING train
    samples adjacent to the test block and EMBARGOing `embargo`*n samples after
    it — so no train sample can leak information across the test boundary."""
    idx = list(range(n))
    fold = n // k
    emb = int(n * embargo)
    out = []
    for i in range(k):
        lo = i * fold
        hi = n if i == k - 1 else (i + 1) * fold
        test = idx[lo:hi]
        train = idx[: max(0, lo)] + idx[min(n, hi + emb):]
        out.append((train, test))
    return out
