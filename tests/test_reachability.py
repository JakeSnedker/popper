"""A signal that can never fire must be rejected before anything expensive runs.

Every DEAD case here is copied from a real hypothesis that died at the L1 gate
with "signal fires only 0x"; every LIVE case is copied from one that fired.
"""

import pytest

from popper.dsl.reachability import reachability_errors as check
from popper.dsl.validator import DSLValidationError, validate_spec

# (signal, param_space) lifted from the corpus
DEAD = [
    ("trend_regime < 0", {}),                                    # 0/1, never negative
    ("atr_14 > ema_50", {}),                                     # points vs price
    ("bb_line < p[w]", {"w": [0.2, 0.3, 0.4]}),                  # bb_line is a price
    ("psar < 1", {}),                                            # also a price
    ("equilibrium < 0", {}),                                     # a price, always positive
    ("regime_score >= p[m]", {"m": [50, 60, 70]}),               # bounded to [-1, 1]
    ("rsi_fast > 120", {}),                                      # bounded to [0, 100]
    ("rel_volume < 0", {}),                                      # non-negative
    ("session_asia AND session_london AND mss > 0", {}),         # sessions partition the day
    ("session_asia == 1 AND session_ny == 1", {}),
]

# these fired on real bars and must NOT be condemned
LIVE = [
    ("close > ema_50 AND rsi_fast < p[b]", {"b": [30, 35]}),
    ("(session_london OR session_ny) AND rel_volume > 1.2", {}),
    ("close > opening_range", {}),                               # always true, but NOT dead
    ("ema_50 > 0", {}),                                          # tautology, not impossible
    ("session_ny AND opening_range > 0 AND session_asia < opening_range", {}),
    ("liquidity_sweep == -1 AND session_asia == 1 AND session_london == 0", {}),
    ("vol_regime >= 1 AND trend_regime > 0", {}),
    ("regime_score > p[t]", {"t": [0.4, 0.5]}),
]


@pytest.mark.parametrize("signal,params", DEAD)
def test_impossible_signals_are_caught(signal, params):
    assert check(signal, params), f"should be unreachable: {signal}"


@pytest.mark.parametrize("signal,params", LIVE)
def test_working_signals_are_not_condemned(signal, params):
    assert check(signal, params) == [], f"false positive on: {signal}"


def test_strict_validation_rejects_an_impossible_signal():
    spec = {
        "hypothesis_id": "H-X", "name": "n",
        "alpha": {"features": ["trend_regime"], "signal": "trend_regime > 0",
                  "signal_short": "trend_regime < 0"},
        "param_space": {},
    }
    validate_spec(spec)                       # lenient: the old corpus still loads
    with pytest.raises(DSLValidationError) as e:
        validate_spec(spec, strict=True)      # agent output is held to the contract
    assert any("never be true" in x for x in e.value.errors)


def test_the_domain_table_reaches_the_agent():
    from popper.dsl.features.domains import prompt_table
    table = prompt_table()
    assert "bb_line PRICE" in table
    assert "trend_regime {0,1}" in table
    assert "NEVER negative" in table
