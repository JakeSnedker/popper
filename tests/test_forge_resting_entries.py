"""Forge parity for v0.4 resting entries and time exits.

The emitters must trade the way popper/dsl/compiler.py backtested, or a gauntlet
result says nothing about the deployed code. These assert on the generated
source: the order function, the price arithmetic, the expiry and the time close.
"""

import re

import pandas as pd
import pytest

from popper.artifacts import StrategySpec
from popper.dsl.compiler import compile_to_backtest
from popper.dsl.features import atr_14
from popper.forge import ForgeUnsupported, forge_spec
from popper.forge.mql5 import generate_mql5
from popper.forge.pine import generate_pine

SIGNAL_BAR = 30  # the only bar whose close clears the signal threshold


def make_bars(rows):
    t = pd.date_range("2025-01-06 09:00", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame({
        "time": t,
        "open": [r[0] for r in rows], "high": [r[1] for r in rows],
        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
        "volume": [100.0] * len(rows),
    })


# One signal, one resting order. Bar 31 both dips below a buy limit and runs
# through a buy stop, and opens between them, so either order fills at exactly
# its resting price -- which is what makes the price arithmetic checkable.
BARS = make_bars(
    [(100.0, 100.5, 99.5, 100.0)] * SIGNAL_BAR
    + [(100.0, 106.0, 99.5, 105.5)]      # signal bar
    + [(105.5, 107.0, 104.0, 106.0)]     # fills a limit at its price, or a stop at its price
    + [(106.0, 106.2, 105.8, 106.0)] * 20
)
SIGNAL_CLOSE = float(BARS["close"].iloc[SIGNAL_BAR])
SIGNAL_ATR = float(atr_14(BARS).iloc[SIGNAL_BAR])


def spec(**wrapper):
    w = {"stop": {"type": "atr", "mult": 1.0}, "target": {"type": "ratio", "ratio": 2.0}}
    w.update(wrapper)
    return StrategySpec.model_validate({
        "id": "S-1", "hypothesis_id": "H-1", "name": "resting",
        "alpha": {"features": [], "signal": "close > 105", "signal_short": "close < 0"},
        "wrapper": w, "context": {"universe": ["US30"], "timeframe": "5m"},
    })


def pine_entry_price(source: str, side: str) -> float:
    """The price Pine rests the order at, evaluated at the signal bar."""
    line = next(l for l in source.splitlines() if f'strategy.entry("{side}"' in l)
    expr = re.search(r"(?:limit|stop)=(.+)\)$", line).group(1)
    return eval(expr, {"close": SIGNAL_CLOSE, "atr_14": SIGNAL_ATR})


def mql5_entry_price(source: str, is_long: bool) -> float:
    line = next(l for l in source.splitlines() if "double price = NormalizeDouble(signal_close" in l)
    expr = re.search(r"NormalizeDouble\((signal_close .+?), digits\)", line).group(1)
    expr = expr.replace("(is_long ? 1.0 : -1.0)", "1.0" if is_long else "-1.0")
    return eval(expr, {"signal_close": SIGNAL_CLOSE, "atr": SIGNAL_ATR})


def compiler_entry(kind: str) -> float:
    result = compile_to_backtest(spec(entry={"type": kind, "offset": 0.5, "expiry_bars": 5}))(BARS)
    assert result["n_trades"], f"{kind} order never filled -- fixture bars are wrong"
    return result["trades"][0]["entry"]


# --- market entries must be untouched by all of this ------------------------

def test_market_entry_emits_no_resting_or_time_constructs():
    pine, mql = generate_pine(spec()), generate_mql5(spec())
    assert 'strategy.entry("Long", strategy.long)' in pine
    assert "limit=close" not in pine and "stop=close" not in pine
    assert "strategy.cancel" not in pine and "strategy.close_all" not in pine
    assert "trade.Buy(lots" in mql
    assert "BuyLimit" not in mql and "BuyStop" not in mql
    assert "PlacePending" not in mql and "trade.PositionClose" not in mql


# --- resting order price: the sign is the bug that matters ------------------

def test_long_limit_rests_below_and_long_stop_rests_above_the_signal_close():
    limit = spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 5})
    stop = spec(entry={"type": "stop", "offset": 0.5, "expiry_bars": 5})
    assert pine_entry_price(generate_pine(limit), "Long") < SIGNAL_CLOSE
    assert pine_entry_price(generate_pine(stop), "Long") > SIGNAL_CLOSE
    assert mql5_entry_price(generate_mql5(limit), True) < SIGNAL_CLOSE
    assert mql5_entry_price(generate_mql5(stop), True) > SIGNAL_CLOSE


def test_short_limit_rests_above_and_short_stop_rests_below_the_signal_close():
    limit = spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 5})
    stop = spec(entry={"type": "stop", "offset": 0.5, "expiry_bars": 5})
    assert pine_entry_price(generate_pine(limit), "Short") > SIGNAL_CLOSE
    assert pine_entry_price(generate_pine(stop), "Short") < SIGNAL_CLOSE
    assert mql5_entry_price(generate_mql5(limit), False) > SIGNAL_CLOSE
    assert mql5_entry_price(generate_mql5(stop), False) < SIGNAL_CLOSE


@pytest.mark.parametrize("kind", ["limit", "stop"])
def test_emitted_price_equals_the_price_the_backtest_actually_filled(kind):
    """The strongest check available: BARS is built so the order fills at exactly
    its resting price, so the compiler's realized entry IS the resting price."""
    sp = spec(entry={"type": kind, "offset": 0.5, "expiry_bars": 5})
    filled = compiler_entry(kind)
    assert pine_entry_price(generate_pine(sp), "Long") == pytest.approx(filled, abs=0.005)
    assert mql5_entry_price(generate_mql5(sp), True) == pytest.approx(filled, abs=0.005)


