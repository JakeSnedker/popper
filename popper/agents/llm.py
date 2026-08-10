"""Role-aware LLM routing for Popper's performance-blind research agents.

Provider transports are intentionally thin and stateless. The router owns
subscription failover, circuit breaking, and usage telemetry; the agents own
content repair. Provider failures must never be mistaken for malformed JSON/YAML.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from popper.agents.subscriptions import OPENCODE_FREE_MODEL, detect_providers
from popper.config import settings
from popper.node_operator import NodePaths, read_json, write_json_atomic

# Logical roles, not provider model ids. LLMRouter resolves each role from config.
MODELS = {
    "hypothesis": "hypothesis",
    "hypothesis_strong": "hypothesis_strong",
    "wildcard": "wildcard",
    "synthesis": "synthesis",
    "synthesis_strong": "synthesis_strong",
    "audit": "audit",
}

_FENCE = re.compile(r"```(?:json|yaml|yml)?\s*(.*?)```", re.DOTALL)
_RATE_MARKERS = (
    "rate limit", "rate_limit", "too many requests", "usage limit", "quota",
    "capacity", "resets at", "you've hit your limit", "429",
)
_CODEX_EXECUTABLE = "codex.cmd" if os.name == "nt" else "codex"
_OPENCODE_EXECUTABLE = "opencode.cmd" if os.name == "nt" else "opencode"
_KNOWN_PROVIDERS = {"claude_cli", "codex_cli", "opencode_cli"}
# Model to use for a role when the operator owns only this provider and the
# configured route named nobody else they have.
_SINGLE_PROVIDER_FALLBACK = {
    "hypothesis": {"claude_cli": "sonnet", "codex_cli": "default", "opencode_cli": "deepseek"},
    "hypothesis_strong": {"claude_cli": "sonnet", "codex_cli": "default", "opencode_cli": "deepseek"},
    "wildcard": {"claude_cli": "opus", "codex_cli": "default", "opencode_cli": "deepseek"},
    "synthesis": {"claude_cli": "haiku", "codex_cli": "default", "opencode_cli": "deepseek"},
    "synthesis_strong": {"claude_cli": "sonnet", "codex_cli": "default", "opencode_cli": "deepseek"},
    "audit": {"claude_cli": "opus", "codex_cli": "default", "opencode_cli": "deepseek"},
}
_HYPOTHESIS_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "claim": {"type": "string"},
        "mechanism": {"type": "string"},
        "required_features": {"type": "array", "items": {"type": "string"}},
        "expected_regime": {
            "type": "string",
            "enum": ["trending", "ranging", "high_vol", "low_vol", "unknown"],
        },
    },
    "required": ["claim", "mechanism", "required_features", "expected_regime"],
    "additionalProperties": False,
}
# Claude/Codex structured output requires an object root, so batches live under
# one key rather than using a top-level array.
_HYPOTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "hypotheses": {"type": "array", "minItems": 1, "items": _HYPOTHESIS_ITEM_SCHEMA},
    },
    "required": ["hypotheses"],
    "additionalProperties": False,
}
_SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {"yaml": {"type": "string"}},
    "required": ["yaml"],
    "additionalProperties": False,
}
_AUDIT_SCHEMA = {
    "type": "object",
    "properties": {"faithful": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["faithful", "reason"],
    "additionalProperties": False,
}


def _response_schema(role: str) -> dict | None:
    if role.startswith("hypothesis") or role == "wildcard":
        return _HYPOTHESIS_SCHEMA
    if role.startswith("synthesis"):
        return _SYNTHESIS_SCHEMA
    if role == "audit":
        return _AUDIT_SCHEMA
    return None


def strip_fence(text: str) -> str:
    """Pull the body out of a fenced JSON/YAML response."""
    m = _FENCE.search(text)
    return (m.group(1) if m else text).strip()


class LLMError(RuntimeError):
    """Base class for errors at the model boundary."""


class LLMOutputError(LLMError):
    """The provider answered, but did not return usable model content."""


class NoSubscriptionAvailable(LLMError):
    """The operator has no supported zero-marginal-cost LLM route."""


class LLMProviderError(LLMError):
    """Transport, authentication, capacity, or quota failure."""

    def __init__(self, message: str, *, provider: str, retryable: bool = False,
                 rate_limited: bool = False, retry_after: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.rate_limited = rate_limited
        self.retry_after = retry_after


class LLMProvidersExhausted(LLMProviderError):
    """Every configured provider for a role failed or has an open circuit."""

    def __init__(self, role: str, errors: list[str], retry_after: int):
        super().__init__(
            f"all LLM providers unavailable for {role}: {'; '.join(errors)}",
            provider="router", retryable=True,
            rate_limited=any("rate" in e.lower() or "quota" in e.lower() for e in errors),
            retry_after=retry_after,
        )
        self.role = role
        self.errors = errors


@dataclass(frozen=True)
class RouteTarget:
    provider: str
    model: str


def parse_route(value: str) -> list[RouteTarget]:
    """Parse `provider:model,provider:model`; reject typos before they can bill."""
    targets = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if ":" not in raw:
            raise ValueError(f"LLM route target must be provider:model, got {raw!r}")
        provider, model = (part.strip() for part in raw.split(":", 1))
        if provider not in _KNOWN_PROVIDERS:
            raise ValueError(f"unknown LLM provider {provider!r}")
        if not model:
            raise ValueError(f"missing model in LLM route target {raw!r}")
        targets.append(RouteTarget(provider, model))
    if not targets:
        raise ValueError("LLM route cannot be empty")
    return targets


def _retry_after(headers, default: int) -> int:
    try:
        return max(1, int(float(headers.get("Retry-After", default))))
    except (TypeError, ValueError):
        return default


def _provider_error(provider: str, message: str, *, code: int | None = None,
                    headers=None) -> LLMProviderError:
    text = message.strip() or f"{provider} failed without an error message"
    low = text.lower()
    rate = code == 429 or any(marker in low for marker in _RATE_MARKERS)
    retryable = rate or code in {408, 409, 425, 500, 502, 503, 504, 529}
    default = (settings.llm_rate_limit_backoff_seconds if rate
               else settings.llm_provider_backoff_seconds)
    return LLMProviderError(
        f"{provider} error{f' {code}' if code else ''}: {text[:500]}",
        provider=provider, retryable=retryable, rate_limited=rate,
        retry_after=_retry_after(headers or {}, default) if retryable else None,
    )


class _MeasuredClient:
    provider = "unknown"

    def __init__(self):
        self.last_meta: dict = {}

    def _meta(self, **values) -> None:
        self.last_meta = values


def _claude_model(model: str) -> str:
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    if model == "default":
        return "sonnet"
    return model


class ClaudeCliClient(_MeasuredClient):
    """Official Claude subscription CLI in stateless, no-tools print mode."""

    provider = "claude_cli"

    def __init__(self, timeout: int = 180):
        super().__init__()
        self.timeout = timeout

    def complete(self, system: str, prompt: str, model: str, max_tokens: int = 1024,
                 role: str = "") -> str:
        env = {k: v for k, v in os.environ.items()
               if k not in ("ANTHROPIC_API_KEY", "POPPER_ANTHROPIC_API_KEY")}
        command = [
            "claude", "-p", "--model", _claude_model(model),
            "--system-prompt", system,
            "--tools", "",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--safe-mode",
            "--effort", "low",
            "--output-format", "json",
        ]
        schema = _response_schema(role)
        if schema:
            command += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
        try:
            result = subprocess.run(
                command, input=prompt, capture_output=True, text=True, env=env,
                timeout=self.timeout, cwd=tempfile.gettempdir(),
            )
        except FileNotFoundError as e:
            raise _provider_error(self.provider, "`claude` CLI not found") from e
        except subprocess.TimeoutExpired as e:
            raise _provider_error(self.provider, f"timed out after {self.timeout}s", code=408) from e
        combined = "\n".join(x for x in (result.stderr.strip(), result.stdout.strip()) if x)
        if result.returncode != 0:
            try:
                failed = json.loads(result.stdout)
                detail = (failed.get("result") or failed.get("error") or
                          failed.get("errors") or combined)
            except json.JSONDecodeError:
                detail = combined
            raise _provider_error(self.provider, str(detail))
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise LLMOutputError(f"Claude CLI returned invalid JSON envelope: {result.stdout[:500]}") from e
        if payload.get("is_error"):
            raise _provider_error(self.provider, str(payload.get("result") or payload))
        usage = payload.get("usage") or {}
        self._meta(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
            cost_usd=float(payload.get("total_cost_usd") or 0.0),
        )
        structured = payload.get("structured_output")
        if structured is not None:
            return json.dumps(structured)
        text = payload.get("result")
        if not isinstance(text, str) or not text.strip():
            raise LLMOutputError(f"Claude CLI returned no result: {json.dumps(payload)[:500]}")
        return text.strip()


def _find_codex_text(events: list[dict]) -> str:
    for event in reversed(events):
        item = event.get("item") if isinstance(event, dict) else None
        if isinstance(item, dict) and item.get("type") in {"agent_message", "message"}:
            text = item.get("text") or item.get("content")
            if isinstance(text, str) and text.strip():
                return text.strip()
        for key in ("message", "output_text", "text", "result"):
            text = event.get(key) if isinstance(event, dict) else None
            if isinstance(text, str) and text.strip() and event.get("type") not in {"error"}:
                return text.strip()
    raise LLMOutputError("Codex CLI returned no final assistant message")


class CodexCliClient(_MeasuredClient):
    """Official OpenAI Codex CLI using the signed-in ChatGPT subscription."""

    provider = "codex_cli"

    def __init__(self, timeout: int = 240):
        super().__init__()
        self.timeout = timeout
        self._auth_checked = False

    def _check_subscription_auth(self) -> None:
        if self._auth_checked:
            return
        env = {k: v for k, v in os.environ.items()
               if k not in ("OPENAI_API_KEY", "POPPER_OPENAI_API_KEY")}
        try:
            status = subprocess.run(
                [_CODEX_EXECUTABLE, "login", "status"], capture_output=True, text=True,
                timeout=30, cwd=tempfile.gettempdir(), env=env,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise _provider_error(self.provider, f"cannot inspect Codex auth: {e}") from e
        detail = "\n".join(x for x in (status.stdout.strip(), status.stderr.strip()) if x)
        if status.returncode != 0:
            raise _provider_error(self.provider, detail or "Codex is not logged in")
        if "api key" in detail.lower() and not settings.llm_allow_metered_codex_cli:
            raise _provider_error(
                self.provider,
                "Codex CLI is authenticated with a metered API key; log in with ChatGPT "
                "or explicitly set POPPER_LLM_ALLOW_METERED_CODEX_CLI=true",
            )
        if "chatgpt" not in detail.lower() and not settings.llm_allow_metered_codex_cli:
            raise _provider_error(
                self.provider, f"Codex subscription auth could not be verified: {detail[:200]}",
            )
        self._auth_checked = True

    def complete(self, system: str, prompt: str, model: str, max_tokens: int = 1024,
                 role: str = "") -> str:
        self._check_subscription_auth()
        env = {k: v for k, v in os.environ.items()
               if k not in ("OPENAI_API_KEY", "POPPER_OPENAI_API_KEY")}
        command = [
            _CODEX_EXECUTABLE, "exec", "-", "--sandbox", "read-only", "--ephemeral",
            "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
            "--color", "never", "--json", "-c", "approval_policy=\"never\"",
        ]
        if model != "default":
            command += ["--model", model]
        schema_path = None
        schema = _response_schema(role)
        if schema:
            handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            try:
                json.dump(schema, handle)
                schema_path = handle.name
            finally:
                handle.close()
            command += ["--output-schema", schema_path]
        full_prompt = (
            "Follow these system instructions exactly. Do not use tools or inspect files.\n"
            f"{system}\n\nTask:\n{prompt}"
        )
        try:
            result = subprocess.run(
                command, input=full_prompt, capture_output=True, text=True, env=env,
                timeout=self.timeout, cwd=tempfile.gettempdir(),
            )
        except FileNotFoundError as e:
            raise _provider_error(self.provider, "`codex` CLI not found") from e
        except subprocess.TimeoutExpired as e:
            raise _provider_error(self.provider, f"timed out after {self.timeout}s", code=408) from e
        finally:
            if schema_path:
                Path(schema_path).unlink(missing_ok=True)
        combined = "\n".join(x for x in (result.stderr.strip(), result.stdout.strip()) if x)
        if result.returncode != 0:
            raise _provider_error(self.provider, combined)
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        error = next((e for e in events if e.get("type") in {"error", "turn.failed"}), None)
        if error:
            raise _provider_error(self.provider, json.dumps(error))
        usage = {}
        for event in reversed(events):
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
                break
        self._meta(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cached_input_tokens", 0),
            cache_write_tokens=0,
            cost_usd=0.0,
        )
        return _find_codex_text(events)


# OpenCode is a coding agent, not a completion endpoint. The inline agent has
# no tools, and XDG isolation below prevents the operator's global config, MCPs,
# sessions, plugins, and parent AGENTS.md from entering a research call.
_OPENCODE_CONFIG_CONTENT = json.dumps({
    "permission": {"*": "deny"},
    "tools": {"*": False},
    "agent": {
        "popper": {
            "description": "Stateless no-tools Popper model transport",
            "mode": "primary",
            "prompt": "Return only the requested answer. Never use tools or inspect files.",
            "permission": {"*": "deny"},
            "tools": {"*": False},
        },
    },
}, separators=(",", ":"))
_OPENCODE_MODEL_ALIASES = {"default": OPENCODE_FREE_MODEL, "deepseek": OPENCODE_FREE_MODEL}


def _opencode_model(model: str) -> str:
    resolved = _OPENCODE_MODEL_ALIASES.get(model, model)
    if resolved != OPENCODE_FREE_MODEL:
        raise _provider_error(
            "opencode_cli",
            f"opencode model {resolved!r} is not the approved free DeepSeek model; "
            "use opencode_cli:deepseek",
        )
    return resolved


def _find_opencode_text(events: list[dict]) -> str:
    parts = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "text":
            continue
        part = event.get("part")
        text = part.get("text") if isinstance(part, dict) else None
        if isinstance(text, str) and text:
            parts.append(text)
    result = "".join(parts).strip()
    if result:
        return result
    raise LLMOutputError("opencode CLI returned no assistant text")


class OpenCodeCliClient(_MeasuredClient):
    """Official opencode CLI pinned to the free OpenCode Zen DeepSeek tier.

    Runs from an empty throwaway directory with an isolated config so the agent
    can only answer. opencode has no structured-output flag, so the schema is
    stated in the prompt and the agents' existing JSON repair covers a miss.
    """

    provider = "opencode_cli"

    def __init__(self, timeout: int = 300):
        super().__init__()
        self.timeout = timeout

    def complete(self, system: str, prompt: str, model: str, max_tokens: int = 1024,
                 role: str = "") -> str:
        target = _opencode_model(model)
        env = {k: v for k, v in os.environ.items() if k not in {
            "ANTHROPIC_API_KEY", "POPPER_ANTHROPIC_API_KEY",
            "OPENAI_API_KEY", "POPPER_OPENAI_API_KEY", "NVIDIA_API_KEY",
            "DEEPSEEK_API_KEY", "GLM_API_KEY", "OPENROUTER_API_KEY",
            "OPENCODE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY",
        }}
        instructions = (
            "Follow these instructions exactly. Do not use tools, inspect files, "
            "or comment on your working directory.\n"
            f"{system}\n"
        )
        schema = _response_schema(role)
        if schema:
            instructions += (
                "\nReturn ONLY one JSON object matching this JSON Schema. No prose, "
                f"no code fence:\n{json.dumps(schema, separators=(',', ':'))}\n"
            )
        instructions += f"\nKeep the response within approximately {max_tokens} tokens.\n"
        with tempfile.TemporaryDirectory(prefix="popper-opencode-") as temporary:
            root = Path(temporary)
            workspace = root / "work"
            workspace.mkdir()
            # Bound instruction discovery to this empty workspace.
            (workspace / ".git").mkdir()
            for variable, directory in (
                ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state"),
            ):
                env[variable] = str(root / directory)
            env.pop("OPENCODE_CONFIG", None)
            env.pop("OPENCODE_CONFIG_DIR", None)
            env["OPENCODE_CONFIG_CONTENT"] = _OPENCODE_CONFIG_CONTENT
            env["OPENCODE_DISABLE_DEFAULT_PLUGINS"] = "true"
            env["NO_COLOR"] = "1"
            command = [
                _OPENCODE_EXECUTABLE, "run", "--dir", str(workspace),
                "--model", target, "--agent", "popper", "--format", "json", "--pure",
            ]
            try:
                result = subprocess.run(
                    command, input=f"{instructions}\nTask:\n{prompt}", capture_output=True,
                    text=True, env=env, timeout=self.timeout, cwd=str(workspace),
                )
            except FileNotFoundError as e:
                raise _provider_error(self.provider, "`opencode` CLI not found") from e
            except subprocess.TimeoutExpired as e:
                raise _provider_error(
                    self.provider, f"timed out after {self.timeout}s", code=408,
                ) from e
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        # opencode reports provider failures as an error event and still exits 0,
        # so the events are checked before the return code, not after it.
        error = next((e for e in events if e.get("type") == "error"), None)
        if error:
            detail = error.get("error") or error
            data = detail.get("data") if isinstance(detail, dict) else None
            message = data.get("message") if isinstance(data, dict) else None
            code = data.get("statusCode") if isinstance(data, dict) else None
            raise _provider_error(self.provider, str(message or json.dumps(detail)[:500]),
                                  code=code if isinstance(code, int) else None)
        if result.returncode != 0:
            combined = "\n".join(x for x in (result.stderr.strip(), result.stdout.strip()) if x)
            raise _provider_error(self.provider, combined)
        tokens = {}
        cost = 0.0
        for event in reversed(events):
            part = event.get("part") if isinstance(event, dict) else None
            if isinstance(part, dict) and isinstance(part.get("tokens"), dict):
                tokens = part["tokens"]
                cost = float(part.get("cost") or 0.0)
                break
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        self._meta(
            input_tokens=tokens.get("input", 0),
            output_tokens=tokens.get("output", 0),
            cache_read_tokens=cache.get("read", 0),
            cache_write_tokens=cache.get("write", 0),
            cost_usd=cost,
        )
        return _find_opencode_text(events)


def _budget_file() -> Path:
    return NodePaths.for_root().state_dir / "llm_budget.json"


def _recent_claude_calls(now: float) -> list[float]:
    window = max(1, settings.llm_claude_reserve_window_hours) * 3600
    calls = read_json(_budget_file()).get("claude_cli") or []
    return [t for t in calls if isinstance(t, (int, float)) and now - t < window]


def claude_reserve_seconds_left() -> int:
    """0 while Claude may still be called; otherwise seconds until the cap frees.

    Every hunt is a separate process, so the count lives on disk. An in-memory
    counter would reset on each hunt and cap nothing.
    """
    limit = settings.llm_claude_max_calls_per_window
    if limit <= 0:
        return 0
    now = time.time()
    calls = _recent_claude_calls(now)
    if len(calls) < limit:
        return 0
    window = max(1, settings.llm_claude_reserve_window_hours) * 3600
    return max(1, round(min(calls) + window - now))


def record_claude_call() -> None:
    """Count one call against the reserve. Attempts count: they spend the quota."""
    if settings.llm_claude_max_calls_per_window <= 0:
        return
    now = time.time()
    kept = _recent_claude_calls(now) + [now]
    # ponytail: last writer wins across concurrent hunts. A lost increment
    # undercounts by one call, which is cheaper than a lock file; add one if
    # several nodes ever share a state directory.
    write_json_atomic(_budget_file(), {"claude_cli": kept[-1000:]})


class LLMRouter:
    """Resolve logical agent roles to providers with failover and circuit breaking."""

    def __init__(self, routes: dict[str, list[RouteTarget]], policy: str = "priority"):
        if policy not in {"priority", "round_robin"}:
            raise ValueError(f"unknown LLM route policy {policy!r}")
        self.routes = routes
        self.policy = policy
        self.records: list[dict] = []
        self._clients: dict[str, _MeasuredClient] = {}
        self._offsets = defaultdict(int)
        self._blocked_until: dict[str, float] = {}

    def supports_role(self, role: str) -> bool:
        return role in self.routes and bool(self.routes[role])

    def mark(self) -> int:
        return len(self.records)

    def records_since(self, mark: int) -> list[dict]:
        return [dict(r) for r in self.records[mark:]]

    def _client(self, provider: str) -> _MeasuredClient:
        if provider in self._clients:
            return self._clients[provider]
        if provider == "claude_cli":
            client = ClaudeCliClient()
        elif provider == "codex_cli":
            client = CodexCliClient()
        elif provider == "opencode_cli":
            client = OpenCodeCliClient()
        else:  # parse_route prevents this; keep the boundary fail-closed.
            raise ValueError(f"unknown LLM provider {provider!r}")
        self._clients[provider] = client
        return client

    def _ordered(self, role: str) -> list[RouteTarget]:
        targets = list(self.routes.get(role) or [])
        if not targets:
            raise ValueError(f"no LLM route configured for role {role!r}")
        if self.policy == "round_robin" and role in {"hypothesis", "wildcard"}:
            offset = self._offsets[role] % len(targets)
            return targets[offset:] + targets[:offset]
        return targets

    def complete(self, system: str, prompt: str, model: str, max_tokens: int = 1024) -> str:
        role = model
        now = time.monotonic()
        errors: list[str] = []
        available_at: list[float] = []
        for index, target in enumerate(self._ordered(role)):
            blocked = self._blocked_until.get(target.provider, 0.0)
            if blocked > now:
                available_at.append(blocked)
                errors.append(f"{target.provider} circuit open")
                continue
            if target.provider == "claude_cli":
                # The node holds back part of the Claude.ai session limit for
                # interactive use; other providers still serve the role.
                reserve_wait = claude_reserve_seconds_left()
                if reserve_wait:
                    available_at.append(now + reserve_wait)
                    errors.append(
                        f"claude_cli reserve spent "
                        f"({settings.llm_claude_max_calls_per_window} calls per "
                        f"{settings.llm_claude_reserve_window_hours}h)"
                    )
                    continue
                record_claude_call()
            client = self._client(target.provider)
            started = time.perf_counter()
            client.last_meta = {}
            try:
                text = client.complete(system, prompt, target.model, max_tokens, role=role)
            except LLMProviderError as e:
                latency = round((time.perf_counter() - started) * 1000)
                retry = e.retry_after or (settings.llm_provider_backoff_seconds
                                            if e.retryable else 86400)
                # Authentication/configuration failures are also circuit-broken;
                # retrying an unchanged login on every candidate only burns time.
                self._blocked_until[target.provider] = now + retry
                available_at.append(now + retry)
                self.records.append({
                    "at": datetime.now(timezone.utc).isoformat(), "role": role,
                    "provider": target.provider, "model": target.model, "status": "error",
                    "latency_ms": latency, "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0,
                    "rate_limited": e.rate_limited, "retry_after": e.retry_after,
                    "error": str(e)[:500], "failover_index": index,
                })
                errors.append(str(e))
                continue
            except LLMOutputError as e:
                latency = round((time.perf_counter() - started) * 1000)
                self.records.append({
                    "at": datetime.now(timezone.utc).isoformat(), "role": role,
                    "provider": target.provider, "model": target.model, "status": "invalid_output",
                    "latency_ms": latency, "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0,
                    "rate_limited": False, "retry_after": None,
                    "error": str(e)[:500], "failover_index": index,
                })
                raise
            latency = round((time.perf_counter() - started) * 1000)
            meta = client.last_meta
            self.records.append({
                "at": datetime.now(timezone.utc).isoformat(), "role": role,
                "provider": target.provider, "model": target.model, "status": "ok",
                "latency_ms": latency,
                "input_tokens": int(meta.get("input_tokens") or 0),
                "output_tokens": int(meta.get("output_tokens") or 0),
                "cache_read_tokens": int(meta.get("cache_read_tokens") or 0),
                "cache_write_tokens": int(meta.get("cache_write_tokens") or 0),
                "cost_usd": float(meta.get("cost_usd") or 0.0),
                "rate_limited": False, "retry_after": None, "error": "",
                "failover_index": index,
            })
            if self.policy == "round_robin" and role in {"hypothesis", "wildcard"}:
                self._offsets[role] += 1
            return text
        wait = settings.llm_provider_backoff_seconds
        if available_at:
            wait = max(1, round(min(available_at) - now))
        raise LLMProvidersExhausted(role, errors, wait)

    def stats(self, mark: int = 0) -> dict:
        records = self.records[mark:]
        providers = Counter(r["provider"] for r in records if r["status"] == "ok")
        return {
            "calls": sum(r["status"] == "ok" for r in records),
            "failed_calls": sum(r["status"] != "ok" for r in records),
            "input_tokens": sum(r["input_tokens"] for r in records),
            "output_tokens": sum(r["output_tokens"] for r in records),
            "cache_read_tokens": sum(r["cache_read_tokens"] for r in records),
            "cost_usd": round(sum(r["cost_usd"] for r in records), 6),
            "failovers": sum(r.get("failover_index", 0) > 0 and r["status"] == "ok"
                             for r in records),
            "providers": dict(providers),
        }


def enabled_providers() -> set[str]:
    """Zero-marginal-cost CLI routes this node may use.

    "auto" detects subscription OAuth plus the approved free OpenCode model.
    An explicit list is honoured but cannot invent an unknown provider.
    """
    configured = settings.llm_providers.strip()
    if configured.lower() == "auto":
        detected = detect_providers()
        if not detected:
            raise NoSubscriptionAvailable(
                "no zero-marginal-cost LLM route found; sign in with Claude.ai "
                "(`claude`) or ChatGPT (`codex login`), or update `opencode` for "
                "its free DeepSeek model before starting a node"
            )
        return set(detected)
    names = {part.strip() for part in configured.split(",") if part.strip()}
    unknown = names - _KNOWN_PROVIDERS
    if unknown:
        raise ValueError(f"unknown LLM provider(s) in POPPER_LLM_PROVIDERS: {sorted(unknown)}")
    if not names:
        raise ValueError("POPPER_LLM_PROVIDERS cannot be empty")
    return names


def _for_this_node(role: str, targets: list[RouteTarget], allowed: set[str]) -> list[RouteTarget]:
    """Drop targets unavailable on this node, preserving fallback order."""
    kept = [target for target in targets if target.provider in allowed]
    if kept:
        return kept
    # The role named only providers this operator lacks. Fall back to their own
    # provider rather than failing the node: a single-provider operator must
    # still be able to run every role.
    substitute = _SINGLE_PROVIDER_FALLBACK.get(role)
    for provider in sorted(allowed):
        model = substitute.get(provider) if substitute else None
        if model:
            return [RouteTarget(provider, model)]
    raise ValueError(
        f"role {role!r} has no route available for providers {sorted(allowed)}"
    )


def _role_routes() -> dict[str, list[RouteTarget]]:
    backend = settings.llm_backend.lower()
    if backend == "cli":
        routes = {
            "hypothesis": parse_route("claude_cli:sonnet"),
            "hypothesis_strong": parse_route("claude_cli:sonnet"),
            "wildcard": parse_route("claude_cli:opus"),
            "synthesis": parse_route("claude_cli:haiku"),
            "synthesis_strong": parse_route("claude_cli:sonnet"),
            "audit": parse_route("claude_cli:opus"),
        }
    elif backend in {"subscriptions", "hybrid"}:  # hybrid: pre-release alias
        routes = {
            "hypothesis": parse_route(settings.llm_hypothesis_route),
            "hypothesis_strong": parse_route(settings.llm_hypothesis_strong_route),
            "wildcard": parse_route(settings.llm_wildcard_route),
            "synthesis": parse_route(settings.llm_synthesis_route),
            "synthesis_strong": parse_route(settings.llm_synthesis_strong_route),
            "audit": parse_route(settings.llm_audit_route),
        }
    else:
        raise ValueError("POPPER_LLM_BACKEND must be subscriptions or cli")
    allowed = enabled_providers()
    return {role: _for_this_node(role, targets, allowed) for role, targets in routes.items()}


def get_llm() -> LLMRouter:
    """Build a validated role router from environment-driven settings."""
    return LLMRouter(_role_routes(), policy=settings.llm_route_policy)


class StubLLM:
    """Deterministic canned responses for tests. Pops in order; records calls."""

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, system: str, prompt: str, model: str, max_tokens: int = 1024) -> str:
        self.calls.append({"system": system, "prompt": prompt, "model": model})
        return self.responses.pop(0)

    def supports_role(self, role: str) -> bool:
        return False
