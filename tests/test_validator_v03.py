"""DSL v0.3 validator (spec §2/§4): mandatory-exit law (a `level` stop/target
must name a valid anchor) and the side rule (stop = loss-side, target =
profit-side, bb_line either). Anchor/offset p[...] refs fold into the existing
param-space checks — including side-checking a categorical anchor grid."""

import pytest

from popper.dsl import DSLValidationError, validate_spec


def _spec(**overrides) -> dict:
    base = {
        "hypothesis_id": "H-V03", "name": "t",
        "alpha": {"features": ["ema_50"], "signal": "close > ema_50"},
        "param_space": {},
    }
    base.update(overrides)
    return base


def _errors(**overrides) -> str:
    with pytest.raises(DSLValidationError) as e:
        validate_spec(_spec(**overrides))
    return str(e.value)


# --- happy paths ---

def test_level_stop_and_target_validate():
    validate_spec(_spec(wrapper={
        "stop": {"type": "level", "anchor": "prior_swing_wick_lo"},
        "target": {"type": "level", "anchor": "prior_swing_wick_hi", "offset": -2.0},
    }))


def test_bb_line_is_either_side():
    validate_spec(_spec(wrapper={
        "stop": {"type": "level", "anchor": "bb_line"},
        "target": {"type": "level", "anchor": "bb_line"},
    }))


def test_v02_atr_rr_spec_still_validates():
    validate_spec(_spec(wrapper={
        "stop": {"type": "atr", "mult": 1.5},
        "target": {"type": "rr", "ratio": 2.0},  # v0.2 alias for ratio
    }))


def test_anchor_and_offset_param_refs_resolve():
    validate_spec(_spec(
        wrapper={
            "stop": {"type": "level", "anchor": "p[sl_anchor]", "offset": "p[sl_off]"},
            "target": {"type": "ratio", "ratio": 2.0},
        },
        param_space={"sl_anchor": ["prior_swing_wick_lo", "prior_fractal_lo"],
                     "sl_off": [-2.0, 0.0]},
    ))


# --- mandatory-exit law ---

def test_level_stop_without_anchor_rejected():
    assert "mandatory-exit law" in _errors(
        wrapper={"stop": {"type": "level"}})


def test_level_target_without_anchor_rejected():
    assert "mandatory-exit law" in _errors(
        wrapper={"target": {"type": "level"}})


# --- anchor membership + side rule ---

def test_unknown_anchor_rejected():
    assert "unknown level-feature" in _errors(
        wrapper={"stop": {"type": "level", "anchor": "magic_level"}})


def test_stop_cannot_anchor_profit_side_level():
    msg = _errors(wrapper={"stop": {"type": "level", "anchor": "prior_swing_wick_hi"}})
    assert "side rule" in msg and "loss-side" in msg


def test_target_cannot_anchor_loss_side_level():
    msg = _errors(wrapper={"target": {"type": "level", "anchor": "session_ny_lo"}})
    assert "side rule" in msg


def test_categorical_anchor_grid_is_side_checked():
    # one profit-side value hiding in a stop-anchor grid must be caught
    msg = _errors(
        wrapper={"stop": {"type": "level", "anchor": "p[a]"}},
        param_space={"a": ["prior_swing_wick_lo", "prior_fractal_hi"]},
    )
    assert "side rule" in msg and "prior_fractal_hi" in msg


# --- param-ref folding ---

def test_anchor_ref_missing_from_param_space_rejected():
    assert "p[ghost] referenced but not in param_space" in _errors(
        wrapper={"stop": {"type": "level", "anchor": "p[ghost]"}})


def test_offset_ref_missing_from_param_space_rejected():
    assert "p[off] referenced but not in param_space" in _errors(
        wrapper={"stop": {"type": "level", "anchor": "prior_swing_wick_lo",
                          "offset": "p[off]"}})


def test_offset_garbage_string_rejected():
    assert "not a number or p[name] reference" in _errors(
        wrapper={"target": {"type": "level", "anchor": "prior_swing_wick_hi",
                            "offset": "two points"}})
