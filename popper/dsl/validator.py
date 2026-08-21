"""Spec validator — reject bad specs before any compute (spec/01).

Rejects: unknown features, undeclared features used in signals, malformed
signal expressions, look-ahead constructs (via expr's bounded grammar),
malformed/missing blocks (Pydantic), param refs not in param_space, and
param_space entries nothing references.

v0.3 (docs/DSL_v0.3_EXIT_SPEC.md §2/§4): also enforces the MANDATORY-EXIT LAW
(a `level` stop/target must name a valid anchor) and the SIDE RULE (stop
anchors are loss-side levels, target anchors profit-side; bb_line is 'either').
Anchors/offsets may be `p[...]` refs — grids are side-checked value-by-value.
"""

import re

from popper.artifacts.strategy_spec import StrategySpec
from popper.dsl.expr import check_signal
from popper.dsl.reachability import reachability_errors
from popper.dsl.features import resolve_feature
from popper.dsl.features.levels import LEVEL_FEATURES, LOSS_SIDE, PROFIT_SIDE
from popper.data.timeframes import higher_feature_alias, normalize_timeframe, timeframe_minutes

_PARAM_REF = re.compile(r"^p\[(\w+)\]$")

_EITHER = LEVEL_FEATURES - LOSS_SIDE - PROFIT_SIDE  # bb_line — usable both ways
_LOCAL_UNIVERSES = {"US30"}
_LOCAL_TIMEFRAMES = {"M1", "M5", "M15"}


class DSLValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("invalid spec:\n  - " + "\n  - ".join(errors))


def signal_feature_names(spec: StrategySpec) -> list[str]:
    names = list(spec.alpha.features)
    for timeframe, features in spec.alpha.higher_timeframe_features.items():
        try:
            names.extend(higher_feature_alias(timeframe, feature) for feature in features)
        except ValueError:
            continue  # _higher_timeframe_errors reports the invalid key cleanly
    return names


def _higher_timeframe_errors(spec: StrategySpec) -> list[str]:
    errors = []
    if not spec.alpha.higher_timeframe_features:
        return errors
    try:
        base_minutes = timeframe_minutes(spec.context.timeframe)
    except ValueError as exc:
        return [f"context.timeframe: {exc}"]
    normalized = set()
    for timeframe, features in spec.alpha.higher_timeframe_features.items():
        try:
            canonical = normalize_timeframe(timeframe)
            minutes = timeframe_minutes(timeframe)
        except ValueError as exc:
            errors.append(f"alpha.higher_timeframe_features[{timeframe!r}]: {exc}")
            continue
        if canonical in normalized:
            errors.append(f"duplicate higher timeframe after normalization: {canonical}")
        normalized.add(canonical)
        if minutes <= base_minutes or minutes % base_minutes:
            errors.append(
                f"higher timeframe {canonical} must be a strict integer multiple of "
                f"base timeframe {normalize_timeframe(spec.context.timeframe)}"
            )
        if not features:
            errors.append(f"alpha.higher_timeframe_features[{timeframe!r}] must not be empty")
        for feature in features:
            if resolve_feature(feature) is None:
                errors.append(f"unknown higher-timeframe feature: {feature!r}")
    return errors


