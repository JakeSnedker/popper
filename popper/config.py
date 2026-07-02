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

    # api
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


settings = Settings()
