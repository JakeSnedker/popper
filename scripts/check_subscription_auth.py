"""Report which official subscription CLIs this node is signed in to.

Popper configures itself from whatever the operator owns: both Claude.ai and
ChatGPT gives Claude-led routing with Codex as the fallback, one subscription
gives a working single-provider node. Exit 1 only when nothing is authenticated.
No tokens are printed.
"""

from popper.agents.llm import enabled_providers
from popper.agents.subscriptions import probe

_LABELS = {"claude_cli": "Claude (claude.ai)", "codex_cli": "OpenAI (ChatGPT via Codex)"}


def main() -> int:
    state = probe()
    for provider, (ok, reason) in state.items():
        marker = "OK" if ok else "NOT AVAILABLE"
        print(f"{_LABELS[provider]}: {marker} - {reason}")

    active = sorted(enabled_providers())  # raises if nothing usable is configured
    if len(active) > 1:
        print("\nRouting: Claude leads, Codex is the fallback.")
    else:
        print(f"\nRouting: single-provider node on {active[0]}; every role uses it.")
    return 0


if __name__ == "__main__":
    from popper.agents.llm import NoSubscriptionAvailable

    try:
        raise SystemExit(main())
    except NoSubscriptionAvailable as exc:
        print(f"\nFAIL: {exc}")
        raise SystemExit(1) from None
