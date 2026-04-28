"""Tests for core helpers added for the remote-routing PR (issue #63)."""
from __future__ import annotations

from pathlib import Path

from core import (
    BACKOFF_SCHEDULE,
    MAX_ATTEMPTS,
    MAX_SEEN_IDS,
    agent_dir,
    network_config_path,
    pending_dir,
    retry_sidecar_path,
    seen_ids_path,
)


class TestPendingDir:
    def test_under_agent_dir(self, fake_home):
        p = pending_dir("claude")
        assert p == agent_dir("claude") / "pending"
        # Sibling of inbox / inbox.tmp / trash — same parent.
        assert p.parent == agent_dir("claude")


class TestRetrySidecarPath:
    def test_appends_retry_suffix(self):
        f = Path("/tmp/01HX.json")
        assert retry_sidecar_path(f) == Path("/tmp/01HX.json.retry")


class TestSeenIdsPath:
    def test_cluster_wide(self, fake_home):
        # Single file under ~/.a8s/, not per-agent.
        p = seen_ids_path()
        assert p.parent == fake_home / ".a8s"
        assert p.name == "seen-ids"


class TestNetworkConfigPath:
    def test_under_a8s(self, fake_home):
        p = network_config_path()
        assert p == fake_home / ".a8s" / "network.json"


class TestBackoffConstants:
    def test_schedule_is_strictly_increasing(self):
        for a, b in zip(BACKOFF_SCHEDULE, BACKOFF_SCHEDULE[1:]):
            assert a < b

    def test_first_step_is_30s(self):
        assert BACKOFF_SCHEDULE[0] == 30

    def test_last_step_is_24h(self):
        assert BACKOFF_SCHEDULE[-1] == 86400

    def test_max_attempts_matches_schedule_length(self):
        assert MAX_ATTEMPTS == len(BACKOFF_SCHEDULE)


class TestSeenIdsCap:
    def test_cap_is_reasonable(self):
        # 26-char ULID + newline = 27 bytes per row; 10k rows ≈ 270 KiB.
        # Sanity: not zero, not absurd.
        assert 1000 <= MAX_SEEN_IDS <= 1_000_000
