"""Verify official Claude.ai and ChatGPT CLI subscription authentication.

No tokens are printed. Exit 0 means the host can run subscription routing; exit 1
lists only the providers that need login changes.
"""

import json
import os
import subprocess


def _run(command, strip_keys=()):
    env = {key: value for key, value in os.environ.items() if key not in strip_keys}
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def main() -> int:
    ok = True
    claude = _run(["claude", "auth", "status"],
                  strip_keys=("ANTHROPIC_API_KEY", "POPPER_ANTHROPIC_API_KEY"))
    try:
        payload = json.loads(claude.stdout) if claude and claude.returncode == 0 else {}
    except json.JSONDecodeError:
        payload = {}
    if payload.get("loggedIn") and payload.get("authMethod") == "claude.ai":
        print("Claude: OK (claude.ai subscription login)")
    else:
        print("Claude: NEEDS LOGIN (`claude` then sign in with Claude.ai)")
        ok = False

    codex_executable = "codex.cmd" if os.name == "nt" else "codex"
    codex = _run([codex_executable, "login", "status"],
                 strip_keys=("OPENAI_API_KEY", "POPPER_OPENAI_API_KEY"))
    detail = "\n".join(
        part for part in ((codex.stdout.strip() if codex else ""),
                          (codex.stderr.strip() if codex else "")) if part
    ).lower()
    if codex and codex.returncode == 0 and "chatgpt" in detail and "api key" not in detail:
        print("OpenAI: OK (ChatGPT subscription via Codex CLI)")
    elif "api key" in detail:
        print("OpenAI: API-KEY LOGIN BLOCKED (`codex logout`, then `codex login` with ChatGPT)")
        ok = False
    else:
        print("OpenAI: NEEDS LOGIN (`codex login` with ChatGPT)")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
