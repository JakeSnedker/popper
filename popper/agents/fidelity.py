"""Hypothesis-to-spec fidelity gates.

The DSL validator proves executable safety. These checks prove the generated DSL
still represents the claim it was meant to test. No performance data is used.
"""

import ast
import json

from popper.agents.llm import MODELS, strip_fence
from popper.data.timeframes import higher_feature_alias
from popper.dsl import DSLValidationError
from popper.dsl.expr import BAR_COLS, parse_signal
from popper.dsl.features import resolve_feature


def _signal_names(spec) -> set[str]:
    names = set()
    for signal in (spec.alpha.signal, spec.alpha.signal_short):
        if not signal:
            continue
        tree = parse_signal(signal)
        names.update(
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id != "p" and node.id not in BAR_COLS
        )
    return names


def hypothesis_feature_errors(hypothesis) -> list[str]:
    """Reject unsupported primitives before spending a Synthesis call."""
    return [
        f"unknown feature: {feature!r} required by hypothesis"
        for feature in sorted(set(hypothesis.required_features))
        if resolve_feature(feature) is None
    ]


def fidelity_errors(hypothesis, spec, mode: str = "strict") -> list[str]:
    """Return deterministic claim/spec contract errors without judging the alpha."""
    if mode == "off":
        return []
    required = set(hypothesis.required_features)
    unsupported = sorted(f for f in required if resolve_feature(f) is None)
    errors = hypothesis_feature_errors(hypothesis)
    supported = required - set(unsupported)
    bindings = {feature: {feature} for feature in spec.alpha.features}
    for timeframe, features in spec.alpha.higher_timeframe_features.items():
        for feature in features:
            bindings.setdefault(feature, set()).add(higher_feature_alias(timeframe, feature))
    missing = sorted(supported - set(bindings))
    if missing:
        errors.append(f"hypothesis required_features missing from alpha feature bindings: {missing}")
    if mode == "strict":
        used = _signal_names(spec)
        unused = sorted(
            feature for feature in supported
            if feature not in bindings or not (bindings[feature] & used)
        )
        if unused:
            errors.append(f"hypothesis required_features not used by either signal: {unused}")
    return errors


def validate_fidelity(hypothesis, spec, mode: str = "strict"):
    errors = fidelity_errors(hypothesis, spec, mode=mode)
    if errors:
        raise DSLValidationError(errors)
    return spec


_AUDIT_SYSTEM = (
    "You audit whether a Popper Strategy DSL faithfully and directly tests its stated "
    "hypothesis. You never see performance. Do not judge profitability. Return only "
    "JSON: {\"faithful\": bool, \"reason\": str}."
)


def audit_fidelity(hypothesis, spec, llm) -> dict:
    """Rare best-model semantic audit for a statistical survivor."""
    prompt = json.dumps({
        "hypothesis": {
            "claim": hypothesis.claim,
            "mechanism": hypothesis.mechanism,
            "required_features": hypothesis.required_features,
        },
        "strategy_spec": spec.model_dump(mode="json", exclude={"id", "created_at"}),
    }, separators=(",", ":"))
    raw = llm.complete(_AUDIT_SYSTEM, prompt, MODELS["audit"], max_tokens=384)
    data = json.loads(strip_fence(raw))
    if not isinstance(data, dict) or not isinstance(data.get("faithful"), bool):
        raise ValueError("semantic audit returned an invalid shape")
    return {"faithful": data["faithful"], "reason": str(data.get("reason") or "")[:1000]}
