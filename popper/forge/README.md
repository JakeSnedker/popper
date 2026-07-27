# Strategy Forge

Forge compiles a promoted `StrategySpec` into downloadable TradingView Pine v5 and MQL5 Expert Advisor source.

The generated files are deterministic and fail closed when a DSL feature or wrapper is not implemented for both targets. They are deployment candidates, not proof of parity. Every export must be compared with the same Popper period and parameters before live or paper trading.

Current target coverage includes both promoted Popper strategies: ATR/EMA/session momentum and the CUSUM/regime/relative-volume strategy. Structural level exits, trailing stops, break-even rules, and `no_trade` constraints remain blocked rather than approximated silently.
