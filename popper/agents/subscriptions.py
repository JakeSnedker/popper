"""Detect the zero-marginal-cost LLM CLIs available to this operator.

Claude and Codex require official subscription OAuth. OpenCode contributes its
free hosted DeepSeek model. Metered API-key routes are not treated as available.
"""

from __future__ import annotations

import json
import os
import subprocess

KNOWN_PROVIDERS = ("claude_cli", "codex_cli", "opencode_cli")

# The only opencode model Popper routes to by default: OpenCode Zen's free
# DeepSeek tier. Named here (not in llm.py) so detection and routing cannot
# disagree about which model counts as zero-cost.
OPENCODE_FREE_MODEL = "opencode/deepseek-v4-flash-free"

_cache: dict[str, tuple[bool, str]] | None = None


def _run(command: list[str], strip_keys: tuple[str, ...] = ()) -> subprocess.CompletedProcess | None:
    env = {key: value for key, value in os.environ.items() if key not in strip_keys}
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _probe_claude() -> tuple[bool, str]:
    result = _run(["claude", "auth", "status"],
                  strip_keys=("ANTHROPIC_API_KEY", "POPPER_ANTHROPIC_API_KEY"))
    if result is None:
        return False, "Claude Code CLI is not installed or did not respond"
    try:
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
    except json.JSONDecodeError:
        payload = {}
    if payload.get("loggedIn") and payload.get("authMethod") == "claude.ai":
        return True, "claude.ai subscription login"
    return False, "run `claude` and sign in with a Claude.ai subscription"


def _probe_codex() -> tuple[bool, str]:
    executable = "codex.cmd" if os.name == "nt" else "codex"
    result = _run([executable, "login", "status"],
                  strip_keys=("OPENAI_API_KEY", "POPPER_OPENAI_API_KEY"))
    if result is None:
        return False, "Codex CLI is not installed or did not respond"
    detail = "\n".join(
        part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part
    ).lower()
    if result.returncode == 0 and "chatgpt" in detail and "api key" not in detail:
        return True, "ChatGPT subscription via Codex CLI"
    if "api key" in detail:
        return False, "API-key login is metered; run `codex logout` then `codex login` with ChatGPT"
    return False, "run `codex login` and sign in with a ChatGPT subscription"


def _probe_opencode() -> tuple[bool, str]:
    executable = "opencode.cmd" if os.name == "nt" else "opencode"
    result = _run([executable, "models", "opencode"])
    if result is None:
        return False, "opencode CLI is not installed or did not respond"
    if result.returncode == 0 and OPENCODE_FREE_MODEL in (result.stdout or ""):
        return True, f"free OpenCode Zen tier ({OPENCODE_FREE_MODEL})"
    return False, "update or run `opencode` so its free DeepSeek model is listed"


_PROBES = {
    "claude_cli": _probe_claude,
    "codex_cli": _probe_codex,
    "opencode_cli": _probe_opencode,
}


def probe(refresh: bool = False) -> dict[str, tuple[bool, str]]:
    """Login state per provider as {provider: (authenticated, human reason)}.

    Cached for the life of the process: a hunt spawns one router and must not
    shell out to two CLIs on every batch.
    """
    global _cache
    if _cache is None or refresh:
        _cache = {name: check() for name, check in _PROBES.items()}
    return _cache


def detect_providers(refresh: bool = False) -> list[str]:
    """Available providers in stable route-filtering order."""
    state = probe(refresh=refresh)
    return [name for name in KNOWN_PROVIDERS if state[name][0]]
