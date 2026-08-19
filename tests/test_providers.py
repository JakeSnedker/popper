"""Operator provider parking: subtract from what the node detects, never strand it."""
import os
import pathlib
import tempfile

import pytest

from popper.node_operator import (
    NodePaths,
    read_disabled_providers,
    write_disabled_providers,
)


@pytest.fixture
def paths(monkeypatch):
    root = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setenv("POPPER_NODE_STATE_DIR", str(root / ".popper"))
    p = NodePaths.for_root(root)
    p.ensure()
    return p


def test_missing_file_parks_nothing(paths):
    assert read_disabled_providers(paths) == set()


def test_round_trip(paths):
    write_disabled_providers(paths, ["claude_cli"])
    assert read_disabled_providers(paths) == {"claude_cli"}
    write_disabled_providers(paths, [])
    assert read_disabled_providers(paths) == set()


def test_unknown_provider_rejected(paths):
    with pytest.raises(ValueError):
        write_disabled_providers(paths, ["gpt5_cli"])


def test_stale_name_in_file_is_inert(paths):
    paths.providers_file.write_text("claude_cli,retired_cli", encoding="ascii")
    assert read_disabled_providers(paths) == {"claude_cli"}


def _enabled(monkeypatch, paths, configured):
    monkeypatch.setenv("POPPER_LLM_PROVIDERS", configured)
    monkeypatch.setenv("POPPER_NODE_STATE_DIR", str(paths.state_dir))
    from popper.agents import llm
    from popper.config import settings
    monkeypatch.setattr(settings, "llm_providers", configured, raising=False)
    monkeypatch.setattr(llm, "_parked_providers",
                        lambda: read_disabled_providers(paths))
    return llm.enabled_providers()


def test_parked_provider_is_dropped(monkeypatch, paths):
    write_disabled_providers(paths, ["claude_cli"])
    got = _enabled(monkeypatch, paths, "claude_cli,codex_cli,opencode_cli")
    assert got == {"codex_cli", "opencode_cli"}


def test_parking_everything_does_not_strand_the_node(monkeypatch, paths):
    """The whole point of the switch is 'give me my machine'. Parking every
    provider must not leave a node that cannot resolve a single route."""
    write_disabled_providers(paths, ["claude_cli", "codex_cli", "opencode_cli"])
    got = _enabled(monkeypatch, paths, "claude_cli,codex_cli,opencode_cli")
    assert got == {"claude_cli", "codex_cli", "opencode_cli"}


def test_parking_cannot_invent_a_provider_the_node_lacks(monkeypatch, paths):
    write_disabled_providers(paths, ["codex_cli"])
    got = _enabled(monkeypatch, paths, "claude_cli")
    assert got == {"claude_cli"}
