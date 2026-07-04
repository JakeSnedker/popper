# DSL v0.3 — Exit Vocabulary & Layered Falsification (PROPOSAL)

**Status:** ✅ **LOCKED 2026-07-04** (red-penned by Jake). This is the contract
all three implementations (Popper compiler, Forge Pine, Forge MQL5) read. Build
order at the end of the doc.

---

## 0. Why this exists

Two problems with v0.2: (1) an entry signal was tested against **one** arbitrary
exit (`2×ATR / 2R`), producing false-negatives on exit-sensitive alphas; (2) the
exit vocabulary was a single SL method + a single TP method. v0.3 makes the exit
a first-class, searched part of the alpha, and adds a cheap signal-edge pre-filter
so the richer search stays affordable.

---

## 1. The three-layer model

**predict → harvest → prove.** Each layer answers a different question and only
survivors pay the next layer's cost.

| Layer | Question | Exit used | Cost |
|-------|----------|-----------|------|
| **1 — Signal edge** | Does the entry *predict* anything? | time horizon only (neutral) | cheap — a few runs |
| **2 — Strategy edge** | Can it be *harvested* as P&L, and how? | the SL × TP menu | expensive — the menu |
| **3 — Robustness** | Is it *real* or overfit? | (same runs) | multiple-testing correction |

---

## 2. The mandatory-exit law (hard invariant)

> **No alpha is ever tested naked.** Every strategy spec must resolve to a stop
> **and** a target. Time is the irreducible backstop (Layer 1 *is* a pure time
> exit); Layer 2+ additionally require an SL and a TP. A spec with neither is
> rejected at validation.

This is the base law: the signal is not a strategy — `signal × SL-method ×
TP-method` is.

---

## 3. Layer 1 — the signal-edge gate (new, runs first)

Purpose: kill signals with no predictive content **before** paying for the exit
menu. Exit-neutral, so it imposes no strategy bias.

- **Horizons:** `{3, 10, 20, 60}` bars (M5: ~15m / 50m / ~1.5h / 5h — scalp→swing).
- **Measure per horizon:** mean forward return conditional on the signal (and
  optionally the Information Coefficient — signal-strength vs forward return). ⟨A⟩
- **Pass rule:** forward return has a **consistent favourable sign across the
  horizons** with a minimum t-stat (⟨A⟩ `t ≥ 2` on the majority of horizons). A
  real edge shows the same sign decaying across horizons; a fluke flips sign.
- **Verdict:** hard KILL if it fails — no predictive signal, no point harvesting.

*Note:* the horizon `N` is itself a choice, so we test several and require
consistency — the same "survive a neighbourhood" principle used everywhere else.

---

## 4. Layer 2 — the exit vocabulary

### 4a. Level-features (wave 1)

Point-in-time **price-level series** — computed from **prior bars only** (shift 1),
so no look-ahead. Both stops and targets reference them. Each needs a Pine + MQL5
twin (§10).

| Level-feature | Computes (as of bar i, using ≤ i-1) | Side |
|---------------|--------------------------------------|------|
| `atr_distance` | `mult × ATR(14)` from entry | either (risk-relative) |
| `prior_fractal_hi` / `_lo` | last confirmed up/down fractal (Bill Williams, needs 2 bars right → lag) | hi=TP-long/SL-short, lo=SL-long/TP-short |
| `prior_swing_wick_hi` / `_lo` | rolling max(`high`) / min(`low`) over N prior bars — **N=20 (placeholder, see below)** | as above |
| `prior_swing_body_hi` / `_lo` | rolling max(`max(open,close)`) / min(`min(open,close)`) over N | as above |
| `session_hi` / `_lo` | high / low of the prior completed session (Asia/Ldn/NY) | as above |
| `bb_line` | Bollinger mid = SMA(20) (already exists) | either |
| `stdev_band` | `close ± k × rolling stdev(N)` — **deferred to wave 2** (not in first-pass) | ± side |

**On the swing lookback `N` (Jake):** `N` is itself strategy-defining — JJC tested
three (most-recent structure ≈ native, ~15m-equivalent, ~4h-equivalent). We do
**not** bake that in now. `N=20` is a deliberate placeholder for the first pass;
**wave 2** promotes the structural lookback to a swept multi-timeframe axis
(recent / ~15m / ~4h) so the falsifier can find which structural horizon an alpha
keys off — without tripling the first-pass grid today.

`± offset` (spread/points) is applied to any level anchor (e.g. fractal − spread
for a long stop just under structure).

### 4b. Stop methods

- **`atr`** — distance = `mult × ATR(14)`. *(v0.2 behaviour, kept.)*
- **`level`** — anchor = a **loss-side** level-feature, `± offset`. Long stops sit
  below (swing_lo / fractal_lo / session_lo / bb_line); shorts mirror. The
  compiler enforces "SL on the loss side of entry."

### 4c. Target methods — **two families** (the RR resolution)

- **`ratio`** (R-multiple) — `TP = entry + ratio × risk`. **RR is a swept
  parameter** → the family that ranges `0.5R … 10R`. Rides on whatever SL is
  paired (risk = entry − stop, structural or ATR).
- **`level`** (structural) — anchor = a **profit-side** level-feature, `± offset`.
  **RR is emergent** — set by where the structure sits, recorded per trade, not
  swept. Adaptive profile.

Testing both families is deliberate: it lets the falsifier *discover* whether an
alpha wants a **fixed** RR profile (ratio) or an **adaptive/structural** one (level).

### 4d. Trail / break-even — **wave 2 (refinement only)**

