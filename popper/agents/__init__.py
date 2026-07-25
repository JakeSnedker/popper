"""Research agents (spec/02, Phase 6 DRAFT): Hypothesis + Synthesis + Gatekeeper.

Each agent is a stateless function over a scoped memory view (views.py). The
generation agents are structurally performance-blind — see views.BlindView.
"""

from popper.agents.gatekeeper import screen
from popper.agents.hypothesis import propose
from popper.agents.llm import (
    MODELS,
    AnthropicClient,
    ClaudeCliClient,
    StubLLM,
    get_llm,
    strip_fence,
)
from popper.agents.synthesis import synthesize
from popper.agents.views import BlindView, FullView, MapEntry, view

__all__ = [
    "propose", "synthesize", "screen",
    "view", "BlindView", "FullView", "MapEntry",
    "MODELS", "AnthropicClient", "ClaudeCliClient", "get_llm", "StubLLM", "strip_fence",
]
