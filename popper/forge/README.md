# Strategy Forge

Forge compiles a promoted `StrategySpec` into downloadable TradingView Pine v5 and MQL5 Expert Advisor source.

The generated files are deterministic and fail closed when a DSL feature or wrapper is not implemented for both targets. They are deployment candidates, not proof of parity. Every export must be compared with the same Popper period and parameters before live or paper trading.

Current target coverage includes both promoted Popper strategies: ATR/EMA/session momentum and the CUSUM/regime/relative-volume strategy. Run `python -m popper.forge.coverage` for the live matrix.

All thirteen DSL v0.3 level-features render in both targets. `tests/test_forge_levels.py` transliterates the emitted Pine and MQL5 back into Python and asserts bar-for-bar equality with `popper.dsl.features.levels.compute_level` on real US30 M5 bars — string checks alone cannot prove an emitter causal.

A level anchor may price a resting entry (all thirteen), a stop, or a target. Three combinations stay blocked rather than approximated silently:

- **`prior_fractal_hi/lo` and `bb_line` as a stop/target anchor.** `popper/dsl/compiler.py` resolves an exit anchor at the FILL bar and documents the invariant "level[i] uses bars <= i-1, all complete at bar i's open". These three break it — a fractal's confirmation reads bar i's own high/low and `bb_line` averages in bar i's close — so no live target can read that value when the trade opens. **This is a look-ahead in the backtest, not only a Forge gap.**
- **A level exit combined with a resting entry.** 1R is measured from the fill bar's level, and a broker needs the order volume at placement.
- Trailing stops, break-even rules, and `no_trade` constraints.
