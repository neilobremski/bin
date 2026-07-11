"""Verdict engine tests — plain-English health lines and dead-letter rollup."""
from __future__ import annotations

import time

import pytest

import state
import tasks
import verdict
from rig import load_rig_config
from roster import load_roster
from ulid import new as new_ulid

NODE = "acme"


@pytest.fixture
def roster(repo):
    return load_roster(repo / "ROSTER.md")


@pytest.fixture
def config(rig_config):
    return load_rig_config(rig_config)


def open_task(creator: str = "acme:neil", **fields) -> dict:
    task = tasks.ensure_task(NODE, new_ulid(), creator)
    task.update(fields)
    tasks.save_task(NODE, task)
    return task


def by_level(verdicts, level):
    return [v for v in verdicts if v.level == level]


def texts(verdicts):
    return "\n".join(v.text for v in verdicts)


class TestRollup:
    def test_routine_vs_signals(self):
        records = [
            {"reason": "task-closed"},
            {"reason": "task-closed"},
            {"reason": "hop-cut"},
            {"reason": "pair-repeat", "from": "a", "to": "b"},
            {"reason": "quota", "from": "a", "to": "b"},
        ]
        roll = verdict.rollup_dead_letters(records)
        assert roll.routine == {"task-closed": 2, "hop-cut": 1}
        assert roll.routine_total == 3
        assert set(roll.signals) == {"pair-repeat", "quota"}
        assert roll.signal_total == 2

    def test_empty(self):
        roll = verdict.rollup_dead_letters([])
        assert roll.routine_total == 0 and roll.signal_total == 0


class TestSeatVerdict:
    def test_unread_waits_on_you(self, r4t_home, roster, config):
        state.park_seat_message(NODE, "Neil", "acme:gerry", "look at this")
        verdicts = verdict.team_verdicts(NODE, roster, config)
        bad = by_level(verdicts, verdict.BAD)
        assert any("waiting on YOU" in v.text for v in bad)
        assert any("seat inbox" in (v.hint or "") for v in bad)

    def test_quiet_seat_is_ok(self, r4t_home, roster, config):
        verdicts = verdict.team_verdicts(NODE, roster, config)
        assert any("nothing waiting on you" in v.text for v in verdicts)

    def test_no_roster_skips_seat(self, r4t_home):
        verdicts = verdict.team_verdicts(NODE, None, None)
        assert "waiting on you" not in texts(verdicts)


class TestRunawayVerdict:
    def test_quiet_team_no_runaway(self, r4t_home, roster, config):
        verdicts = verdict.team_verdicts(NODE, roster, config)
        assert any("no runaway signs" in v.text for v in verdicts)

    def test_high_turn_rate_warns(self, r4t_home, roster, config):
        for _ in range(verdict.RUNAWAY_TURNS_PER_WINDOW):
            state.record_velocity(
                NODE, agent="phil", rig="junior-dev", task="01X",
                hop=1, duration_seconds=1.0, exit_code=0,
            )
        verdicts = verdict.team_verdicts(NODE, roster, config)
        warn = by_level(verdicts, verdict.WARN)
        assert any("hot:" in v.text for v in warn)
        assert not any("no runaway signs" in v.text for v in verdicts)

    def test_old_turns_do_not_count(self, r4t_home, roster, config):
        for _ in range(verdict.RUNAWAY_TURNS_PER_WINDOW):
            state.record_velocity(
                NODE, agent="phil", rig="junior-dev", task="01X",
                hop=1, duration_seconds=1.0, exit_code=0,
            )
        later = time.time() + verdict.RECENT_WINDOW_SECONDS + 60
        verdicts = verdict.team_verdicts(NODE, roster, config, now=later)
        assert any("no runaway signs" in v.text for v in verdicts)

    def test_hot_budget_warns(self, r4t_home, roster, config):
        task = open_task(used=0.8, budget=1.0)
        verdicts = verdict.team_verdicts(NODE, roster, config)
        warn = by_level(verdicts, verdict.WARN)
        assert any(task["id"] in v.text and "budget" in v.text for v in warn)


