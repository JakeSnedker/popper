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
import subprocess
import urllib.request

from popper.config import settings

# model split (spec §12). Overridable per agent call.
# synthesis was opus — dropped to sonnet: it's the heaviest call and runs on EVERY
# explore iteration, so opus dominated cost + subscription rate-limit burn. Sonnet
# compiles the DSL fine (the synthesis repair loop catches the occasional miss) and
# multiplies throughput-per-rate-window, which is what unattended bulk explore needs.
MODELS = {
    "hypothesis": "claude-sonnet-5",
    "gatekeeper": "claude-haiku-4-5-20251001",
    "synthesis": "claude-sonnet-5",
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


def _cli_model(model: str) -> str:
    """Map an API model id to a `claude` CLI alias."""
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    return "sonnet"


class ClaudeCliClient:
    """$0 backend: shells to `claude -p` so discovery runs on the Claude.ai
    subscription instead of the metered API. Same interface as AnthropicClient.

    The subprocess env has ANTHROPIC_API_KEY stripped ON PURPOSE — with the key
    present the CLI bills the API and the whole point is lost; without it the CLI
    falls back to the claude.ai OAuth login. Slower per call than the API (a CLI
    spawn each time) and subject to subscription rate limits, so it's the
    autopilot/cron backend, not the low-latency one.
    ponytail: no retries/backoff here — the agents already have a self-repair
    loop; add backoff only if rate-limit errors actually bite in practice."""

    def __init__(self, timeout: int = 180):
        self.timeout = timeout

    def complete(self, system: str, prompt: str, model: str, max_tokens: int = 1024) -> str:
        env = {k: v for k, v in os.environ.items()
               if k not in ("ANTHROPIC_API_KEY", "POPPER_ANTHROPIC_API_KEY")}
        try:
            r = subprocess.run(
                ["claude", "-p", "--model", _cli_model(model),
                 "--append-system-prompt", system],
                input=prompt, capture_output=True, text=True,
                env=env, timeout=self.timeout,
            )
        except FileNotFoundError as e:
            raise RuntimeError("`claude` CLI not found (needed for POPPER_LLM_BACKEND=cli)") from e
        if r.returncode != 0:
            raise RuntimeError(f"claude -p failed ({r.returncode}): {r.stderr.strip()[:300]}")
        out = r.stdout.strip()
        if not out:
            raise RuntimeError("claude -p returned empty output")
        return out


def get_llm():
    """Pick the LLM backend from settings: 'cli' = $0 subscription via `claude -p`,
    anything else = the metered Anthropic API. Env: POPPER_LLM_BACKEND."""
    if getattr(settings, "llm_backend", "api").lower() == "cli":
        return ClaudeCliClient()
    return AnthropicClient()


class StubLLM:
    """Deterministic canned responses for tests. Pops in order; records calls."""

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, system: str, prompt: str, model: str, max_tokens: int = 1024) -> str:
        self.calls.append({"system": system, "prompt": prompt, "model": model})
        return self.responses.pop(0)
