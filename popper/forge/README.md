# Strategy Forge

Forge compiles a promoted `StrategySpec` into downloadable TradingView Pine v5 and MQL5 Expert Advisor source.

The generated files are deterministic and fail closed when a DSL feature or wrapper is not implemented for both targets. They are deployment candidates, not proof of parity. Every export must be compared with the same Popper period and parameters before live or paper trading.

Every name in `popper.dsl.features.FEATURES` renders in both targets, including the `ema_<n>` and SMC (`bos_<n>`, `mss_<n>`, `equilibrium_<n>`, `ob_bull_mid_<n>`, `ob_bear_mid_<n>`, `cisd_<n>`) name families. Run `python -m popper.forge.coverage` for the live matrix.

Where a platform indicator does not match the Python it is not used. `ta.rsi`/`iRSI`, `ta.sar`/`iSAR`, `ta.atr`/`iATR` and `iMA`/`MODE_EMA` all seed Wilder/EMA smoothing on an SMA of the first n samples, where `popper/dsl/features/` seeds on the first sample; for `htf_trend` (span 240) that is a >1e-9 disagreement for ~2900 M5 bars, and for the order block's displacement test it arms different candles. Those features re-derive the recursion instead. `ema_<n>` itself still uses the native call — a pre-existing choice that predates this rule and cannot change without moving every emitted `ema` spec.

An EA has no dataset origin to seed a recursion from, so it re-walks a bounded window: `InpWarmupBars` (2000) for the smoothed recursions and `InpStructureBars` (1000) for the SMC state machines. Both are inputs, both are calibration knobs, and `tests/test_forge_features.py` measures the convergence that makes them safe.

All thirteen DSL v0.3 level-features render in both targets. `tests/test_forge_levels.py` transliterates the emitted Pine and MQL5 back into Python and asserts bar-for-bar equality with `popper.dsl.features.levels.compute_level` on real US30 M5 bars — string checks alone cannot prove an emitter causal.

All thirteen level anchors now price a resting entry, a stop and a target. `prior_fractal_hi/lo` and `bb_line` read bar i's own OHLC, so `popper/dsl/compiler.py`'s `exit_anchor` shifts exactly those three (`INTRA_BAR_LEVELS`) one bar back when it resolves an EXIT; the emitters render the same shift (`level[1]` in Pine, shift 1 in MQL5) and leave entry anchors unshifted. Two combinations stay blocked rather than approximated silently:

- **A level exit combined with a resting entry.** 1R is measured from the fill bar's level, and a broker needs the order volume at placement.
- `no_trade` constraints.