Not in first-pass. Added to survivors during refinement: `trail` families
(ATR/structural/PSAR-chandelier) and `break_even` triggers. (v0.2 already has
`trail: none|atr`, `break_even: none|after_r` — extended later.)

---

## 5. The first-pass menu (Layer 2 entry)

Curated, JJC-foundation-sized. Runs **only on Layer-1 survivors**.

- **SL (3):** `atr(mult=1.5)` · `level(prior_swing_wick_lo)` · `level(prior_fractal_lo, −spread)`
- **TP — ratio family (7 RR):** `{0.5, 1, 2, 3, 5, 8, 10}` R
- **TP — structural family (2):** `level(prior_swing_wick_hi)` · `level(session_hi)`  *(stdev_band deferred)*
- **Cross:** 3 SL × (7 + 2) TP = **27 combos** first-pass (< the 48 cap).
- **Default vs override:** the menu is the default; an alpha **may** declare its
  own exit thesis and skip the menu when its mechanism demands it (e.g. FVG-fill
  → TP at the gap). *(Decision 4.)*

*(For long examples above; short specs use the mirrored sides.)*

---

## 6. Layer 3 — multiple-testing keeps it honest

Every exit config in the menu is a **trial**. The count (≈30 here, more with mult
sweeps) feeds `deflated_sharpe(n_trials=…)` and PBO. So an alpha that only works
with **one exact exit** out of the menu dies as a knife-edge — exactly the
overfit we want killed. This is why the RR sweep is **coarse**: more points = a
higher deflation bar for no real information.

---

## 7. Staged search flow (Decision 5)

```
Layer 1  signal-edge gate (time horizons)      → KILL most junk cheaply
   │  survivors only
Layer 2  first-pass menu (30 combos)           → find harvestable form + regime
   │  survivors only
Refine   sweep the winner's mults, add trail/BE, tighten RR around its regime
   │
Layer 3  DSR/PBO across everything tried        → prove it's not overfit
```

Mirrors JJC's staged optimiser: cheap gates first, expensive search only on what
earns it, stop when no edge.

---

## 8. Concrete schema (what changes in `strategy_spec.py`)

```python
LEVEL_FEATURES = {
    "atr_distance", "prior_fractal_hi", "prior_fractal_lo",
    "prior_swing_wick_hi", "prior_swing_wick_lo",
    "prior_swing_body_hi", "prior_swing_body_lo",
    "session_hi", "session_lo", "bb_line", "stdev_band",
}

class StopRule(BaseModel):
    type: Literal["atr", "level"] = "atr"
    mult: Param = 2.0                 # type=atr
    anchor: str | None = None         # type=level → a loss-side LEVEL_FEATURE
    offset: Param = 0.0               # ± points/spread

class TargetRule(BaseModel):
    type: Literal["ratio", "level"] = "ratio"
    ratio: Param = 2.0                # type=ratio → the RR sweep family
    anchor: str | None = None         # type=level → a profit-side LEVEL_FEATURE
    offset: Param = 0.0
```

`param_space` gains the ability to sweep a **categorical** dimension (the method
/ anchor), not just numeric grids — needed so one hypothesis can carry the menu.
⟨A⟩ *(implementation: a reserved `exit_menu` field or categorical param entries —
settle at build time.)*

---

## 9. Causality (the non-negotiable)

Every level-feature is computed with `.shift(1)` / prior-bar-only windows so bar
`i` can never see bar `i`'s own extreme or the future. The existing
`tests/test_dsl.py` prefix-invariance check is **extended to cover every new
level-feature** — a feature that changes a past value when future bars are added
fails the build. This is also what keeps Gate 0 (leakage) meaningful.

---

## 10. Codegen contract (Strategy Forge)

Because Forge is unified on this DSL, every level-feature + method added here owes
a **Pine twin and an MQL5 twin** that reproduce the Python math bar-for-bar.
Starter mapping (to be filled during build):

| DSL | Pine | MQL5 |
|-----|------|------|
| `prior_swing_wick_hi (N)` | `ta.highest(high, N)[1]` | `iHighest(...,MODE_HIGH,N,1)` |
| `session_hi (NY)` | session-scoped `ta.highest` | prior-session loop |
| `ratio TP (r)` | `entry + r*risk` | `entry + r*risk` |
| … | … | … |

---

## 11. Deferred to wave 2

Fair-value-gap edges · fib extensions anchored to a specific engulfing/MES candle
body · fib retracement stops · trailing families (ATR/structural/PSAR/chandelier)
· break-even variants · cross/price-action exits (exit-in-profit and exit-in-loss
on top of SL/TP). All need reliable, 3-language-identical pattern detection —
frame first.

---

## 12. Red-pen resolutions (LOCKED)

- Layer-1 horizons `{3,10,20,60}`, pass = consistent favourable sign with `t≥2` on the majority. ✅
- Swing lookback **N=20 placeholder**; multi-timeframe lookback → wave 2 (§4a note). ✅
- First-pass SL = `atr(1.5)` · `swing_wick_lo` · `fractal_lo −spread`. ✅
- First-pass structural TP = `swing_wick_hi` · `session_hi`; **stdev_band deferred**. ✅
- RR grid `{0.5,1,2,3,5,8,10}`. ✅
- Fractal = **Bill Williams 5-bar** (2 bars each side; confirms with a right-lag — keeps it causal). ✅
- Categorical-param mechanism (`exit_menu` vs categorical `param_space`) — settled at build time (implementation detail). ⏳

**Build order:** schema → level-features (pure, causality-tested) → compiler wiring
→ validator (mandatory-exit + side rules) → Layer-1 gate → staged menu → Forge twins.
