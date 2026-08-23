# Strategy Forge

Forge compiles a promoted `StrategySpec` into downloadable TradingView Pine v5 and MQL5 Expert Advisor source.

The generated files are deterministic and fail closed when a DSL feature or wrapper is not implemented for both targets. They are deployment candidates, not proof of parity. Every export must be compared with the same Popper period and parameters before live or paper trading.

Current target coverage includes both promoted Popper strategies: ATR/EMA/session momentum and the CUSUM/regime/relative-volume strategy. Run `python -m popper.forge.coverage` for the live matrix.

All thirteen DSL v0.3 level-features render in both targets. `tests/test_forge_levels.py` transliterates the emitted Pine and MQL5 back into Python and asserts bar-for-bar equality with `popper.dsl.features.levels.compute_level` on real US30 M5 bars — string checks alone cannot prove an emitter causal.

All thirteen level anchors now price a resting entry, a stop and a target. `prior_fractal_hi/lo` and `bb_line` read bar i's own OHLC, so `popper/dsl/compiler.py`'s `exit_anchor` shifts exactly those three (`INTRA_BAR_LEVELS`) one bar back when it resolves an EXIT; the emitters render the same shift (`level[1]` in Pine, shift 1 in MQL5) and leave entry anchors unshifted. Two combinations stay blocked rather than approximated silently:

- **A level exit combined with a resting entry.** 1R is measured from the fill bar's level, and a broker needs the order volume at placement.
- `no_trade` constraints.
