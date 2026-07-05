"""DSL v0.3 layered falsification (spec §3/§5/§7): Layer-1 signal-edge gate
kills no-edge signals cheaply BEFORE the expensive gates; the staged first-pass
exit menu (27 SL x TP configs) runs on Layer-1 survivors only and its breadth
feeds DSR n_trials. Offline + deterministic: synthetic bars, injected run_fn."""

import numpy as np
import pandas as pd

from test_falsify import END, START, _bars, _cfg, _run_fn_over

from popper.artifacts import StrategySpec, VerdictType
from popper.falsify import judge
from popper.falsify.gauntlet import _signal_edge


def _uptrend_bars(n=13000, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 + 0.04 * np.arange(n) + rng.normal(0, 1.5, n).cumsum()
    return _bars(close)


def _mean_reverting_bars(n=8000, seed=5):
    """OU process: strongly mean-reverting, so MOMENTUM has real negative edge."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.95 * x[i - 1] + rng.normal(0, 2.0)
    return _bars(300 + x)


def _momo_spec(**overrides) -> StrategySpec:
    base = dict(
        hypothesis_id="H-V03", name="momo",
        alpha={"features": ["ema_10"], "signal": "close > ema_10"},
        context={"universe": ["US30"], "timeframe": "1h"},
        param_space={},
    )
    base.update(overrides)
    return StrategySpec(**base)


# --- Layer 1: the signal-edge gate ---

def test_layer1_kills_no_edge_signal_before_expensive_gates():
    bars = _mean_reverting_bars()
    sc, v = judge(_momo_spec(), run_fn=_run_fn_over(bars), period=(START, END),
                  cfg=_cfg(), bars=bars)
    assert v.type == VerdictType.KILL
    assert v.reasons and v.reasons[0].startswith("no signal edge")
    se = sc.diagnostics["gate_signal_edge"]
    assert se["passed"] is False and len(se["horizons"]) == 4
    # hard-killed FIRST: none of the expensive gates ever ran
    assert "gate1_walkforward" not in sc.diagnostics
    assert "gate4_multiple_testing" not in sc.diagnostics


def test_layer1_passes_predictive_signal():
    se = _signal_edge(_momo_spec(), _uptrend_bars())
    assert se["passed"] is True
    assert all(h["mean"] > 0 for h in se["horizons"].values())
    assert sum(h["t"] >= 2 for h in se["horizons"].values()) >= 3


def test_layer1_sign_adjusts_short_signals():
    n = 13000
    rng = np.random.default_rng(7)
    bars = _bars(1200 - 0.04 * np.arange(n) + rng.normal(0, 1.5, n).cumsum())
    spec = _momo_spec(alpha={"features": ["ema_10"], "signal": "close > p[never]",
                             "signal_short": "close < ema_10"},
                      param_space={"never": [9e9]})
    se = _signal_edge(spec, bars)
    assert se["passed"] is True  # falling prices are FAVOURABLE for a short


def test_layer1_rejects_too_few_signals():
    bars = _uptrend_bars(n=8000)
    spec = _momo_spec(alpha={"features": ["atr_14"], "signal": "atr_14 > p[floor]"},
                      param_space={"floor": [9e9]})  # never fires
    se = _signal_edge(spec, bars)
    assert se["passed"] is False and "no evidence" in se["reason"]


def test_layer1_skips_cleanly_without_bars():
    """Injected run_fn + no bars: Layer-1 records skipped and gates 0-4 behave
    exactly as v0.2 (the pre-v0.3 test contract)."""
    bars = _uptrend_bars(n=8000)
    spec = _momo_spec(alpha={"features": ["atr_14"], "signal": "atr_14 > p[floor]"},
                      param_space={"floor": [9e9]})
    sc, v = judge(spec, run_fn=_run_fn_over(bars), period=(START, END), cfg=_cfg())
    assert sc.diagnostics["gate_signal_edge"]["passed"] is None
    assert v.type == VerdictType.KILL  # dead spec still dies in the old gates
