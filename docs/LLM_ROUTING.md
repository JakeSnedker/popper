# Popper LLM routing

## Goal

Spend strong-model capacity only where it changes research quality. The LLM proposes
and compiles candidates; deterministic code still decides novelty, DSL validity,
backtest results, falsification, optimization, and portfolio admission.

## Runtime decision tree

```text
Hypothesis queue empty
  -> one strong creative call generates up to 5 blind hypotheses
  -> deterministic novelty gate screens each separately
  -> cheap Synthesis model compiles the accepted hypothesis
  -> deterministic DSL + hypothesis-fidelity validation
       pass -> gauntlet
       fail -> one strong-model repair
  -> deterministic falsification verdict
       kill -> no more LLM spend
       survive/promote -> one best-model semantic fidelity audit (advisory)
```

Provider failures never enter content repair:

```text
provider quota/429/5xx/CLI failure
  -> open that provider circuit
  -> fail over to the next configured provider
  -> all circuits open: stop the run immediately with retry_after
  -> outer worker sleeps before starting another process
```

## Zero-marginal-cost providers

`claude_cli` runs the official `claude -p` command using Claude subscription OAuth.
It strips Anthropic API keys, replaces the default system prompt, disables tools and
session persistence, uses safe mode, and requests JSON telemetry.

`codex_cli` runs the official `codex exec` command using the signed-in ChatGPT
subscription. It executes ephemerally from the OS temp directory, ignores user/project
rules, uses a read-only sandbox, and emits JSONL telemetry.

`opencode_cli` runs only `opencode/deepseek-v4-flash-free`. Popper gives it an
isolated temporary config/data/cache/state root, a no-tools agent, no parent project
instructions, and no Anthropic, OpenAI, NVIDIA, DeepSeek, GLM or OpenRouter API keys.
The temporary session is deleted after each call. Paid OpenCode models fail closed.

No browser cookies, web-session tokens, or unofficial authentication are used.
A ChatGPT subscription is used only through the official Codex CLI. Before every
Codex process lifecycle, Popper checks
`codex login status`; an API-key login is rejected unless
`POPPER_LLM_ALLOW_METERED_CODEX_CLI=true` is explicitly set.

Check this machine before a run:

```powershell
claude auth status
codex login status
opencode models opencode
```

Claude should report `claude.ai`; Codex must report ChatGPT rather than an API key;
and OpenCode must list `opencode/deepseek-v4-flash-free`.

## Backends

| `POPPER_LLM_BACKEND` | Behavior |
|---|---|
| `subscriptions` | Role routes across Claude.ai, ChatGPT/Codex, and free OpenCode DeepSeek (legacy backend name) |
| `cli` | Claude subscription only; compatibility/fallback mode |

## Route syntax

A route is an ordered comma-separated list:

```text
provider:model,provider:model
```

Supported providers:

- `claude_cli`
- `codex_cli`
- `opencode_cli` (alias `deepseek` resolves only to `opencode/deepseek-v4-flash-free`)

Local LLMs and metered APIs are deliberately not selectable through OpenCode. Unknown
providers and non-approved OpenCode models fail before a process is launched.

The defaults rotate hypothesis generation across Claude, Codex, and DeepSeek when all
three are available. Synthesis and strong/audit routes retain their existing provider
ordering. A persistent Claude reserve stops node use after 60 calls in a rolling five-hour
window, while Codex/DeepSeek remain available.
See `.env.example` for every route.

## Telemetry

Each real provider call records:

- logical role, provider, and concrete model
- status and failover position
- latency
- input/output/cache tokens when the provider reports them
- provider-reported cost equivalent when available (not necessarily subscription billing)
- rate-limit state and retry delay

Telemetry is attached to `research_events`, aggregated into `RunSummary.llm_usage`,
returned by `POST /api/research`, and printed by `scripts/run_hunt.py`.

CLIs may report token counts differently over time. A missing count is stored as zero
rather than estimated.

## Model benchmarking

Never infer model quality from parser tests. Run the no-persistence canary:

```powershell
.venv\Scripts\python.exe scripts\benchmark_llm_routes.py `
  --route claude=claude_cli:sonnet `
  --route openai=codex_cli:default `
  --route deepseek=opencode_cli:deepseek `
  --task hypothesis --samples 3 --batch-size 5 `
  --output route-benchmark.json
```

For Synthesis:

```powershell
.venv\Scripts\python.exe scripts\benchmark_llm_routes.py `
  --route haiku=claude_cli:haiku `
  --route codex=codex_cli:default `
  --task synthesis --samples 3
```

Compare valid unique hypotheses or DSL/fidelity passes per reported token and minute.
Do not route all generation to a cheaper model until the canary demonstrates useful
output parity.

## Novelty migration

The legacy local embedding remains the default so an existing map never silently mixes
vector spaces. `local_semantic` is a zero-cost domain-normalized replacement.

```powershell
.venv\Scripts\python.exe scripts\reembed_hypotheses.py --backend local_semantic --dry-run
.venv\Scripts\python.exe scripts\reembed_hypotheses.py --backend local_semantic
```

A mixed embedding dimension stops research with an explicit migration instruction.
Supabase hypothesis reads are paginated so novelty sees the complete map past 1,000 rows.