class TestMemberVerdicts:
    def test_all_healthy(self, r4t_home, roster, config):
        verdicts = verdict.team_verdicts(NODE, roster, config)
        assert any("member(s) healthy" in v.text for v in verdicts)

    def test_breaker_open_is_bad(self, r4t_home, roster, config):
        state.update_meta(
            NODE, "phil",
            consecutive_failures=config.breaker_cap,
            last_failure_at=state.utc_now(),
        )
        verdicts = verdict.team_verdicts(NODE, roster, config)
        bad = by_level(verdicts, verdict.BAD)
        assert any("Phil broken" in v.text for v in bad)
        assert not any("member(s) healthy" in v.text for v in verdicts)

    def test_muted_is_bad(self, r4t_home, roster, config):
        state.bucket_drain(NODE, "phil", config.bucket_max, config.bucket_max)
        verdicts = verdict.team_verdicts(NODE, roster, config)
        assert any("Phil muted" in v.text for v in by_level(verdicts, verdict.BAD))

    def test_nudged_member_is_stalled(self, r4t_home, roster, config):
        task = open_task(nudges={"phil": 1})
        verdicts = verdict.team_verdicts(NODE, roster, config)
        warn = by_level(verdicts, verdict.WARN)
        assert any(
            "Phil stalled" in v.text and task["id"] in v.text for v in warn
        )

    def test_nudge_cap_reached_says_so(self, r4t_home, roster, config):
        open_task(nudges={"phil": config.nudge_cap})
        verdicts = verdict.team_verdicts(NODE, roster, config)
        assert any("next silence closes the task" in v.text for v in verdicts)


class TestHopStarvation:
    def test_hop_cuts_on_open_task_warn(self, r4t_home, roster, config):
        task = open_task()
        for _ in range(3):
            state.record_dead_letter(
                NODE, reason="hop-cut", sender="acme:gerry", to="phil",
                task=task["id"], content="x",
            )
        verdicts = verdict.team_verdicts(NODE, roster, config)
        warn = by_level(verdicts, verdict.WARN)
        assert any(
            "ran out of hops" in v.text and task["id"] in v.text for v in warn
        )
        assert any("hop_limit" in (v.hint or "") for v in warn)

    def test_hop_cuts_on_closed_task_are_quiet(self, r4t_home, roster, config):
        task = open_task(status=tasks.STATUS_CLOSED)
        state.record_dead_letter(
            NODE, reason="hop-cut", sender="acme:gerry", to="phil",
            task=task["id"], content="x",
        )
        verdicts = verdict.team_verdicts(NODE, roster, config)
        assert "ran out of hops" not in texts(verdicts)


class TestSignalDeadLetters:
    def test_recent_signal_warns_with_gloss(self, r4t_home, roster, config):
        state.record_dead_letter(
            NODE, reason="pair-repeat", sender="acme:gerry", to="phil",
            task="01X", content="x",
        )
        verdicts = verdict.team_verdicts(NODE, roster, config)
        warn = by_level(verdicts, verdict.WARN)
        assert any(
            "pair-repeat" in v.text and "looping" in v.text for v in warn
        )

    def test_stale_signal_is_quiet(self, r4t_home, roster, config):
        state.record_dead_letter(
            NODE, reason="pair-repeat", sender="acme:gerry", to="phil",
            task="01X", content="x",
        )
        later = time.time() + verdict.SIGNAL_RECENT_SECONDS + 60
        verdicts = verdict.team_verdicts(NODE, roster, config, now=later)
        assert "pair-repeat" not in texts(verdicts)


def test_worst_level():
    v = verdict.Verdict
    assert verdict.worst_level([v("ok", "a")]) == "ok"
    assert verdict.worst_level([v("ok", "a"), v("warn", "b")]) == "warn"
    assert verdict.worst_level([v("warn", "a"), v("bad", "b")]) == "bad"
    assert verdict.worst_level([]) == "ok"
