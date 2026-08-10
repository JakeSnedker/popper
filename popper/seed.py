"""Seed the alpha memory with a realistic spread of hypotheses + trails.

So the dashboard opens with a populated happy-path on day one. Idempotent-ish: it appends,
so run against a fresh DB (delete popper.db first) for clean numbers.

DEMO DATA ONLY. These five fixtures were once seeded into the production Supabase
memory, where nothing distinguished them from research output: two of them held
promoted/survived status with zero gate evidence and read as validated alphas on
the dashboard, and two more sat in the untested backlog forever. Seeding now
refuses to touch a shared store — see `seed()`.
"""

from popper.artifacts import (
    ExperimentResult,
    Hypothesis,
    HypothesisStatus,
    Regime,
    Scorecard,
    StrategySpec,
    Verdict,
    VerdictType,
)
from popper.config import settings
from popper.memory import get_memory


def _spec(hyp_id: str, name: str, *, universe=None, timeframe="5m", features=None) -> StrategySpec:
    return StrategySpec(
        hypothesis_id=hyp_id, name=name,
        alpha={
            "features": features or ["atr_14", "ema_50", "session_ny"],
            "signal": "close > ema_50 AND session_ny > 0 AND atr_14 > p[atr_min]",
        },
        wrapper={
            "stop": {"type": "atr", "mult": "p[sl_atr]"},
            "target": {"type": "rr", "ratio": "p[rr]"},
            "sizing": {"risk_pct": 0.5},
            "constraints": {"max_trades_per_day": 3},
        },
        context={"universe": universe or ["US30"], "timeframe": timeframe},
        param_space={"atr_min": [0.5, 1.0, 1.5], "sl_atr": [1.0, 1.5, 2.0], "rr": [1.5, 2.0, 2.5, 3.0]},
    )


