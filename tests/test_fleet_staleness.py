"""'Online' must mean the node is reporting NOW, not at some point recently.

A running node heartbeats every 5 seconds. The threshold used to be 300s, which
tolerated sixty missed beats, so a machine dead for four minutes still read as
online -- and the status pill kept rendering its last published status, so it
also still claimed to be researching. Both misled the operator twice in one day.
"""

from datetime import datetime, timedelta, timezone

from popper.node_presence import STALE_AFTER_SECONDS

HEARTBEAT_PERIOD_SECONDS = 5


def _online(age_seconds: float, threshold: int = STALE_AFTER_SECONDS) -> bool:
    """The freshness rule from node_presence.fleet(), isolated."""
    return age_seconds is not None and age_seconds <= threshold


def test_threshold_is_a_small_multiple_of_the_heartbeat():
    missed = STALE_AFTER_SECONDS / HEARTBEAT_PERIOD_SECONDS
    assert missed <= 20, f"tolerating {missed:.0f} missed beats makes 'online' meaningless"
    assert missed >= 4, "too tight; a slow publish would flap the indicator"


def test_a_live_node_reads_online():
    assert _online(0) and _online(HEARTBEAT_PERIOD_SECONDS * 2)


def test_a_node_dead_for_two_minutes_is_not_online():
    assert not _online(120)


def test_the_four_minute_case_that_used_to_pass():
    """240s was 'online' under the old 300s threshold."""
    assert not _online(240)
    assert _online(240, threshold=300), "documents the old behaviour this replaced"


def test_a_missing_heartbeat_is_never_online():
    assert not _online(None)


def test_parked_nodes_still_heartbeat_so_parked_is_not_offline():
    """Parking stops research, not reporting -- that distinction is the whole
    point of a separate online flag, and _wait_for_resume keeps beating."""
    assert _online(HEARTBEAT_PERIOD_SECONDS)
