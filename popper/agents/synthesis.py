"""Synthesis agent (spec/02): express a Hypothesis as a VALID DSL StrategySpec,
with a compiler-feedback REPAIR loop — generate -> validate_spec -> on error,
feed the error text back and retry (capped). Returns a spec that passes, or
raises after the cap. This is Phase 6's done-when: valid specs, unaided.
"""

import yaml

from popper.agents.llm import MODELS, strip_fence
from popper.artifacts import StrategySpec
from popper.dsl import DSLValidationError, validate_spec
from popper.dsl.features import FEATURES

SYSTEM = (
    "You translate a trading hypothesis into a Popper Strategy DSL spec in YAML. "
    "Schema: name; alpha{features:[...], signal:'<bool expr>', signal_short?}; "
    "wrapper{stop{type:atr,mult}, target{type:rr,ratio}, sizing{risk_pct}, ...}; "
    "context{universe:[US30], timeframe}; param_space{name:[values]}. "
    "Signals use ONLY declared features with AND/OR/comparisons. Numeric wrapper "
    "fields may be a literal number or a p[name] reference; every p[name] MUST "
    "appear in param_space and every param_space key MUST be referenced. "
    "Respond with ONLY the YAML."
)


def _prompt(h) -> str:
    return "\n".join([
        f"Hypothesis: {h.claim}",
        f"Mechanism: {h.mechanism}",
        f"Suggested features: {', '.join(h.required_features) or 'choose from the library'}",
        f"Feature library: {', '.join(FEATURES)} (plus ema_<n>).",
        "Write the StrategySpec YAML.",
    ])


def synthesize(hypothesis, llm, model: str = MODELS["synthesis"], max_repairs: int = 3) -> StrategySpec:
    """Hypothesis -> validated StrategySpec, repairing against validator feedback."""
    prompt = _prompt(hypothesis)
    last_err = "none"
    for attempt in range(max_repairs + 1):
        p = prompt if attempt == 0 else (
            f"{prompt}\n\nYour previous spec was INVALID:\n{last_err}\n"
            "Fix every listed problem and return corrected YAML only."
        )
        raw = llm.complete(SYSTEM, p, model)
        try:
            data = yaml.safe_load(strip_fence(raw)) or {}
            if not isinstance(data, dict):
                raise ValueError("spec must be a YAML mapping")
            data["hypothesis_id"] = hypothesis.id or ""
            data.setdefault("name", f"synth_{hypothesis.id or 'spec'}")
            return validate_spec(data)  # dict -> validated StrategySpec (raises DSLValidationError)
        except DSLValidationError as e:
            last_err = "\n".join(e.errors)
        except Exception as e:  # malformed YAML / bad shape -> feed back too
            last_err = str(e)
    raise DSLValidationError([f"synthesis failed after {max_repairs} repairs; last error:\n{last_err}"])