def seed(force: bool = False) -> None:
    # A shared store is the team's evidence base. Demo fixtures there are not a
    # cosmetic problem: they are indistinguishable from real results, and the two
    # that carried a passing status made the dashboard claim three validated
    # alphas when there was one. Keep examples and live research in separate stores.
    if settings.store != "sqlite" and not force:
        raise RuntimeError(
            f"refusing to seed demo data into the {settings.store!r} store - it would be "
            "indistinguishable from real research. Seed a local sqlite store instead "
            "(POPPER_STORE=sqlite), or pass force=True if you really mean it."
        )
    mem = get_memory()

    # 1) PROMOTED — survived everything, now live
    h1 = mem.add_hypothesis(Hypothesis(
        claim="NY-open momentum on US30 persists 15-30m after a >0.8 ATR opening drive",
        mechanism="Liquidity vacuum at the cash open: resting orders cleared, trend continues until late longs fade.",
        rationale="Consistent with intraday auction theory; opening drive signals informed flow.",
        required_features=["atr_14", "session_ny", "opening_range"],
        expected_regime=Regime.TRENDING, novelty_score=0.81, status=HypothesisStatus.PROMOTED,
    ))
    s1 = mem.add_spec(_spec(h1.id, "ny_open_momentum"))
    e1 = mem.add_experiment(ExperimentResult(
        spec_id=s1.id, dataset="US30 5m 2022-2025", period_start="2022-01-01", period_end="2025-06-01",
        trades_n=412, raw_metrics={"pnl": 0.34, "sharpe": 1.62, "win_rate": 0.41, "max_dd": 0.11, "expectancy": 0.28},
        equity_curve=[0, 0.04, 0.03, 0.09, 0.12, 0.18, 0.16, 0.24, 0.29, 0.34],
    ))
    mem.add_scorecard(Scorecard(experiment_id=e1.id, robustness=0.78, regime_stability=0.71,
                                capacity=0.64, execution_realism=0.69, novelty=0.81,
                                deflated_sharpe=1.18, pbo=0.12))
    mem.add_verdict(Verdict(hypothesis_id=h1.id, experiment_id=e1.id, type=VerdictType.PROMOTE,
                            reasons=["pbo=0.12", "deflated_sharpe>1", "regime_stability=0.71"]))

    # 2) SURVIVED — passed the gauntlet, queued for promotion
    h2 = mem.add_hypothesis(Hypothesis(
        claim="NAS100 mean-reverts after a 3-sigma 1m spike into the London-NY overlap",
        mechanism="Overlap liquidity absorbs stop-runs; price reverts to VWAP as the spike's flow exhausts.",
        required_features=["zscore_1m", "vwap", "session_overlap"],
        expected_regime=Regime.HIGH_VOL, novelty_score=0.73, status=HypothesisStatus.SURVIVED,
    ))
    s2 = mem.add_spec(_spec(h2.id, "nas_overlap_reversion", universe=["NAS100"],
                            features=["zscore_1m", "vwap", "session_overlap"]))
    e2 = mem.add_experiment(ExperimentResult(
        spec_id=s2.id, dataset="NAS100 1m 2023-2025", period_start="2023-01-01", period_end="2025-06-01",
        trades_n=289, raw_metrics={"pnl": 0.21, "sharpe": 1.31, "win_rate": 0.58, "max_dd": 0.09, "expectancy": 0.19},
        equity_curve=[0, 0.02, 0.05, 0.04, 0.08, 0.11, 0.13, 0.17, 0.19, 0.21],
    ))
    mem.add_scorecard(Scorecard(experiment_id=e2.id, robustness=0.66, regime_stability=0.62,
                                capacity=0.55, execution_realism=0.6, novelty=0.73,
                                deflated_sharpe=0.94, pbo=0.28))
    mem.add_verdict(Verdict(hypothesis_id=h2.id, experiment_id=e2.id, type=VerdictType.SURVIVE,
                            reasons=["pbo=0.28", "robustness=0.66"]))

    # 3) KILLED — looked great, was overfit
    h3 = mem.add_hypothesis(Hypothesis(
        claim="A 7-EMA / 23-EMA cross on US30 15m prints 80%+ win rate",
        mechanism="(weak) curve-fit to two specific EMA lengths; no structural reason given.",
        required_features=["ema_7", "ema_23"], expected_regime=Regime.TRENDING,
        novelty_score=0.22, status=HypothesisStatus.KILLED,
    ))
    s3 = mem.add_spec(_spec(h3.id, "ema_7_23_cross", timeframe="15m", features=["ema_7", "ema_23"]))
    e3 = mem.add_experiment(ExperimentResult(
        spec_id=s3.id, dataset="US30 15m 2024", period_start="2024-01-01", period_end="2024-12-31",
        trades_n=64, raw_metrics={"pnl": 0.51, "sharpe": 2.9, "win_rate": 0.83, "max_dd": 0.05, "expectancy": 0.44},
        equity_curve=[0, 0.08, 0.17, 0.21, 0.3, 0.36, 0.41, 0.46, 0.49, 0.51],
    ))
    mem.add_scorecard(Scorecard(experiment_id=e3.id, robustness=0.18, regime_stability=0.21,
                                capacity=0.4, execution_realism=0.45, novelty=0.22,
                                deflated_sharpe=0.31, pbo=0.74))
    mem.add_verdict(Verdict(hypothesis_id=h3.id, experiment_id=e3.id, type=VerdictType.KILL,
                            reasons=["pbo=0.74 (overfit)", "deflated_sharpe=0.31", "fails on out-of-sample year",
                                     "parameter surface unstable", "novelty=0.22 (near prior killed idea)"]))

    # 4) TESTING — in the gauntlet now
    mem.add_hypothesis(Hypothesis(
        claim="US30 trends continue through the first 30m post-FOMC when pre-release range < 0.5 ATR",
        mechanism="Coiled pre-event volatility releases directionally once the catalyst removes uncertainty.",
        required_features=["atr_14", "news_fomc", "pre_event_range"],
        expected_regime=Regime.HIGH_VOL, novelty_score=0.69, status=HypothesisStatus.TESTING,
    ))

    # 5) PROPOSED — waiting for a slot
    mem.add_hypothesis(Hypothesis(
        claim="NAS100 gap-fills before 10:30 ET when the overnight gap < 0.7 ATR and prior day closed strong",
        mechanism="Small gaps lack conviction; dealer hedging pulls price back to fill the imbalance.",
        required_features=["overnight_gap", "atr_14", "prior_close_strength"],
        expected_regime=Regime.RANGING, novelty_score=0.58, status=HypothesisStatus.PROPOSED,
    ))

    print("Seeded alpha memory:", mem.stats())


if __name__ == "__main__":
    seed()
