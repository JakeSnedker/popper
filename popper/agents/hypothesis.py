"""Hypothesis agent (spec/02): propose a novel, falsifiable alpha aimed at the
map's sparse regions. Performance-BLIND — it sees coverage / whitespace / prior
claims via a BlindView, never any result. A good hypothesis names a market
MECHANISM (why the edge exists), not just a pattern.
"""

import json

from popper.agents.llm import MODELS, strip_fence
from popper.artifacts import Hypothesis, Regime
from popper.dsl.features import FEATURES

_REGIMES = {r.value for r in Regime}

SYSTEM = (
    "You are a quantitative researcher proposing FALSIFIABLE trading hypotheses "
    "for the US30 index. Name a market MECHANISM (why an edge should exist — "
    "liquidity, order-flow, or behavioural), not merely a pattern. Aim at "
    "under-explored regions of the research map. Respond with ONLY a JSON object: "
    '{"claim": str, "mechanism": str, "required_features": [str], '
    '"expected_regime": "trending|ranging|high_vol|low_vol|unknown"}.'
)


def _prompt(view) -> str:
    claims = [e.claim for e in view.hypotheses()][:12]
    return "\n".join([
        "Current map coverage (edge_type: count):",
        json.dumps(view.coverage()),
        f"Under-explored (whitespace) edge types: {view.whitespace() or 'none'}",
        "Existing claims — do NOT duplicate:",
        *(f"- {c}" for c in claims),
        "",
        f"Features you may require: {', '.join(FEATURES)} (plus ema_<n> for any n).",
        "Propose ONE new hypothesis targeting a gap.",
    ])


def propose(view, llm, model: str = MODELS["hypothesis"]) -> Hypothesis:
    """Generate one Hypothesis from the blind map view via `llm`."""
    d = json.loads(strip_fence(llm.complete(SYSTEM, _prompt(view), model)))
    regime = d.get("expected_regime", "unknown")
    return Hypothesis(
        claim=d["claim"],
        mechanism=d["mechanism"],
        required_features=d.get("required_features", []),
        expected_regime=Regime(regime) if regime in _REGIMES else Regime.UNKNOWN,
    )
