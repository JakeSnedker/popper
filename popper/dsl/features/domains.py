"""What each feature's values actually ARE — range, unit and meaning.

Every feature name used to reach the synthesis agent as a bare string, so the
agent guessed semantics from the name and guessed wrong. Measured against
US30 M5 (2025-01-01..2025-06-30), the corpus contained conditions like
`bb_line < 0.2` (bb_line is a PRICE near 40,000), `psar < 1` (also a price),
`trend_regime < 0` (it is 0 or 1), `atr_14 > ema_50` (points vs price) and
`regime_score >= 50` (it is bounded to [-1, 1]).

Roughly a third of every hypothesis produced a signal that fired zero times.

This table exists to be read by two consumers:
  * the synthesis prompt, so the agent knows what it is comparing, and
  * `validator._reachability_errors`, so an impossible comparison is rejected
    before anything expensive runs.

`lo`/`hi` are OBSERVED bounds, not guarantees — they are wide enough to be safe
for "can this comparison ever be true" reasoning, which is the only question
asked of them.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kinds that carry a small, enumerable value set. Anything else is continuous.
BINARY = "binary"        # exactly {0, 1}
TRIT = "trit"            # exactly {-1, 0, 1}
SIGN = "sign"            # exactly {-1, 1} -- never zero
UNIT = "unit"            # continuous on [-1, 1]
PRICE = "price"          # an instrument PRICE level (tens of thousands on US30)
POINTS = "points"        # an absolute price DISTANCE in points
RATIO = "ratio"          # a non-negative multiple, ~1.0 means "typical"
PERCENT = "percent"      # 0..100
UNBOUNDED = "unbounded"  # signed, no useful bound

DISCRETE_VALUES = {
    BINARY: (0.0, 1.0),
    TRIT: (-1.0, 0.0, 1.0),
    SIGN: (-1.0, 1.0),
}


@dataclass(frozen=True)
class Domain:
    kind: str
    lo: float
    hi: float
    note: str

    @property
    def values(self) -> tuple[float, ...] | None:
        """The exact value set, when the feature has one."""
        return DISCRETE_VALUES.get(self.kind)


# Bounds are observed on US30 M5 over six months, then widened where the
# feature is structurally unbounded. PRICE bounds are deliberately generous:
# the only claim being made is "this is an instrument price, not a small
# number", which is what catches `bb_line < 0.2`.
DOMAINS: dict[str, Domain] = {
    # --- exact value sets -------------------------------------------------
    "session_ny":      Domain(BINARY, 0, 1, "1 during the NY session (13-21 UTC), else 0"),
    "session_london":  Domain(BINARY, 0, 1, "1 during London (07-13 UTC), else 0"),
    "session_asia":    Domain(BINARY, 0, 1, "1 during Asia (00-07 UTC), else 0"),
    "trend_regime":    Domain(BINARY, 0, 1, "1 = trending, 0 = ranging. NEVER negative"),
    "htf_trend":       Domain(SIGN, -1, 1, "+1 up, -1 down. Never 0"),
    "vol_regime":      Domain(TRIT, -1, 1, "-1 low vol, 0 normal, +1 high vol"),
    "bos":             Domain(TRIT, -1, 1, "+1 bullish break of structure, -1 bearish, 0 none"),
    "mss":             Domain(TRIT, -1, 1, "+1 bullish market structure shift, -1 bearish, 0 none"),
    "cisd":            Domain(TRIT, -1, 1, "+1 bullish change in state of delivery, -1 bearish, 0 none"),
    "fvg":             Domain(TRIT, -1, 1, "+1 bullish fair value gap, -1 bearish, 0 none"),
    "liquidity_sweep": Domain(TRIT, -1, 1, "+1 sweep of lows then close back up, -1 mirror, 0 none"),

    # --- bounded continuous ----------------------------------------------
    "body_pressure":   Domain(UNIT, -1, 1, "(close-open)/(high-low). +1 closed at the high"),
    "regime_score":    Domain(UNIT, -1, 1, "composite regime score. NOT a 0-100 score"),
    "rsi_fast":        Domain(PERCENT, 0, 100, "fast RSI, 0-100"),

    # --- PRICE LEVELS: comparable only to price, never to a small number --
    "bb_line":         Domain(PRICE, 1_000, 1_000_000, "a Bollinger PRICE level, not a width"),
    "equilibrium":     Domain(PRICE, 1_000, 1_000_000, "midpoint PRICE of the swing range. Always positive"),
    "psar":            Domain(PRICE, 1_000, 1_000_000, "parabolic SAR PRICE level, not a flag"),
    "ob_bull_mid":     Domain(PRICE, 1_000, 1_000_000, "bullish order block midpoint PRICE"),
    "ob_bear_mid":     Domain(PRICE, 1_000, 1_000_000, "bearish order block midpoint PRICE"),

    # --- absolute distances in points -------------------------------------
    "atr_14":          Domain(POINTS, 0, 100_000, "ATR(14) in POINTS. Compare to other distances, never to price"),
    "candle_size":     Domain(POINTS, 0, 100_000, "bar range in POINTS"),
    "opening_range":   Domain(POINTS, 0, 100_000, "session opening range width in POINTS"),

    # --- ratios and unbounded signed --------------------------------------
    "rel_volume":      Domain(RATIO, 0, 1_000, "this bar's volume / recent mean. 1.0 = typical"),
    "delta_proxy":     Domain(UNBOUNDED, -1_000, 1_000, "sign(close-open) * rel_volume"),
    "cusum_shift":     Domain(UNBOUNDED, -1_000, 1_000, "CUSUM regime-break statistic, signed"),
    "fib_pos":         Domain(UNBOUNDED, -1_000, 1_000, "position within the swing. Overshoots outside 0-1 are normal"),
}

# Sessions partition the day, so ANDing two of them can never be true.
MUTUALLY_EXCLUSIVE = (("session_ny", "session_london", "session_asia"),)

PRICE_LIKE = {PRICE}
DISTANCE_LIKE = {POINTS}


def domain_for(name: str) -> Domain | None:
    """Domain for a feature name, resolving the parameterised families."""
    if name in DOMAINS:
        return DOMAINS[name]
    if name.startswith("ema_"):
        return Domain(PRICE, 1_000, 1_000_000, "an EMA PRICE level")
    base = name.rsplit("_", 1)[0]
    if base in DOMAINS and name.rsplit("_", 1)[-1].isdigit():
        return DOMAINS[base]          # bos_20 shares the bos domain
    return None


def prompt_table() -> str:
    """The domain table as compact text for the synthesis system prompt."""
    lines = []
    for name in sorted(DOMAINS):
        d = DOMAINS[name]
        if d.values:
            span = "{" + ",".join(str(int(v)) for v in d.values) + "}"
        elif d.kind == PRICE:
            span = "PRICE(~40000)"
        elif d.kind == POINTS:
            span = "POINTS(>=0)"
        elif d.kind == PERCENT:
            span = "0..100"
        elif d.kind == UNIT:
            span = "-1..1"
        else:
            span = "signed"
        lines.append(f"{name} {span} - {d.note}")
    lines.append("ema_<n> PRICE(~40000) - an EMA PRICE level")
    return "\n".join(lines)
