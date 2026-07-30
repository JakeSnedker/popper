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
    return refs


def validate_spec(spec: StrategySpec | dict) -> StrategySpec:
    """Returns the validated spec; raises DSLValidationError listing every problem."""
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
