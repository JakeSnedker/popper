import pytest

from popper.artifacts import StrategySpec
from popper.forge import ForgeUnsupported, forge_record, forge_spec
from popper.forge.expr import compile_expression


def _spec(**overrides):
    data = {
        "id": "S-1",
        "hypothesis_id": "H-1",
        "name": "NY momentum",
        "alpha": {
            "features": ["atr_14", "ema_50", "session_ny"],
            "signal": "close > ema_50 AND session_ny > 0 AND atr_14 > p[atr_min]",
        },
        "wrapper": {
            "stop": {"type": "atr", "mult": "p[sl_atr]"},
            "target": {"type": "ratio", "ratio": "p[rr]"},
            "constraints": {"max_trades_per_day": 3},
        },
        "param_space": {
            "atr_min": [0.5, 1.0, 1.5],
            "sl_atr": [1.0, 1.5, 2.0],
            "rr": [1.5, 2.0, 2.5],
        },
    }
    data.update(overrides)
    return StrategySpec(**data)


def test_expression_translation_preserves_params_and_boolean_logic():
    pine = compile_expression("close > ema_50 AND atr_14 > p[atr_min]", "pine")
    mql = compile_expression("close > ema_50 AND atr_14 > p[atr_min]", "mql5")
    assert "and" in pine and "atr_min" in pine
    assert "&&" in mql and "InpAtrMin" in mql


def test_forge_emits_both_download_targets_with_middle_defaults():
    bundle = forge_spec(_spec())
    pine = next(value for name, value in bundle.files.items() if name.endswith(".pine"))
    mql = next(value for name, value in bundle.files.items() if name.endswith(".mq5"))
    assert "//@version=5" in pine
    assert 'atr_min = input.float(1' in pine
    assert "strategy.entry" in pine
    assert "#include <Trade/Trade.mqh>" in mql
    assert "input double InpAtrMin = 1;" in mql
    assert "iATR(_Symbol, _Period, 14)" in mql
    assert "double close = iClose(_Symbol, _Period, 1);" in mql
    assert "README.txt" in bundle.files


def test_forge_supports_current_regime_promoted_feature_set():
    spec = _spec(
        alpha={
            "features": ["cusum_shift", "vol_regime", "trend_regime", "regime_score",
                         "atr_14", "ema_50", "rel_volume", "delta_proxy"],
            "signal": "cusum_shift != 0 AND vol_regime == 1 AND trend_regime > 0 AND regime_score > p[score] AND rel_volume > 1.2 AND delta_proxy > 0",
            "signal_short": "delta_proxy < 0",
        },
        param_space={"score": [0.5, 0.6, 0.7], "sl_atr": [1.5], "rr": [3.0]},
    )
    bundle = forge_spec(spec)
    pine = next(value for name, value in bundle.files.items() if name.endswith(".pine"))
    mql = next(value for name, value in bundle.files.items() if name.endswith(".mq5"))
    assert "cusum_pos" in pine and "regime_score" in pine
    assert "CusumShift" in mql and "RegimeScore" in mql


def test_record_must_be_promoted():
    record = {"hypothesis": {"status": "survived"}, "specs": [_spec().model_dump(mode="json")],
              "experiments": [], "verdicts": []}
    with pytest.raises(ForgeUnsupported, match="promoted"):
        forge_record(record)


def test_unsupported_feature_fails_closed():
    spec = _spec(alpha={"features": ["liquidity_sweep"], "signal": "liquidity_sweep > 0"},
                 param_space={"sl_atr": [1.5], "rr": [2.0]})
    with pytest.raises(ForgeUnsupported, match="does not implement"):
        forge_spec(spec)
