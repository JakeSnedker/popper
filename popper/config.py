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

    # agents (Phase 6)
    anthropic_api_key: str = ""
    # LLM backend: "api" (metered Anthropic API, low-latency) or "cli" ($0 via
    # `claude -p` on the claude.ai subscription — for unattended/cron runs).
    llm_backend: str = "api"

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
