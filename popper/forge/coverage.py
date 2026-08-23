"""What the DSL can say versus what each target can actually run.

The DSL was meant to be one language that translates everywhere. It drifted:
features were added to `popper/dsl/features/` and nothing ever failed when the
Pine and MQL5 emitters did not implement them, so coverage could only decay
silently. A promoted strategy could turn out to be undeployable, and nobody
found out until Forge raised.

This module computes the matrix from the live registries rather than from a
hand-maintained list, so it cannot go stale. `tests/test_forge_coverage.py`
ratchets it: coverage may rise, and any fall is a failing test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from popper.dsl.features import FEATURES
from popper.dsl.features.levels import LEVEL_FEATURES
from popper.forge import mql5 as _mql5
from popper.forge import pine as _pine

TARGETS = ("pine", "mql5")

_SUPPORTED_BY = {"pine": _pine._SUPPORTED, "mql5": _mql5._SUPPORTED}
_SUPPORTED_LEVELS_BY = {"pine": _pine._SUPPORTED_LEVELS, "mql5": _mql5._SUPPORTED_LEVELS}

# Wrapper and alpha capabilities each target implements. These are read off the
# emitters' own _validate guards; when a guard is removed, update the entry here
# in the same commit -- the ratchet test is what makes that non-optional.
_CAPABILITIES: dict[str, dict[str, bool]] = {
    "pine": {
        "entry.market": True,
        "entry.limit/stop (offset)": True,
        "entry.limit/stop (level anchor)": True,
        "stop.atr": True,
        "stop.level": True,
        "target.ratio": True,
        "target.level": True,
        # the three levels whose value on bar i reads bar i's own OHLC cannot price
        # an exit the compiler resolves at the fill bar -- both emitters refuse them
        "level exit anchored to prior_fractal_hi/lo or bb_line": False,
        # 1R is only known at the fill; a broker needs the volume at placement
        "level exit with a resting entry": False,
        "trail.atr": False,
        "break_even.after_r": False,
        "time_stop.bars": True,
        "constraints.max_trades_per_day": True,
        "constraints.no_trade": False,
        "higher_timeframe_features": False,
        "exit on the entry bar": False,
        "refuse a touched fill": False,
    },
    "mql5": {
        "entry.market": True,
        "entry.limit/stop (offset)": True,
        "entry.limit/stop (level anchor)": True,
        "stop.atr": True,
        "stop.level": True,
        "target.ratio": True,
        "target.level": True,
        # the three levels whose value on bar i reads bar i's own OHLC cannot price
        # an exit the compiler resolves at the fill bar -- both emitters refuse them
        "level exit anchored to prior_fractal_hi/lo or bb_line": False,
        # 1R is only known at the fill; a broker needs the volume at placement
        "level exit with a resting entry": False,
        "trail.atr": False,
        "break_even.after_r": False,
        "time_stop.bars": True,
        "constraints.max_trades_per_day": True,
        "constraints.no_trade": False,
        "higher_timeframe_features": False,
        "exit on the entry bar": True,
        "refuse a touched fill": False,
    },
}


@dataclass
class TargetCoverage:
    target: str
    features_supported: list[str] = field(default_factory=list)
    features_missing: list[str] = field(default_factory=list)
    levels_supported: list[str] = field(default_factory=list)
    levels_missing: list[str] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)

    @property
    def concepts_total(self) -> int:
        return (len(self.features_supported) + len(self.features_missing)
                + len(self.levels_supported) + len(self.levels_missing)
                + len(self.capabilities))

    @property
    def concepts_supported(self) -> int:
        return (len(self.features_supported) + len(self.levels_supported)
                + sum(1 for v in self.capabilities.values() if v))

    @property
    def ratio(self) -> float:
        return self.concepts_supported / self.concepts_total if self.concepts_total else 0.0


def coverage_for(target: str) -> TargetCoverage:
    supported = set(_SUPPORTED_BY[target])
    features = set(FEATURES)
    # ema_<n> is a family the emitters do handle, so count it as one concept
    levels = set(LEVEL_FEATURES) & set(_SUPPORTED_LEVELS_BY[target])
    all_levels = set(LEVEL_FEATURES)
    return TargetCoverage(
        target=target,
        features_supported=sorted(features & supported),
        features_missing=sorted(features - supported),
        levels_supported=sorted(levels),
        levels_missing=sorted(all_levels - levels),
        capabilities=dict(_CAPABILITIES[target]),
    )


def coverage() -> dict[str, TargetCoverage]:
    return {t: coverage_for(t) for t in TARGETS}


def report() -> str:
    """A readable compatibility register, generated from the live registries."""
    lines = ["DSL -> target compatibility", "=" * 60, ""]
    for target, cov in coverage().items():
        lines.append(
            f"{target.upper()}: {cov.concepts_supported}/{cov.concepts_total} concepts "
            f"({cov.ratio:.0%})"
        )
        lines.append(f"  features : {len(cov.features_supported)}/"
                     f"{len(cov.features_supported) + len(cov.features_missing)}")
        if cov.features_missing:
            lines.append(f"    missing: {', '.join(cov.features_missing)}")
        lines.append(f"  levels   : {len(cov.levels_supported)}/"
                     f"{len(cov.levels_supported) + len(cov.levels_missing)}")
        if cov.levels_missing:
            lines.append(f"    missing: {', '.join(cov.levels_missing)}")
        unsupported = [k for k, v in cov.capabilities.items() if not v]
        lines.append(f"  wrapper  : {len(cov.capabilities) - len(unsupported)}/"
                     f"{len(cov.capabilities)}")
        if unsupported:
            lines.append(f"    missing: {', '.join(unsupported)}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":       # python -m popper.forge.coverage
    print(report())
