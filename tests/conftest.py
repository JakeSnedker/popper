"""Test-suite defaults that keep results independent of the operator's .env.

Popper reads real operator configuration at import time. On a configured node
that means POPPER_AUTH_REQUIRED=true, and every API test that does not
authenticate starts returning 401 — a failure caused by the developer's own
machine rather than by the code. Tests that are specifically about the auth
boundary set `auth_required` themselves; monkeypatch inside a test wins over
this fixture.
"""

import pytest


@pytest.fixture(autouse=True)
def _api_auth_off(monkeypatch):
    monkeypatch.setattr("popper.security.auth.settings.auth_required", False, raising=False)
