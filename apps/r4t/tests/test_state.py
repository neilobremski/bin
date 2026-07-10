from __future__ import annotations

import json
import time

import state
from state import (
    AgentLock,
    append_history,
    bucket_drain,
    bucket_earn,
    bucket_level,
    bucket_muted,
    count_tier_locks,
    history_path,
    list_dead_letters,
    list_pending,
    live_locks,
    park_pending,
    prepare_staging,
    prune_stale_locks,
    read_history,
    record_dead_letter,
    record_velocity,
    staged_envelopes,
    suppression_check,
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


class TestDeadLetters:
    def test_record_and_list(self, r4t_home):
        record_dead_letter(
            NODE, reason="quota", sender="s1l:phil", to="gerry",
            task="01ABC", content="too chatty",
        )
        record_dead_letter(
            NODE, reason="pair-repeat", sender="s1l:phil", to="gerry",
            task="01ABC", content="again", count=3,
        )
        records = list_dead_letters(NODE)
        assert len(records) == 2
        by_reason = {r["reason"]: r for r in records}
        assert by_reason["quota"]["from"] == "s1l:phil"
        assert by_reason["quota"]["count"] == 1
        assert by_reason["pair-repeat"]["count"] == 3
        assert all(r["time"] for r in records)

    def test_content_capped(self, r4t_home):
        record_dead_letter(
            NODE, reason="quota", sender="a", to="b", task="t", content="x" * 5000,
        )
        assert len(list_dead_letters(NODE)[0]["content"]) == 2000


class TestSuppression:
    def test_first_passes_repeats_counted(self, r4t_home):
        suppressed, count = suppression_check(NODE, "pair:abc", 600)
        assert not suppressed and count == 1
        suppressed, count = suppression_check(NODE, "pair:abc", 600)
        assert suppressed and count == 2
        suppressed, count = suppression_check(NODE, "pair:abc", 600)
        assert suppressed and count == 3

    def test_different_keys_independent(self, r4t_home):
        assert suppression_check(NODE, "pair:one", 600) == (False, 1)
        assert suppression_check(NODE, "pair:two", 600) == (False, 1)

    def test_window_expiry(self, r4t_home):
        assert suppression_check(NODE, "pair:x", 0.05) == (False, 1)
        time.sleep(0.1)
        assert suppression_check(NODE, "pair:x", 0.05) == (False, 1)


class TestBuckets:
    def test_starts_full(self, r4t_home):
        assert bucket_level(NODE, "phil", 8.0) == 8.0

    def test_drain_and_floor_at_zero(self, r4t_home):
        assert bucket_drain(NODE, "phil", 3.0, 8.0) == 5.0
        assert bucket_drain(NODE, "phil", 100.0, 8.0) == 0.0

    def test_earn_caps_at_max(self, r4t_home):
        bucket_drain(NODE, "phil", 1.0, 8.0)
        assert bucket_earn(NODE, "phil", 0.1, 8.0) == 7.1
        for _ in range(20):
            bucket_earn(NODE, "phil", 0.1, 8.0)
        assert bucket_level(NODE, "phil", 8.0) == 8.0

    def test_muted_below_half(self):
        assert not bucket_muted(4.0, 8.0)
        assert bucket_muted(3.9, 8.0)


class TestStaging:
    def test_prepare_wipes_leftovers(self, r4t_home):
        d = prepare_staging(NODE, "phil")
        (d / "stale.json").write_text("{}", encoding="utf-8")
        d2 = prepare_staging(NODE, "phil")
        assert d2 == d
        assert not list(d.iterdir())

    def test_staged_envelopes_sorted_json_only(self, r4t_home):
        d = prepare_staging(NODE, "phil")
        (d / "002.json").write_text("{}", encoding="utf-8")
        (d / "001.json").write_text("{}", encoding="utf-8")
        (d / "ignore.txt").write_text("", encoding="utf-8")
        (d / "001").mkdir()
        names = [p.name for p in staged_envelopes(NODE, "phil")]
        assert names == ["001.json", "002.json"]
