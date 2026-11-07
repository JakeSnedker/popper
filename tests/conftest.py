"""Test-suite defaults that keep results independent of the operator's .env.

Popper reads real operator configuration at import time. On a configured node
that means POPPER_AUTH_REQUIRED=true, and every API test that does not
authenticate starts returning 401 — a failure caused by the developer's own
machine rather than by the code. Tests that are specifically about the auth
boundary set `auth_required` themselves; monkeypatch inside a test wins over
this fixture.
"""

from pathlib import Path

import pytest

_DATA = Path(__file__).resolve().parents[1] / "data"

# These modules load real US30 bars at import, and market data is not in Git.
collect_ignore = (
    []
    if (_DATA / "us30" / "US30_M5.parquet").exists() or (_DATA / "ftmo-us30-v1").exists()
    else [
        "test_forge_features.py",
        "test_forge_levels.py",
        "test_forge_resting_level_exits.py",
        "test_forge_wrappers.py",
    ]
)


@pytest.fixture(autouse=True)
def _api_auth_off(monkeypatch):
    monkeypatch.setattr("popper.security.auth.settings.auth_required", False, raising=False)


@pytest.fixture(autouse=True)
def _clear_endpoint_caches():
    """Drop the API's TTL response caches around every test.

    /api/map and /api/analytics are cached for minutes because they are the
    expensive reads that blew the database egress quota. That cache is
    process-local and outlives a test's swapped-in memory, so without this a
    test asserts on the PREVIOUS test's dashboard.
    """
    from popper.api import main

    cached = [fn for fn in vars(main).values() if hasattr(fn, "cache_clear")]
    for fn in cached:
        fn.cache_clear()
    yield
    for fn in cached:
        fn.cache_clear()
