"""Provider routing, batching, fidelity, and circuit-breaker acceptance tests."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from popper.agents import propose_batch, synthesize, view
from popper.agents.fidelity import fidelity_errors, validate_fidelity
from popper.agents.llm import (
    ClaudeCliClient,
    CodexCliClient,
    LLMProviderError,
    LLMProvidersExhausted,
    LLMRouter,
    OpenCodeCliClient,
    RouteTarget,
    parse_route,
)
from popper.agents.subscriptions import OPENCODE_FREE_MODEL
from popper.artifacts import Hypothesis, StrategySpec
from popper.dsl import DSLValidationError
from popper.memory import LocalEmbedder
from popper.memory.sqlite_repo import SqliteMemory


def _unreachable(*args, **kwargs):
    raise AssertionError("provider must be rejected before it is launched")


def _mem(tmp_path):
    return SqliteMemory(str(tmp_path / "routing.db"), embedder=LocalEmbedder())


class FakeClient:
    def __init__(self, provider, responses):
        self.provider = provider
        self.responses = list(responses)
        self.last_meta = {}
        self.calls = 0

    def complete(self, system, prompt, model, max_tokens=1024, role=""):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        self.last_meta = {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.0}
        return response


def _router(*targets):
    routes = {role: list(targets) for role in (
        "hypothesis", "hypothesis_strong", "wildcard", "synthesis",
        "synthesis_strong", "audit",
    )}
    return LLMRouter(routes)


def test_route_parser_fails_closed_on_typos():
    assert parse_route("claude_cli:sonnet,codex_cli:default")[1].provider == "codex_cli"
    with pytest.raises(ValueError, match="unknown LLM provider"):
        parse_route("calude_cli:sonnet")
    with pytest.raises(ValueError, match="provider:model"):
        parse_route("sonnet")


def test_router_fails_over_and_records_usage():
    router = _router(RouteTarget("claude_cli", "sonnet"), RouteTarget("codex_cli", "default"))
    router._clients = {
        "claude_cli": FakeClient("claude_cli", [LLMProviderError(
            "quota", provider="claude_cli", retryable=True, rate_limited=True, retry_after=60,
        )]),
        "codex_cli": FakeClient("codex_cli", ["ok"]),
    }
    assert router.complete("s", "p", "hypothesis") == "ok"
    stats = router.stats()
    assert stats == {
        "calls": 1, "failed_calls": 1, "input_tokens": 100, "output_tokens": 20,
        "cache_read_tokens": 0, "cost_usd": 0.0, "failovers": 1,
        "providers": {"codex_cli": 1},
    }


def test_router_opens_circuit_instead_of_hammering():
    failure = LLMProviderError(
        "rate limited", provider="claude_cli", retryable=True,
        rate_limited=True, retry_after=120,
    )
    client = FakeClient("claude_cli", [failure])
    router = _router(RouteTarget("claude_cli", "sonnet"))
    router._clients = {"claude_cli": client}
    with pytest.raises(LLMProvidersExhausted) as first:
        router.complete("s", "p", "hypothesis")
    with pytest.raises(LLMProvidersExhausted) as second:
        router.complete("s", "p", "hypothesis")
    assert first.value.retry_after == 120
    assert second.value.retry_after > 0
    assert client.calls == 1


def test_claude_cli_is_stateless_and_uses_replacement_system_prompt(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0, stderr="",
            stdout=json.dumps({
                "result": "answer", "usage": {"input_tokens": 12, "output_tokens": 3},
                "total_cost_usd": 0,
            }),
        )

    monkeypatch.setattr("popper.agents.llm.subprocess.run", fake_run)
    client = ClaudeCliClient()
    assert client.complete("small system", "task", "haiku", role="synthesis") == "answer"
    command = captured["command"]
    assert "--system-prompt" in command and "--append-system-prompt" not in command
    assert {"--safe-mode", "--no-session-persistence", "--disable-slash-commands"} <= set(command)
    assert command[command.index("--tools") + 1] == ""
    assert captured["cwd"]
    assert client.last_meta["input_tokens"] == 12


def test_codex_cli_rejects_metered_api_login_by_default(monkeypatch):
    monkeypatch.setattr(
        "popper.agents.llm.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="Logged in using an API key - sk-proj-***", stderr="",
        ),
    )
    with pytest.raises(LLMProviderError, match="metered API key"):
        CodexCliClient()._check_subscription_auth()


def test_hypothesis_batch_amortizes_one_model_call(tmp_path):
    response = json.dumps([
        {"claim": f"claim {i}", "mechanism": f"mechanism {i}",
         "required_features": ["atr_14"], "expected_regime": "unknown"}
        for i in range(4)
    ])
    client = FakeClient("fake", [response])
    hypotheses = propose_batch(view("hypothesis", _mem(tmp_path)), client, count=4)
    assert len(hypotheses) == 4
    assert client.calls == 1


def test_provider_failure_is_not_content_repaired(tmp_path):
    error = LLMProviderError(
        "quota", provider="fake", retryable=True, rate_limited=True, retry_after=60,
    )
    client = FakeClient("fake", [error, "must not be consumed"])
    with pytest.raises(LLMProviderError):
        propose_batch(view("hypothesis", _mem(tmp_path)), client, count=3, max_repairs=1)
    assert client.calls == 1


def test_fidelity_requires_declared_and_used_features():
    hypothesis = Hypothesis(
        claim="momentum with volatility", mechanism="flow",
        required_features=["ema_50", "atr_14"],
    )
    spec = StrategySpec(
        hypothesis_id="H", name="x",
        alpha={"features": ["ema_50"], "signal": "close > ema_50"},
    )
    errors = fidelity_errors(hypothesis, spec)
    assert any("missing" in error for error in errors)
    assert any("not used" in error for error in errors)
    with pytest.raises(DSLValidationError):
        validate_fidelity(hypothesis, spec)


def test_unsupported_hypothesis_feature_spends_no_synthesis_call():
    hypothesis = Hypothesis(claim="needs unavailable data", mechanism="flow",
                            required_features=["future_order_book"])
    client = FakeClient("fake", ["must not be consumed"])
    with pytest.raises(DSLValidationError, match="unknown feature"):
        synthesize(hypothesis, client, enforce_local_context=False)
    assert client.calls == 0


def test_synthesis_escalates_only_after_deterministic_failure():
    hypothesis = Hypothesis(claim="trend persists", mechanism="flow", required_features=["ema_50"])
    invalid = "name: x\nalpha: {features: [ema_50], signal: 'close > nope'}"
    valid = "name: x\nalpha: {features: [ema_50], signal: 'close > ema_50'}"

    class RecordingClient(FakeClient):
        def complete(self, system, prompt, model, max_tokens=1024, role=""):
            self.roles.append(model)
            return super().complete(system, prompt, model, max_tokens, role)

    client = RecordingClient("fake", [invalid, valid])
    client.roles = []
    result = synthesize(hypothesis, client, enforce_local_context=False)
    assert result.alpha.signal == "close > ema_50"
    assert client.roles == ["synthesis", "synthesis_strong"]


# --- per-operator subscription selection ---

def _routes(monkeypatch, providers, detected=("claude_cli", "codex_cli")):
    monkeypatch.setattr("popper.agents.llm.settings.llm_providers", providers)
    monkeypatch.setattr("popper.agents.llm.detect_providers", lambda: list(detected))
    from popper.agents.llm import _role_routes

    return {role: [t.provider for t in targets] for role, targets in _role_routes().items()}


def test_operator_with_both_subscriptions_keeps_claude_led_routing(monkeypatch):
    routes = _routes(monkeypatch, "auto")
    assert routes["hypothesis"] == ["claude_cli", "codex_cli"]
    assert routes["synthesis"] == ["claude_cli", "codex_cli"]
    # Every role still has a fallback available.
    assert all(len(providers) == 2 for providers in routes.values())


def test_chatgpt_only_operator_runs_every_role_on_codex(monkeypatch):
    routes = _routes(monkeypatch, "auto", detected=("codex_cli",))
    assert routes and all(providers == ["codex_cli"] for providers in routes.values())


def test_claude_only_operator_runs_every_role_on_claude(monkeypatch):
    routes = _routes(monkeypatch, "auto", detected=("claude_cli",))
    assert routes and all(providers == ["claude_cli"] for providers in routes.values())


def test_node_with_no_subscription_fails_loudly(monkeypatch):
    from popper.agents.llm import NoSubscriptionAvailable

    with pytest.raises(NoSubscriptionAvailable):
        _routes(monkeypatch, "auto", detected=())


def test_explicit_provider_override_beats_detection(monkeypatch):
    routes = _routes(monkeypatch, "codex_cli", detected=("claude_cli", "codex_cli"))
    assert all(providers == ["codex_cli"] for providers in routes.values())


def test_unknown_provider_name_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="unknown LLM provider"):
        _routes(monkeypatch, "gemini_cli")


# --- opencode / DeepSeek provider ---

def _opencode_events(text="answer"):
    return "\n".join(json.dumps(e) for e in [
        {"type": "step_start", "part": {"type": "step-start"}},
        {"type": "text", "part": {"type": "text", "text": text}},
        {"type": "step_finish", "part": {
            "type": "step-finish", "cost": 0,
            "tokens": {"input": 500, "output": 40, "cache": {"read": 100, "write": 0}},
        }},
    ])


def test_opencode_runs_toolless_from_an_isolated_throwaway_directory(monkeypatch):
    captured = {}
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY", "GLM_API_KEY"):
        monkeypatch.setenv(key, "must-not-reach-opencode")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        workspace = Path(kwargs["cwd"])
        captured["workspace_entries"] = {item.name for item in workspace.iterdir()}
        captured["config"] = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        return SimpleNamespace(returncode=0, stderr="", stdout=_opencode_events())

    monkeypatch.setattr("popper.agents.llm.subprocess.run", fake_run)
    client = OpenCodeCliClient()
    assert client.complete("system", "task", "deepseek", role="synthesis") == "answer"

    command = captured["command"]
    assert command[command.index("--model") + 1] == OPENCODE_FREE_MODEL
    assert {"--pure", "--agent", "--format"} <= set(command)
    assert command[command.index("--agent") + 1] == "popper"
    assert command[command.index("--format") + 1] == "json"
    config = captured["config"]
    assert config["tools"] == {"*": False}
    assert config["permission"] == {"*": "deny"}
    assert config["agent"]["popper"]["tools"] == {"*": False}
    assert captured["workspace_entries"] == {".git"}
    assert all(key.startswith("XDG_") and captured["env"].get(key)
               for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"))
    assert "OPENCODE_CONFIG" not in captured["env"]
    assert captured["env"]["OPENCODE_DISABLE_DEFAULT_PLUGINS"] == "true"
    assert all(key not in captured["env"] for key in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY", "GLM_API_KEY",
    ))
    # Prompt carries the schema, since OpenCode has no structured-output flag.
    assert "expected_regime" not in captured["input"] and "yaml" in captured["input"]
    assert client.last_meta["input_tokens"] == 500
    assert client.last_meta["cache_read_tokens"] == 100
    assert client.last_meta["cost_usd"] == 0.0


def test_opencode_rejects_a_metered_model_by_default(monkeypatch):
    monkeypatch.setattr("popper.agents.llm.subprocess.run", _unreachable)
    with pytest.raises(LLMProviderError, match="not the approved free DeepSeek model"):
        OpenCodeCliClient().complete("system", "task", "anthropic/claude-opus-4")


def test_opencode_reports_a_provider_error_that_exited_zero(monkeypatch):
    payload = json.dumps({"type": "error", "error": {"data": {
        "message": "Gone: model reached end of life", "statusCode": 410}}})
    monkeypatch.setattr(
        "popper.agents.llm.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=payload),
    )
    with pytest.raises(LLMProviderError, match="end of life"):
        OpenCodeCliClient().complete("system", "task", "deepseek")


def test_three_provider_operator_gets_deepseek_in_the_rotation(monkeypatch):
    routes = _routes(monkeypatch, "auto",
                     detected=("claude_cli", "codex_cli", "opencode_cli"))
    assert routes["hypothesis"] == ["claude_cli", "codex_cli", "opencode_cli"]
    assert "opencode_cli" in routes["wildcard"]


def test_deepseek_only_operator_runs_every_role(monkeypatch):
    routes = _routes(monkeypatch, "auto", detected=("opencode_cli",))
    assert routes and all(providers == ["opencode_cli"] for providers in routes.values())


# --- Claude reserve ---

def _reserve(monkeypatch, tmp_path, limit=2, hours=5):
    monkeypatch.setenv("POPPER_NODE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("popper.agents.llm.settings.llm_claude_max_calls_per_window", limit)
    monkeypatch.setattr("popper.agents.llm.settings.llm_claude_reserve_window_hours", hours)


def test_claude_reserve_hands_the_role_to_the_next_provider(monkeypatch, tmp_path):
    from popper.agents.llm import claude_reserve_seconds_left

    _reserve(monkeypatch, tmp_path, limit=2)
    claude = FakeClient("claude_cli", ["a", "b", "c"])
    deepseek = FakeClient("opencode_cli", ["from deepseek"])
    router = _router(RouteTarget("claude_cli", "sonnet"), RouteTarget("opencode_cli", "deepseek"))
    router._clients = {"claude_cli": claude, "opencode_cli": deepseek}

    assert router.complete("s", "p", "hypothesis") == "a"
    assert router.complete("s", "p", "hypothesis") == "b"
    assert claude_reserve_seconds_left() > 0
    # Third call must not touch Claude; research continues on DeepSeek.
    assert router.complete("s", "p", "hypothesis") == "from deepseek"
    assert claude.calls == 2 and deepseek.calls == 1
    assert router.records[-1]["provider"] == "opencode_cli"


def test_claude_reserve_survives_a_process_restart(monkeypatch, tmp_path):
    from popper.agents.llm import claude_reserve_seconds_left, record_claude_call

    _reserve(monkeypatch, tmp_path, limit=1)
    record_claude_call()
    assert claude_reserve_seconds_left() > 0
    # A hunt is a fresh process; only the on-disk count can cap anything.
    monkeypatch.setattr("popper.agents.llm.settings.llm_claude_reserve_window_hours", 5)
    assert claude_reserve_seconds_left() <= 5 * 3600


def test_reserve_disabled_by_default_zero(monkeypatch, tmp_path):
    from popper.agents.llm import claude_reserve_seconds_left, record_claude_call

    _reserve(monkeypatch, tmp_path, limit=0)
    record_claude_call()
    assert claude_reserve_seconds_left() == 0
