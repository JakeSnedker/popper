"""Direction must be declared, and the declaration must match reality."""

import pytest

from popper.artifacts.strategy_spec import StrategySpec
from popper.dsl.compiler import direction_realized_errors
from popper.dsl.validator import DSLValidationError, validate_spec


def _spec(**alpha):
    base = {
        "hypothesis_id": "H-TEST",
        "name": "t",
        "alpha": {"features": ["ema_50", "atr_14"], "signal": "close > ema_50", **alpha},
        "param_space": {},
    }
    return base


def test_direction_both_requires_a_short_signal():
    with pytest.raises(DSLValidationError) as e:
        validate_spec(_spec(), require_direction=True)
    assert any("signal_short is missing" in x for x in e.value.errors)


def test_long_only_is_allowed_but_must_be_argued():
    with pytest.raises(DSLValidationError) as e:
        validate_spec(_spec(direction="long_only"), require_direction=True)
    assert any("direction_rationale" in x for x in e.value.errors)

    ok = validate_spec(_spec(direction="long_only", direction_rationale="overnight drift is up-only"), require_direction=True)
    assert ok.alpha.direction == "long_only"


def test_long_only_may_not_also_carry_a_short_signal():
    with pytest.raises(DSLValidationError) as e:
        validate_spec(_spec(direction="long_only", direction_rationale="why",
                            signal_short="close < ema_50"), require_direction=True)
    assert any("long_only" in x and "signal_short is set" in x for x in e.value.errors)


def test_bidirectional_spec_passes():
    spec = validate_spec(_spec(signal_short="close < ema_50"), require_direction=True)
    assert spec.alpha.direction == "both"


def test_unreachable_short_branch_is_caught_empirically():
    """The H-0010 defect: declared bi-directional, structurally long-only."""
    spec = StrategySpec.model_validate(_spec(signal_short="close < ema_50"))
    errors = direction_realized_errors(spec, {"signal_bars_long": 400, "signal_bars_short": 0})
    assert errors and "unreachable" in errors[0]

    assert direction_realized_errors(spec, {"signal_bars_long": 400, "signal_bars_short": 300}) == []


def test_a_signal_that_never_fires_is_caught():
    spec = StrategySpec.model_validate(_spec(signal_short="close < ema_50"))
    errors = direction_realized_errors(spec, {"signal_bars_long": 0, "signal_bars_short": 5})
    assert any("never fires" in x for x in errors)
