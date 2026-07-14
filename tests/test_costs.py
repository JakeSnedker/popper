"""Cost model: frictionless default preserved; costs deduct the right R.
Net-of-cost metrics are INFORMATIONAL and provably NON-GATING."""
import numpy as np
import pandas as pd
import pytest

from popper.artifacts import ExperimentResult, VerdictType
from popper.artifacts.strategy_spec import Alpha, StrategySpec
from popper.config import settings
from popper.dsl import compile_to_backtest
from popper.experiments.runner import compute_metrics
from popper.falsify import GauntletConfig, judge


def _bars(n=60):
    # gentle uptrend so a "close > open of 20 bars ago"-ish long triggers & wins
    t = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    base = np.linspace(100.0, 130.0, n)
    return pd.DataFrame({
        "time": t, "open": base, "high": base + 2.0, "low": base - 2.0,
        "close": base + 1.0, "tick_volume": 100, "spread": 50,  # 50 pts
    })


def _spec():
    return StrategySpec(hypothesis_id="H", name="t",
                        alpha=Alpha(features=["ema_10"], signal="close > ema_10"),
                        param_space={})


def test_frictionless_is_default():
    bt = compile_to_backtest(_spec())
    r = bt(_bars())
    assert all(t["cost_r"] == 0.0 for t in r["trades"])


def test_costs_reduce_pnl_by_expected_r():
    bt = compile_to_backtest(_spec())
    bars = _bars()
    clean = bt(bars)
    costs = {"point_value": 0.1, "slippage_pts": 1.0, "commission_pts": 2.0}
    dirty = bt(bars, costs=costs)
    assert clean["n_trades"] == dirty["n_trades"] and clean["n_trades"] > 0
    # per trade: 0.5*(50+50)*0.1 spread + 2*1*0.1 slip + 2*0.1 comm = 5.0 + 0.2 + 0.2
    expected_cost_px = 0.5 * (50 + 50) * 0.1 + 2 * 1.0 * 0.1 + 2 * 0.1  # = 5.4
    for cl, dt in zip(clean["trades"], dirty["trades"]):
        # the R deducted IS the difference between the clean and dirty pnl_r
        assert dt["cost_r"] > 0
        assert cl["pnl_r"] - dt["pnl_r"] == pytest.approx(dt["cost_r"], abs=2e-4)
        # and it equals cost_px / risk; recover risk (price) from clean R
        risk_px = abs(cl["exit"] - cl["entry"]) / abs(cl["pnl_r"])
        assert dt["cost_r"] == pytest.approx(expected_cost_px / risk_px, rel=1e-2)


def test_trade_records_risk():
    # risk (price distance of 1R) is on every trade, so net-of-cost metrics can
    # be derived exactly from a frictionless run without a second backtest
    r = compile_to_backtest(_spec())(_bars())
    assert r["n_trades"] > 0
    for t in r["trades"]:
        assert t["risk"] > 0
        # pnl_r * risk recovers the signed price move
        d = 1 if t["dir"] == "long" else -1
        assert t["pnl_r"] * t["risk"] == pytest.approx((t["exit"] - t["entry"]) * d, abs=0.02)


def test_net_metrics_informational_gross_unchanged():
    res = compile_to_backtest(_spec())(_bars())
    gross = compute_metrics(res)
    spread = _bars()["spread"].to_numpy()
    with_net = compute_metrics(res, spread=spread)
    # (a) gross metrics byte-identical whether or not the cost model runs
    assert {k: with_net[k] for k in gross} == gross
    # (b) net numbers exist and show the drag (default point_value=0.01, spread=50)
    assert with_net["avg_cost_r"] > 0
    assert with_net["expectancy_net"] < gross["expectancy"]
    assert with_net["profit_factor_net"] <= gross["profit_factor"]  # inf == inf when no net losers
    assert 0.0 <= with_net["pct_trades_cost_flip"] <= 1.0