def _wrapper_param_refs(spec: StrategySpec, errors: list[str]) -> set[str]:
    refs: set[str] = set()
    w = spec.wrapper
    # numeric fields depend on the exit type: atr uses mult, level uses offset
    fields = [
        ("wrapper.trail.mult", w.trail.mult),
        ("wrapper.break_even.trigger_r", w.break_even.trigger_r),
        ("wrapper.stop.mult", w.stop.mult) if w.stop.type == "atr"
        else ("wrapper.stop.offset", w.stop.offset),
        ("wrapper.target.ratio", w.target.ratio) if w.target.type == "ratio"
        else ("wrapper.target.offset", w.target.offset),
    ]
    if w.entry.type != "market" and not w.entry.anchor:
        fields.append(("wrapper.entry.offset", w.entry.offset))
    if w.time_stop.type == "bars":
        fields.append(("wrapper.time_stop.max_bars", w.time_stop.max_bars))
    for path, value in fields:
        if isinstance(value, str):
            m = _PARAM_REF.match(value)
            if m:
                refs.add(m.group(1))
            else:
                errors.append(f"{path}: {value!r} is not a number or p[name] reference")

    # v0.3: mandatory-exit law + side rule for level anchors
    for path, rule, allowed, side in [
        ("wrapper.stop", w.stop, LOSS_SIDE | _EITHER, "loss-side"),
        ("wrapper.target", w.target, PROFIT_SIDE | _EITHER, "profit-side"),
    ]:
        if rule.type != "level":
            continue
        if not rule.anchor:
            errors.append(f"{path}: type 'level' must name an anchor (mandatory-exit law)")
            continue
        m = _PARAM_REF.match(rule.anchor)
        if m:  # categorical anchor sweep: side-check every grid value
            refs.add(m.group(1))
            candidates = spec.param_space.get(m.group(1), [])
        else:
            candidates = [rule.anchor]
        for a in candidates:
            if a not in LEVEL_FEATURES:
                errors.append(f"{path}.anchor: unknown level-feature {a!r}")
            elif a not in allowed:
                errors.append(
                    f"{path}.anchor: {a!r} is not a {side} level "
                    f"(side rule: a {'stop' if side == 'loss-side' else 'target'} "
                    f"cannot anchor a {'profit' if side == 'loss-side' else 'loss'}-side level)"
                )
    # entry orders: a resting order needs a price and a finite lifetime
    if w.entry.type != "market":
        if w.entry.anchor:
            if w.entry.anchor not in LEVEL_FEATURES:
                errors.append(f"wrapper.entry.anchor: unknown level-feature {w.entry.anchor!r}")
        elif isinstance(w.entry.offset, (int, float)) and w.entry.offset <= 0:
            errors.append(
                f"wrapper.entry.offset must be > 0 for a {w.entry.type} order: an offset of 0 "
                f"rests the order at the signal close, which is just a market entry"
            )
        if w.entry.expiry_bars < 1:
            errors.append(
                "wrapper.entry.expiry_bars must be at least 1; an order that never expires can "
                "fill arbitrarily far from the conditions that justified it"
            )
    elif w.entry.anchor:
        errors.append("wrapper.entry.anchor is set but wrapper.entry.type is 'market'")

    if w.time_stop.type == "bars" and isinstance(w.time_stop.max_bars, (int, float)):
        if w.time_stop.max_bars < 1:
            errors.append("wrapper.time_stop.max_bars must be at least 1")

    return refs


def _direction_errors(spec: StrategySpec) -> list[str]:
    """Direction must be declared and must match the signals actually written.

    Asymmetry is allowed but never free: a one-sided alpha has to argue for
    itself. Left optional, "no short signal" is the path of least resistance
    and the corpus silently fills with long-only strategies.
    """
    errors: list[str] = []
    direction = spec.alpha.direction
    has_short = bool((spec.alpha.signal_short or "").strip())
    has_long = bool((spec.alpha.signal or "").strip())

    if direction == "both":
        if not has_short:
            errors.append(
                "alpha.direction is 'both' but alpha.signal_short is missing. "
                "Provide a short signal, or declare direction: long_only with a "
                "direction_rationale explaining why the edge is one-sided."
            )
    else:
        if not (spec.alpha.direction_rationale or "").strip():
            errors.append(
                f"alpha.direction is {direction!r} and therefore requires "
                "alpha.direction_rationale: why does this edge only exist on one side?"
            )
        if direction == "long_only" and has_short:
            errors.append("alpha.direction is 'long_only' but alpha.signal_short is set")
        if direction == "short_only" and has_long and has_short:
            errors.append(
                "alpha.direction is 'short_only': put the short condition in "
                "alpha.signal and leave alpha.signal_short unset"
            )
    return errors


