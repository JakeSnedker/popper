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

    # Subscription-only runtime. Each operator authenticates the official
    # Claude and Codex CLIs with their own Claude.ai / ChatGPT account.
    llm_backend: str = "subscriptions"
    llm_route_policy: str = "round_robin"  # priority | round_robin
    llm_hypothesis_route: str = "claude_cli:sonnet,codex_cli:default"
    llm_hypothesis_strong_route: str = "codex_cli:default,claude_cli:sonnet"
    llm_wildcard_route: str = "codex_cli:default,claude_cli:opus"
    llm_synthesis_route: str = "claude_cli:haiku,codex_cli:default"
    llm_synthesis_strong_route: str = "codex_cli:default,claude_cli:sonnet"
    llm_audit_route: str = "codex_cli:default,claude_cli:opus"
    llm_batch_size: int = 10
    llm_provider_backoff_seconds: int = 300
    llm_rate_limit_backoff_seconds: int = 2700
    llm_survivor_audit: bool = True
    llm_fidelity_mode: str = "strict"  # strict | declared | off
    # Safety: `codex login status` must report ChatGPT auth. An API-key Codex
    # login is metered and is rejected unless this explicit opt-in is true.
    llm_allow_metered_codex_cli: bool = False

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
