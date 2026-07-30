"""Compile a hypothesis into valid, faithful Popper Strategy DSL."""

import yaml

from popper.agents.fidelity import hypothesis_feature_errors, validate_fidelity
from popper.agents.llm import LLMProviderError, MODELS, strip_fence
from popper.artifacts import StrategySpec
from popper.config import settings
from popper.dsl import DSLValidationError, validate_spec
from popper.dsl.features import FEATURES
from popper.dsl.validator import validate_local_data_context

SYSTEM = (
    "Translate one trading hypothesis into Popper Strategy DSL YAML. Schema: name; "
    "alpha{features,higher_timeframe_features?,signal,signal_short?}; "
    "wrapper{stop,target,trail,break_even,sizing}; "
    "context{universe:[US30],timeframe:1m|5m}; param_space. Use only the supplied feature "
    "library and bounded AND/OR/NOT arithmetic/comparison expressions. Every hypothesis "
    "required_feature must be declared and used by signal or signal_short. Every p[name] "
    "must have a non-empty param_space grid and every grid must be referenced. Grid keys "
    "are bare names, NEVER p[name]. Example: signal: 'atr_14 > p[atr_min]' with "
    "param_space: {atr_min: [5, 10, 15]}. Wrapper enums are stop atr|level, target "
    "ratio|level, trail none|atr, break_even none|after_r. Never invent other types. "
    "Higher features use aliases like htf_m15_ema_50 in signals. A higher timeframe "
    "must be strictly coarser than the base; only completed higher bars are exposed. "
    "Use one base timeframe and optional M5/M15 confluence inside the same hypothesis; "
    "never clone one hypothesis across timeframes. Return YAML only."
)


def _prompt(hypothesis) -> str:
    return "\n".join([
        f"Hypothesis: {hypothesis.claim}",
        f"Mechanism: {hypothesis.mechanism}",
        f"Mandatory signal features: {', '.join(hypothesis.required_features) or 'choose minimally'}",
        f"Feature library: {', '.join(FEATURES)}; ema_<n> is allowed.",
        "Valid shape example:",
        "name: probe",
        "alpha:",
        "  features: [atr_14]",
        "  higher_timeframe_features: {15m: [ema_50]}",
        "  signal: 'atr_14 > p[atr_min] AND htf_m15_ema_50 > close'",
        "wrapper:",
        "  stop: {type: atr, mult: 'p[sl_atr]'}",
        "  target: {type: ratio, ratio: 'p[rr]'}",
        "  trail: {type: none}",
        "  break_even: {type: none}",
        "  sizing: {risk_pct: 0.5}",
        "context: {universe: [US30], timeframe: 5m}",
        "param_space: {atr_min: [5, 10, 15], sl_atr: [1.0, 1.5, 2.0], rr: [1.0, 1.5, 2.0]}",
        "Keep the signal minimal and directly falsifiable. Write the complete StrategySpec YAML.",
    ])


def synthesize(
    hypothesis,
    llm,
    model: str = MODELS["synthesis"],
    max_repairs: int = 1,
    enforce_local_context: bool = True,
    fidelity_mode: str | None = None,
) -> StrategySpec:
    """Cheap first pass, then one stronger-model repair against exact errors."""
    unsupported = hypothesis_feature_errors(hypothesis)
    if unsupported:
        raise DSLValidationError(unsupported)
    prompt = _prompt(hypothesis)
    last_err = "none"
    mode = fidelity_mode or settings.llm_fidelity_mode
    for attempt in range(max_repairs + 1):
        role = model if attempt == 0 else MODELS["synthesis_strong"]
        request = prompt if attempt == 0 else (
            f"{prompt}\n\nThe prior spec was INVALID:\n{last_err}\n"
            "Fix every listed issue and return one complete corrected YAML document."
        )
        raw = ""
        try:
            raw = llm.complete(SYSTEM, request, role, max_tokens=1400)
            data = yaml.safe_load(strip_fence(raw)) or {}
            # Subscription CLIs use a structured {yaml: "..."} envelope.
            if isinstance(data, dict) and set(data) == {"yaml"} and isinstance(data["yaml"], str):
                data = yaml.safe_load(data["yaml"]) or {}
            if not isinstance(data, dict):
                raise ValueError("spec must be a YAML mapping")
            data["hypothesis_id"] = hypothesis.id or ""
            data.setdefault("name", f"synth_{hypothesis.id or 'spec'}")
            spec = validate_spec(data)
            if enforce_local_context:
                spec = validate_local_data_context(spec)
            return validate_fidelity(hypothesis, spec, mode=mode)
        except LLMProviderError:
            raise
        except DSLValidationError as e:
            last_err = "\n".join(e.errors)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}; raw={raw[:500]}"
    raise DSLValidationError([
        f"synthesis failed after {max_repairs + 1} content attempts; last error:\n{last_err}"
    ])