@pytest.mark.parametrize("kind,pine_kw,mql_fn", [
    ("limit", "limit=", "trade.BuyLimit("), ("stop", "stop=close", "trade.BuyStop("),
])
def test_each_order_type_uses_its_own_order_function(kind, pine_kw, mql_fn):
    sp = spec(entry={"type": kind, "offset": 0.5, "expiry_bars": 5})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert pine_kw in pine
    assert mql_fn in mql
    assert ("SellLimit" if kind == "limit" else "SellStop") in mql


def test_a_swept_offset_becomes_an_input_in_both_targets():
    sp = spec(entry={"type": "limit", "offset": "p[off]", "expiry_bars": 5})
    sp.param_space = {"off": [0.25, 0.5, 0.75]}
    assert "limit=close - off * atr_14" in generate_pine(sp)
    assert "* InpOff * atr" in generate_mql5(sp)


# --- expiry and the one-order-at-a-time rule --------------------------------

def test_expiry_cancels_the_order_after_the_declared_number_of_bars():
    sp = spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 4})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert "if rest_bars >= 4" in pine
    assert 'strategy.cancel("Long")' in pine and 'strategy.cancel("Short")' in pine
    assert "ORDER_TIME_SETUP)) >= 4" in mql and "trade.OrderDelete(pending_ticket)" in mql
    assert "4 * PeriodSeconds()" in mql  # broker-side backstop on the pending order


def test_a_resting_order_blocks_a_new_signal():
    sp = spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 5})
    assert "rest_dir == 0 and" in generate_pine(sp)
    assert "else return;   // still resting" in generate_mql5(sp)


def test_the_stop_distance_uses_the_signal_bars_atr_not_the_fill_bars():
    sp = spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 5})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert "signal_atr := atr_14" in pine and "nz(signal_atr, atr_14)" in pine
    assert "PlacePending(true, atr_14, close)" in mql  # atr and close of the signal bar
    assert "pending_atr = atr;" in mql and "RepriceStops(pending_atr)" in mql


# --- time exit ---------------------------------------------------------------

def test_time_stop_closes_the_position_after_the_stop_and_target_are_checked():
    pine = generate_pine(spec(time_stop={"type": "bars", "max_bars": 12}))
    assert "max_bars_in_trade = math.floor(12)" in pine
    assert 'strategy.close_all(comment="time", immediately=true)' in pine
    # ordering is the semantics: the price exits are placed first, so a bar that
    # breaches the stop AND expires the clock is recorded as the stop
    assert pine.index('strategy.exit("Long exit"') < pine.index("strategy.close_all")


def test_time_stop_counts_completed_bars_since_the_fill_in_mql5():
    mql = generate_mql5(spec(time_stop={"type": "bars", "max_bars": 12}))
    assert "POSITION_TIME)) - 1 >= (int)12" in mql
    assert "trade.PositionClose(_Symbol);" in mql


def test_a_swept_max_bars_becomes_an_input_in_both_targets():
    sp = spec(time_stop={"type": "bars", "max_bars": "p[hold]"})
    sp.param_space = {"hold": [10, 20, 30]}
    assert "math.floor(hold)" in generate_pine(sp)
    assert ">= (int)InpHold" in generate_mql5(sp)


def test_no_time_stop_emits_no_time_close():
    assert "close_all" not in generate_pine(spec())
    assert "PositionClose" not in generate_mql5(spec())


def test_limit_and_time_stop_render_together():
    sp = spec(entry={"type": "limit", "offset": 0.5, "expiry_bars": 3},
              time_stop={"type": "bars", "max_bars": 6})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    assert "limit=close - 0.5 * atr_14" in pine and "math.floor(6)" in pine
    assert "trade.BuyLimit(" in mql and "- 1 >= (int)6" in mql
    bundle = forge_spec(sp)
    assert any(name.endswith(".pine") for name in bundle.files)
    assert any(name.endswith(".mq5") for name in bundle.files)


# --- what still refuses to render -------------------------------------------

@pytest.mark.parametrize("kind", ["limit", "stop"])
def test_an_anchored_resting_order_now_rests_at_the_level(kind):
    """Both targets render level features now (tests/test_forge_levels.py proves
    each one against compute_level), so an anchored order has a price to rest at:
    the anchor's value on the SIGNAL bar, with no offset -- exactly `rest_px =
    entry_lvl[i]` in the compiler."""
    sp = spec(entry={"type": kind, "anchor": "prior_fractal_lo", "expiry_bars": 5})
    pine, mql = generate_pine(sp), generate_mql5(sp)
    keyword = "limit" if kind == "limit" else "stop"
    assert f'strategy.entry("Long", strategy.long, {keyword}=prior_fractal_lo)' in pine
    assert "atr_14" not in pine.split("strategy.entry(\"Long\"")[1].split("\n")[0]
    assert "double anchor = LevelFractal(1, false);" in mql
    assert forge_spec(sp).files


@pytest.mark.parametrize("kind", ["limit", "stop"])
def test_a_level_exit_with_a_resting_order_still_fails_closed(kind):
    """The compiler measures 1R from the FILL bar's level. A broker needs the
    volume when the order is sent, bars earlier -- sizing off the placement bar
    would deploy a position size nobody backtested."""
    sp = spec(stop={"type": "level", "anchor": "prior_swing_wick_lo"},
              entry={"type": kind, "offset": 0.5, "expiry_bars": 5})
    for generate in (generate_pine, generate_mql5):
        with pytest.raises(ValueError, match="only known at the fill"):
            generate(sp)
    with pytest.raises(ForgeUnsupported, match="only known at the fill"):
        forge_spec(sp)
