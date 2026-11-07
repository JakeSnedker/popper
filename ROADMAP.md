# Roadmap

Popper's goal is real numbers on real data. Every item below moves a number in this funnel:

```
VOLUME  ->  PASS RATE  ->  WRAPPERS  ->  EXPORT  ->  LIVE
```

Nothing further right can be proven until the stage before it has data, so the work runs roughly
in this order. History of the completed build phases is in `docs/ROADMAP.md` and
`docs/SELF_LEARNING_ROADMAP.md`.

## Trust the engine

- [ ] Reconcile the Python backtester against real MT5 fills and close the fidelity gate
      (`scripts/fidelity_diff.py`, `mt5/FidelityEMACross.mq5`).
- [ ] Publish the fidelity result with its known differences, so every later number has a stated error bar.

## Volume and pass rate

- [ ] Measure unique evaluated hypotheses per provider and per hour, then raise the ceiling.
- [ ] Run a large explore and exploit campaign and report the true pass rate through the gauntlet.
- [ ] Add the deferred meta-learning bias to the hypothesis weighting.

## Wrappers

- [ ] Test each survivor jointly with its execution wrapper, not as a raw entry.
- [ ] Transfer good wrappers within behavioural categories and measure whether they hold.

## Export

- [ ] Finish Strategy Forge coverage so every DSL feature renders in Pine and MQL5.
- [ ] Add the EA Lab: run an already-built MQL5 EA through the same falsification gauntlet.

## Live

- [ ] Forward-test exported survivors on demo and record what actually happened.
- [ ] Compare live results with the backtest and feed the difference back into the gauntlet.

## Usability for other researchers

- [ ] Ship a small synthetic dataset so a fresh clone can run the whole loop with no broker data.
- [ ] Write a guide for building your own dataset.
- [ ] Add NAS100, DAX and gold data support through the same dataset format.
