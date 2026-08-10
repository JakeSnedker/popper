"""Performance-scoped research agents and their injectable LLM boundary."""

from popper.agents.fidelity import (
    audit_fidelity,
    fidelity_errors,
    hypothesis_feature_errors,
    validate_fidelity,
)
from popper.agents.gatekeeper import screen
from popper.agents.hypothesis import propose, propose_batch
from popper.agents.llm import (
    MODELS,
    ClaudeCliClient,
    CodexCliClient,
    LLMError,
    LLMOutputError,
    LLMProviderError,
    LLMProvidersExhausted,
    LLMRouter,
    OpenCodeCliClient,
    StubLLM,
    claude_reserve_seconds_left,
    get_llm,
    parse_route,
    strip_fence,
)
from popper.agents.synthesis import synthesize
from popper.agents.views import BlindView, FullView, MapEntry, view

__all__ = [
    "propose", "propose_batch", "synthesize", "screen",
    "audit_fidelity", "fidelity_errors", "hypothesis_feature_errors", "validate_fidelity",
    "view", "BlindView", "FullView", "MapEntry",
    "MODELS", "ClaudeCliClient", "CodexCliClient", "OpenCodeCliClient", "LLMRouter",
    "LLMError", "LLMOutputError", "claude_reserve_seconds_left",
    "LLMProviderError", "LLMProvidersExhausted", "get_llm", "parse_route",
    "StubLLM", "strip_fence",
]
