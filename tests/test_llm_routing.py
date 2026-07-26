"""Provider routing, batching, fidelity, and circuit-breaker acceptance tests."""

import json
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
    RouteTarget,
    parse_route,
)
from popper.artifacts import Hypothesis, StrategySpec
from popper.dsl import DSLValidationError
from popper.memory import LocalEmbedder
from popper.memory.sqlite_repo import SqliteMemory


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
