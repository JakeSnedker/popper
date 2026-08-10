"""Environment-driven configuration. No hardcoded secrets."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POPPER_", env_file=".env", extra="ignore")

    # store: "sqlite" (default, runs now) or "supabase" (production alpha memory)
    store: str = "sqlite"
    db_path: str = "popper.db"

    # supabase (only needed when store == "supabase")
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_email: str = ""
    supabase_password: str = ""
    auth_required: bool = False

    # Node attribution. Production releases set these explicitly; local mode
    # falls back to the hostname, package version and canonical dataset manifest.
    operator_id: str = ""
    machine_id: str = ""
    release_id: str = ""
    dataset_id: str = ""
    # Canonical dataset is distributed separately from Git and may live on another drive.
    dataset_dir: str = "data/ftmo-us30-v1"

    # Zero-marginal-cost runtime: official Claude/ChatGPT subscription CLIs
    # plus OpenCode's approved free hosted DeepSeek model.
    llm_backend: str = "subscriptions"
    llm_route_policy: str = "round_robin"  # priority | round_robin
    # "auto" detects Claude/Codex subscription logins and the free OpenCode
    # DeepSeek route. Use an explicit list only to deliberately hold one back.
    llm_providers: str = "auto"
    llm_hypothesis_route: str = "claude_cli:sonnet,codex_cli:default,opencode_cli:deepseek"
    llm_hypothesis_strong_route: str = "codex_cli:default,claude_cli:sonnet"
    llm_wildcard_route: str = "codex_cli:default,opencode_cli:deepseek,claude_cli:opus"
    # DeepSeek sits LAST on the synthesis routes on purpose. Measured on the same
    # claim it produces a DSL-valid spec, but in 153s over two calls against 59s
    # in one for claude:haiku -- so as a primary it would cut throughput ~2.6x.
    # As a tail entry it costs nothing while the subscriptions are healthy and
    # keeps research moving once they hit their reserve, which is the whole point.
    llm_synthesis_route: str = "claude_cli:haiku,codex_cli:default,opencode_cli:deepseek"
    llm_synthesis_strong_route: str = "codex_cli:default,claude_cli:sonnet,opencode_cli:deepseek"
    llm_audit_route: str = "codex_cli:default,claude_cli:opus"
    # Exploit engine: seed each run with previously-killed but rescuable specs
    # (cost-killed and near-miss) loaded from shared memory. Without this the
    # exploit budget can only refine what the SAME run happened to kill.
    rescue_backlog: bool = True
    rescue_backlog_limit: int = 200
    rescue_backlog_band: float = 0.1
    # explore | balanced | exploit | auto. Auto explores while an LLM route is
    # available and runs one local rescue at a time during provider backoff.
    research_mode: str = "auto"
    auto_exploit_batch_size: int = 1
    exploit_max_evals: int = 24
    auto_exploit_max_evals: int = 1

    llm_batch_size: int = 10
    llm_provider_backoff_seconds: int = 300
    llm_rate_limit_backoff_seconds: int = 2700
    llm_survivor_audit: bool = True
    llm_fidelity_mode: str = "strict"  # strict | declared | off
    # Safety: `codex login status` must report ChatGPT auth. An API-key Codex
    # login is metered and is rejected unless this explicit opt-in is true.
    llm_allow_metered_codex_cli: bool = False
    # Claude reserve. The node stops calling claude_cli once it has spent this
    # many calls in the rolling window, leaving the rest of the Claude.ai
    # session limit for interactive Claude Code. Research continues on the other
    # providers rather than stopping. 0 disables the cap. The count is shared
    # across hunt processes via .popper/llm_budget.json.
    llm_claude_max_calls_per_window: int = 60
    llm_claude_reserve_window_hours: int = 5

    # default per-trade cost model — INFORMATIONAL, NON-GATING. The gauntlet
    # judges gross (frictionless) edge; these only feed the *_net raw_metrics
    # so a human/observability can see the drag.
    # point_value is the CALIBRATION knob: Jake sets it from the fidelity
    # result (MT5 'spread' is in points; point size differs by feed).
    # 0.012 = the A1 matched-pair calibration (entry delta +2.53pts = spread ->
    # pt~=0.011; rounded up, honest/conservative). Override via POPPER_POINT_VALUE.
    point_value: float = 0.012
    slippage_pts: float = 0.0
    commission_pts: float = 0.0

    # api
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


settings = Settings()
