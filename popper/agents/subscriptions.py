"""Detect which official subscription CLIs this operator is actually logged into.

Popper is subscription-only: an API-key login is metered and is treated as
absent. Detection is what makes a node self-configuring — an operator with both
Claude.ai and ChatGPT gets the full route table, an operator with only one gets
routes collapsed onto the subscription they own, and nobody edits a config file
to say so.
"""

from __future__ import annotations

import json
import os
import subprocess

KNOWN_PROVIDERS = ("claude_cli", "codex_cli")

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


_PROBES = {"claude_cli": _probe_claude, "codex_cli": _probe_codex}


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
    """Authenticated providers in preference order: Claude first, Codex second."""
    state = probe(refresh=refresh)
    return [name for name in KNOWN_PROVIDERS if state[name][0]]
