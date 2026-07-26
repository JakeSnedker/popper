"""Performance-blind hypothesis generation, optimized for batched discovery."""

import json

from popper.agents.llm import LLMProviderError, MODELS, strip_fence
from popper.artifacts import Hypothesis, Regime
from popper.dsl.features import FEATURES

_REGIMES = {r.value for r in Regime}

SYSTEM = (
    "You are a quantitative researcher proposing FALSIFIABLE trading hypotheses "
    "for US30. Name a concrete market mechanism, not merely a chart pattern. "
    "Use only the supplied feature library. Every required_features item must be "
    "essential to and explicitly used by the eventual signal. Return only the "
    "requested JSON object; no prose."
)


def _map_context(view) -> dict:
    if hasattr(view, "map_context"):
        return view.map_context(limit=12)
    entries = view.hypotheses()
    coverage = view.coverage()
    return {
        "claims": [e.claim for e in entries[:12]],
        "coverage": coverage,
        "whitespace": view.whitespace(),
    }


def _prompt(view, wildcard: bool = False, count: int = 1) -> str:
    context = _map_context(view)
    claims = [str(c).replace("\n", " ")[:240] for c in context["claims"]]
    aim = (
        "Generate deliberately off-map mechanisms, including combinations not represented "
        "by the current four coarse edge labels."
        if wildcard else
        "Prioritize the sparsest categories and materially different mechanisms."
    )
    return "\n".join([
        f"Generate {count} distinct hypotheses as JSON: {{\"hypotheses\":[...]}}.",
        f"Coverage: {json.dumps(context['coverage'], separators=(',', ':'))}",
        f"Zero-coverage labels: {context['whitespace'] or 'none'}",
        "Recent claims to avoid:",
        *(f"- {claim}" for claim in claims),
        f"Allowed features: {', '.join(FEATURES)}; ema_<n> is also allowed.",
        aim,
        "Each item: {claim, mechanism, required_features, expected_regime}.",
        "Keep claim and mechanism under 300 characters each.",
    ])


def _parse_items(raw: str) -> list[Hypothesis]:
    data = json.loads(strip_fence(raw))
    if isinstance(data, dict) and isinstance(data.get("hypotheses"), list):
        data = data["hypotheses"]
    elif isinstance(data, dict):
        data = [data]  # backward-compatible with single-item model/test responses
    if not isinstance(data, list):
        raise ValueError("response must be a JSON array")
    out = []
    seen = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        mechanism = str(item.get("mechanism") or "").strip()
        features = item.get("required_features") or []
        if not claim or not mechanism or not isinstance(features, list):
            continue
        if len(claim) > 500 or len(mechanism) > 500:
            continue
        key = " ".join(claim.lower().split())
        if key in seen:
            continue
        seen.add(key)
        regime = item.get("expected_regime", "unknown")
        out.append(Hypothesis(
            claim=claim,
            mechanism=mechanism,
            required_features=list(dict.fromkeys(str(f) for f in features if str(f).strip())),
            expected_regime=Regime(regime) if regime in _REGIMES else Regime.UNKNOWN,
        ))
    if not out:
        raise ValueError("response contained no complete hypothesis")
    return out


def propose_batch(
    view,
    llm,
    count: int = 5,
    wildcard: bool = False,
    model: str | None = None,
    max_repairs: int = 1,
) -> list[Hypothesis]:
    """Generate several blind hypotheses per expensive creative-model call."""
    count = max(1, min(int(count), 10))
    role = model or (MODELS["wildcard"] if wildcard else MODELS["hypothesis"])
    prompt = _prompt(view, wildcard=wildcard, count=count)
    last_err = "none"
    for attempt in range(max_repairs + 1):
        call_role = role if attempt == 0 else MODELS["hypothesis_strong"]
        request = prompt if attempt == 0 else (
            f"{prompt}\n\nThe prior output was INVALID JSON: {last_err}\n"
            "Return one complete JSON object with a hypotheses array only."
        )
        raw = ""
        try:
            raw = llm.complete(
                SYSTEM, request, call_role,
                max_tokens=min(4096, max(640, 420 * count)),
            )
            return _parse_items(raw)[:count]
        except LLMProviderError:
            raise
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}; raw={raw[:500]}"
    raise ValueError(
        f"hypothesis generation failed after {max_repairs + 1} content attempts; {last_err}"
    )


def propose(
    view,
    llm,
    model: str = MODELS["hypothesis"],
    wildcard: bool = False,
    max_repairs: int = 1,
) -> Hypothesis:
    """Single-item compatibility wrapper used by direct callers and tests."""
    return propose_batch(
        view, llm, count=1, wildcard=wildcard, model=model, max_repairs=max_repairs,
    )[0]
