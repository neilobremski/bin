from __future__ import annotations

import json

import state
from state import (
    AgentLock,
    append_history,
    count_tier_locks,
    history_path,
    live_locks,
    park_pending,
    list_pending,
    prune_stale_locks,
    read_history,
    record_velocity,
    team_dir,
)

NODE = "s1l"


class TestHistory:
    def test_append_creates(self, r4t_home):
        append_history(NODE, "Phil", "## turn 1\n\nhello")
        assert read_history(NODE, "phil").startswith("## turn 1")

    def test_appends_in_order(self, r4t_home):
        append_history(NODE, "phil", "## turn 1\n\na")
        append_history(NODE, "phil", "## turn 2\n\nb")
        text = read_history(NODE, "phil")
        assert text.index("turn 1") < text.index("turn 2")

    def test_truncates_oldest_first_at_boundaries(self, r4t_home):
        for i in range(10):
            append_history(NODE, "phil", f"## turn {i}\n\n{'x' * 300}", max_bytes=1000)
        text = read_history(NODE, "phil")
        assert len(text.encode("utf-8")) <= 1000
        assert "turn 9" in text
        assert "turn 0" not in text
        assert text.startswith("## ")

    def test_single_oversize_entry_survives(self, r4t_home):
        append_history(NODE, "phil", "## big\n\n" + "y" * 2000, max_bytes=1000)
        assert "## big" in read_history(NODE, "phil")

    def test_home_relocates_with_env(self, r4t_home):
        append_history(NODE, "phil", "## x\n\nhi")
        assert history_path(NODE, "phil").is_relative_to(r4t_home)


class TestLocks:
    def test_acquire_release(self, r4t_home):
        lock = AgentLock(NODE, "phil")
        assert lock.acquire("junior-dev")
        assert not AgentLock(NODE, "phil").acquire("junior-dev")
        lock.release()
        assert AgentLock(NODE, "phil").acquire("junior-dev")

    def test_stale_lock_is_stolen(self, r4t_home):
        path = state.agent_dir(NODE, "phil") / ".lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": 99999999, "tier": "t"}), encoding="utf-8")
        assert AgentLock(NODE, "phil").acquire("junior-dev")

    def test_live_locks_and_tier_count(self, r4t_home):
        AgentLock(NODE, "phil").acquire("junior-dev")
        AgentLock(NODE, "marcus").acquire("junior-dev")
        AgentLock(NODE, "gerry").acquire("leader")
        assert len(live_locks(NODE)) == 3
        assert count_tier_locks(NODE, "junior-dev") == 2
        assert count_tier_locks(NODE, "LEADER") == 1

    def test_prune_stale_locks(self, r4t_home):
        AgentLock(NODE, "gerry").acquire("leader")
        dead = state.agent_dir(NODE, "phil") / ".lock"
        dead.parent.mkdir(parents=True, exist_ok=True)
        dead.write_text(json.dumps({"pid": 99999999, "tier": "t"}), encoding="utf-8")
        corrupt = state.agent_dir(NODE, "zoe") / ".lock"
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_text("not json", encoding="utf-8")
        assert prune_stale_locks(NODE) == 2
        assert not dead.exists()
        assert not corrupt.exists()
        assert len(live_locks(NODE)) == 1


class TestPending:
    def test_park_and_list_fifo(self, r4t_home):
        first = park_pending(NODE, {"from": "a", "to": "s1l:phil", "body": "1"})
        second = park_pending(NODE, {"from": "b", "to": "s1l:phil", "body": "2"})
        listed = list_pending(NODE)
        assert listed == sorted([first, second])


class TestVelocity:
    def test_rows_and_header(self, r4t_home):
        record_velocity(
            NODE,
            agent="phil",
            tier="junior-dev",
            task="01ABC",
            hop=1,
            duration_seconds=1.234,
            exit_code=0,
        )
        record_velocity(
            NODE,
            agent="gerry",
            tier="leader",
            task="01DEF",
            hop=0,
            duration_seconds=2.0,
            exit_code=1,
        )
        lines = (team_dir(NODE) / "velocity.csv").read_text().splitlines()
        assert lines[0] == state.VELOCITY_HEADER.strip()
        assert len(lines) == 3
        assert "phil,junior-dev,01ABC,1,1.23,0" in lines[1]

    def test_field_quoting(self, r4t_home):
        record_velocity(
            NODE,
            agent='we,"ird',
            tier="t",
            task="x",
            hop=0,
            duration_seconds=0,
            exit_code=0,
        )
        lines = (team_dir(NODE) / "velocity.csv").read_text().splitlines()
        assert '"we,""ird"' in lines[1]