# --- cost is provably NON-GATING: identical verdict with/without cost model ---

START, END = "2025-01-01", "2026-08-01"


def _synth_bars(n=13000):
    rng = np.random.default_rng(3)
    close = 100 + 0.04 * np.arange(n) + rng.normal(0, 1.5, n).cumsum()
    time = pd.date_range(START, periods=n, freq="1h", tz="UTC")
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    wick = np.abs(np.diff(close, prepend=close[0])) + 1.0
    return pd.DataFrame({
        "time": time, "open": open_, "high": np.maximum(open_, close) + wick,
        "low": np.minimum(open_, close) - wick, "close": close,
        "tick_volume": 100, "spread": 50,
    })


def _run_fn_over(bars: pd.DataFrame, seen: list):
    def run_fn(spec, period=None, params=None):
        df = bars
        if period:
            s, e = period
            df = bars[(bars["time"] >= pd.Timestamp(s, tz="UTC"))
                      & (bars["time"] <= pd.Timestamp(e, tz="UTC"))].reset_index(drop=True)
        if len(df) < 5:
            res = {"trades": [], "equity_curve": [1.0], "total_return": 0.0, "n_trades": 0}
        else:
            res = compile_to_backtest(spec)(df, params)
        spread = df["spread"].to_numpy() if "spread" in df.columns else None
        m = compute_metrics(res, spread=spread)
        m["trades"] = res["trades"]
        seen.append(m)
        return ExperimentResult(
            spec_id=spec.id or "", dataset="synth",
            period_start=str(period[0]) if period else "", period_end=str(period[1]) if period else "",
            trades_n=res["n_trades"], raw_metrics=m, equity_curve=res["equity_curve"],
        )
    return run_fn


def test_verdict_identical_with_and_without_cost_model(monkeypatch):
    spec = StrategySpec(
        hypothesis_id="H-COST", name="trend_follow",
        alpha={"features": ["ema_10"], "signal": "close > ema_10"},
        wrapper={"stop": {"type": "atr", "mult": 1.5}, "target": {"type": "rr", "ratio": "p[rr]"}},
        context={"universe": ["US30"], "timeframe": "1h"},
        param_space={"rr": [1.5, 2.0, 2.5]},
    )
    cfg = GauntletConfig(mc_sims=300)
    bars = _synth_bars()

    # run A: no spread column -> no net metrics at all (cost model effectively off)
    bare = bars.drop(columns=["spread"])
    seen_a: list = []
    sc_a, v_a = judge(spec, run_fn=_run_fn_over(bare, seen_a), period=(START, END),
                      cfg=cfg, bars=bare)
    assert all("expectancy_net" not in m for m in seen_a)

    # run B: spread present AND a brutally expensive cost model
    monkeypatch.setattr(settings, "point_value", 0.1)
    monkeypatch.setattr(settings, "slippage_pts", 50.0)
    monkeypatch.setattr(settings, "commission_pts", 50.0)
    seen_b: list = []
    sc_b, v_b = judge(spec, run_fn=_run_fn_over(bars, seen_b), period=(START, END),
                      cfg=cfg, bars=bars)
    costed = [m for m in seen_b if "expectancy_net" in m]
    assert costed, "net metrics were never computed"
    assert all(m["expectancy_net"] < m["expectancy"] for m in costed)

    # THE point: verdict + scorecard are bit-identical -> cost never gates
    assert v_a.type == v_b.type != VerdictType.KILL
    assert v_a.reasons == v_b.reasons
    assert (sc_a.robustness, sc_a.parameter_stability, sc_a.deflated_sharpe, sc_a.pbo) \
        == (sc_b.robustness, sc_b.parameter_stability, sc_b.deflated_sharpe, sc_b.pbo)


if __name__ == "__main__":
    test_frictionless_is_default()
    test_costs_reduce_pnl_by_expected_r()
    test_trade_records_risk()
    test_net_metrics_informational_gross_unchanged()
    print("ok")