def validate_spec(spec: StrategySpec | dict, strict: bool = False) -> StrategySpec:
    """Returns the validated spec; raises DSLValidationError listing every problem.

    `strict` holds a spec to the full v0.4 contract: direction must be declared,
    and no signal may be provably impossible. It is OFF by default so the pre-0.4
    corpus and hand-written specs still validate, and ON for freshly synthesised
    specs -- the agent is what produced both defects.
    """
    if isinstance(spec, dict):
        try:
            spec = StrategySpec.model_validate(spec)
        except Exception as e:  # pydantic ValidationError -> our error type
            raise DSLValidationError([str(e)]) from e

    errors: list[str] = []

    # features exist in the library and higher contexts are strictly coarser
    for f in spec.alpha.features:
        if resolve_feature(f) is None:
            errors.append(f"unknown feature: {f!r}")
    errors += _higher_timeframe_errors(spec)
    if strict:
        errors += _direction_errors(spec)
        errors += reachability_errors(spec.alpha.signal or "", spec.param_space, "alpha.signal")
        errors += reachability_errors(
            spec.alpha.signal_short or "", spec.param_space, "alpha.signal_short"
        )

    # signals parse, use only declared names, and stay inside the bounded grammar
    used_params: set[str] = set()
    for label, text in [("signal", spec.alpha.signal), ("signal_short", spec.alpha.signal_short)]:
        if text is None:
            continue
        if not text.strip():
            errors.append(f"alpha.{label} is empty")
            continue
        params, errs = check_signal(text, signal_feature_names(spec))
        used_params |= params
        errors += [f"alpha.{label}: {e}" for e in errs]

    used_params |= _wrapper_param_refs(spec, errors)

    # param refs resolve; param_space keys are all used; grids are non-empty lists
    for p in sorted(used_params - set(spec.param_space)):
        errors.append(f"p[{p}] referenced but not in param_space")
    for k, grid in spec.param_space.items():
        if k not in used_params:
            errors.append(f"param_space key {k!r} is not referenced by any signal or wrapper field")
        if not isinstance(grid, list) or not grid:
            errors.append(f"param_space[{k!r}] must be a non-empty list")

    if errors:
        raise DSLValidationError(errors)
    return spec


def validate_local_data_context(spec: StrategySpec) -> StrategySpec:
    """Reject specs the current local research runner cannot load.

    Kept separate from validate_spec() because the DSL itself is portable and
    tests can inject synthetic data at other timeframes. The real local runner
    currently has US30 M1/M5 parquet only, so agents must synthesize against 5m.
    """
    errors: list[str] = []
    unsupported_universe = [u for u in spec.context.universe if u not in _LOCAL_UNIVERSES]
    if unsupported_universe:
        errors.append(
            f"context.universe contains unsupported symbols {unsupported_universe}; "
            f"available local data: {sorted(_LOCAL_UNIVERSES)}"
        )
    try:
        base_timeframe = normalize_timeframe(spec.context.timeframe)
    except ValueError:
        base_timeframe = spec.context.timeframe
    if base_timeframe not in _LOCAL_TIMEFRAMES:
        errors.append(
            f"context.timeframe {spec.context.timeframe!r} has no local data; "
            f"available canonical timeframes: {sorted(_LOCAL_TIMEFRAMES)}"
        )
    for timeframe in spec.alpha.higher_timeframe_features:
        try:
            canonical = normalize_timeframe(timeframe)
        except ValueError:
            continue
        if canonical not in _LOCAL_TIMEFRAMES:
            errors.append(
                f"higher timeframe {timeframe!r} has no local data; "
                f"available canonical timeframes: {sorted(_LOCAL_TIMEFRAMES)}"
            )
    if errors:
        raise DSLValidationError(errors)
    return spec
