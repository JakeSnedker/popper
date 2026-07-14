"""Injectable LLM client for the research agents (spec/02).

Mirrors popper/memory/embed.py: the real client is plain urllib against the
Anthropic Messages API — NO SDK dependency. Tests inject StubLLM (canned
responses, no network, deterministic). Per-agent model strings live in MODELS
(spec §12); each agent takes `model=` so they stay overridable.

Fable 5 is BUILD-ONLY — these runtime models never include it.
"""

import json
import os
import re
import urllib.request

from popper.config import settings

# model split (spec §12). Overridable per agent call.
MODELS = {
    "hypothesis": "claude-sonnet-5",
    "gatekeeper": "claude-haiku-4-5-20251001",
    "synthesis": "claude-opus-4-8",
}

_FENCE = re.compile(r"```(?:json|yaml|yml)?\s*(.*?)```", re.DOTALL)


def strip_fence(text: str) -> str:
    """Pull the body out of a ```json/```yaml fenced block if the model wrapped it."""
    m = _FENCE.search(text)
    return (m.group(1) if m else text).strip()


def _extract_text(payload: dict) -> str:
    """Anthropic content can contain multiple block types; use every text block."""
    blocks = payload.get("content", [])
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and "text" in b).strip()
    if text:
        return text
    raise RuntimeError(f"Anthropic response contained no text block: {json.dumps(payload)[:500]}")


class AnthropicClient:
    """Anthropic Messages API via stdlib urllib. Key from settings or env."""

    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")

    def complete(self, system: str, prompt: str, model: str, max_tokens: int = 1024) -> str:
        if not self.api_key:
            raise RuntimeError("no Anthropic API key (settings.anthropic_api_key / ANTHROPIC_API_KEY)")
        body = json.dumps({
            "model": model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(self.URL, data=body, headers={
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed host)
            return _extract_text(json.load(resp))


class StubLLM:
    """Deterministic canned responses for tests. Pops in order; records calls."""

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, system: str, prompt: str, model: str, max_tokens: int = 1024) -> str:
        self.calls.append({"system": system, "prompt": prompt, "model": model})
        return self.responses.pop(0)
