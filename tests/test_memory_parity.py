"""Offline drift guard: SupabaseMemory must expose every public method the app
uses on SqliteMemory. This would have caught the missing add_research_event /
add_feature_gap that crashed real Supabase runs. No creds — imports the class,
never instantiates (the supabase client import lives in __init__)."""
from popper.memory.sqlite_repo import SqliteMemory
from popper.memory.supabase_repo import SupabaseMemory


def _public(cls):
    return {n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n))}


def test_supabase_covers_sqlite_surface():
    missing = _public(SqliteMemory) - _public(SupabaseMemory)
    assert not missing, f"SupabaseMemory missing methods used via the AlphaMemory API: {missing}"
