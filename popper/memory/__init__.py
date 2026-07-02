"""Alpha Memory — the system's long-term record. Prevents re-exploration; powers novelty.

Phase 0 ships a SQLite implementation that runs with zero external dependencies.
Set POPPER_STORE=supabase (+ creds) to use the production Postgres store.
"""

from popper.config import settings
from popper.memory.base import AlphaMemory, embed_text
from popper.memory.embed import LocalEmbedder, get_embedder
from popper.memory.map import categorize, complete_scorecard
from popper.memory.sqlite_repo import SqliteMemory

_instance: AlphaMemory | None = None


def get_memory() -> AlphaMemory:
    """Return the configured alpha-memory backend (singleton)."""
    global _instance
    if _instance is not None:
        return _instance
    if settings.store == "supabase":
        from popper.memory.supabase_repo import SupabaseMemory

        _instance = SupabaseMemory()
    else:
        _instance = SqliteMemory(settings.db_path)
    return _instance


__all__ = [
    "AlphaMemory", "SqliteMemory", "get_memory",
    "get_embedder", "LocalEmbedder", "embed_text", "categorize", "complete_scorecard",
]
