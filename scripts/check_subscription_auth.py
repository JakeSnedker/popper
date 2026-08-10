"""Report which zero-marginal-cost LLM routes this node can use.

Claude and Codex use official subscription OAuth; OpenCode contributes its free
hosted DeepSeek model. Exit 1 only when no route is available. No tokens print.
"""

from popper.agents.llm import enabled_providers
from popper.agents.subscriptions import probe

_LABELS = {
    "claude_cli": "Claude (claude.ai)",
    "codex_cli": "OpenAI (ChatGPT via Codex)",
    "opencode_cli": "DeepSeek (free via OpenCode)",
}


def main() -> int:
    state = probe()
    for provider, (ok, reason) in state.items():
        marker = "OK" if ok else "NOT AVAILABLE"
        print(f"{_LABELS[provider]}: {marker} - {reason}")

    active = sorted(enabled_providers())  # raises if nothing usable is configured
    print("\nActive routing providers: " + ", ".join(active))
    return 0


if __name__ == "__main__":
    from popper.agents.llm import NoSubscriptionAvailable

    try:
        raise SystemExit(main())
    except NoSubscriptionAvailable as exc:
        print(f"\nFAIL: {exc}")
        raise SystemExit(1) from None
