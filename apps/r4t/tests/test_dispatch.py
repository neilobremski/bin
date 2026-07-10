from __future__ import annotations

import json
import sys
import textwrap

import state
import tasks
from dispatch import (
    DEAD,
    DEFERRED,
    RAN,
    SYNTHESIS,
    _handle,
    drain,
    drain_until_quiet,
    handle_message,
    run_harness,
    run_idle,
    split_recipient,
)
from harness import Tier
from r4t import main as r4t_main
from ulid import new as new_ulid

NODE = "s1l"


def harness_calls(fake_harness):
    _script, out = fake_harness
    return sorted(out.iterdir())


def read_prompt(path):
    return path.read_text(encoding="utf-8")


def outbox_envelopes(repo):
    d = repo / ".outbox"
    if not d.is_dir():
        return []
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(d.glob("*.json"))
    ]


def dead_reasons():
    return sorted(r["reason"] for r in state.list_dead_letters(NODE))


class TestSplitRecipient:
    def test_sub_address(self):
        assert split_recipient("s1l:phil") == ("s1l", "phil")

    def test_bare(self):
        assert split_recipient("s1l") == ("s1l", "")

    def test_first_colon_only(self):
        assert split_recipient("s1l:team:ops") == ("s1l", "team:ops")


class TestDispatchEndToEnd:
    def test_member_turn_runs_fake_harness(self, ctx, tells, fake_harness):
        sent, _ = tells
        assert _handle(ctx, "gerry", "s1l:phil", "review the ECS payload") == RAN
        calls = harness_calls(fake_harness)
        assert len(calls) == 1
        prompt = read_prompt(calls[0])
        assert "You are Phil" in prompt
        assert "Grumpy, cynical veteran" in prompt
        assert "From: gerry" in prompt
        assert "review the ECS payload" in prompt
        assert "tell s1l:gerry" in prompt
        assert "Neil (Human, tell neil)" in prompt
        assert sent == []  # silence on success — no auto-ack

    def test_prompt_carries_actor_doctrine_not_headers(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "s1l:phil", "hi")
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert "Never wait for a reply inside a turn" in prompt
        assert "END your turn" in prompt
        assert "tell --sync" in prompt
        assert "silence is fine" in prompt
        assert "[r4t task=" not in prompt  # headers are stamped mechanically

    def test_new_task_ledger_created(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "s1l:phil", "hi")
        listing = tasks.list_tasks(NODE)
        assert len(listing) == 1
        task = listing[0]
        assert task["creator"] == "gerry"
        assert task["turns"] == 1

    def test_incoming_header_adopted_and_stripped(self, ctx, fake_harness):
        task_id = new_ulid()
        header = tasks.format_header(task_id, 1, auto=True)
        handle_message(ctx, "gerry", "s1l:gerry", f"{header} continue please")
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert header not in prompt
        assert "## Incoming message\nFrom: gerry\n\ncontinue please" in prompt
        assert tasks.load_task(NODE, task_id)["turns"] == 1

    def test_bare_node_goes_to_leader(self, ctx, fake_harness):
        handle_message(ctx, "neil", "s1l", "status update please")
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert "You are Gerry" in prompt

    def test_history_holds_inbound_message_not_stdout(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "s1l:phil", "first job")
        history = state.read_history(NODE, "phil")
        assert "from gerry" in history
        assert "first job" in history
        assert "fake harness ran" not in history  # stdout goes to the log only

    def test_conversation_history_fed_back_into_prompt(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "s1l:phil", "first job")
        handle_message(ctx, "marcus", "s1l:phil", "second job")
        prompt = read_prompt(harness_calls(fake_harness)[1])
        assert "## Your conversation so far" in prompt
        assert "from gerry" in prompt
        assert "first job" in prompt

    def test_velocity_recorded(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "s1l:phil", "job")
        text = (state.team_dir(NODE) / "velocity.csv").read_text()
        assert "phil,junior-dev," in text

    def test_transcript_logged(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "s1l:phil", "job")
        logs = list((state.team_dir(NODE) / "log").glob("*.md"))
        assert len(logs) == 1
        text = logs[0].read_text(encoding="utf-8")
        assert "dispatch gerry -> Phil" in text
        assert "### Output" in text


class TestDispatchRejections:
    def test_unknown_member(self, ctx, tells, fake_harness):
        sent, _ = tells
        handle_message(ctx, "gerry", "s1l:nobody", "hi")
        assert not harness_calls(fake_harness)
        assert len(sent) == 1
        agent, body = sent[0]
        assert agent == "gerry"
        assert "nobody" in body and "Gerry" in body and "Phil" in body

    def test_human_never_dispatched(self, ctx, tells, fake_harness):
        sent, _ = tells
        handle_message(ctx, "gerry", "s1l:neil", "hi")
        assert not harness_calls(fake_harness)
        assert "Human" in sent[0][1]
        assert "tell neil" in sent[0][1]

    def test_malformed_member_disabled_with_error(self, ctx, tells, fake_harness):
        sent, _ = tells
        handle_message(ctx, "gerry", "s1l:broken", "hi")
        assert not harness_calls(fake_harness)
        assert "disabled" in sent[0][1]
        assert "Status" in sent[0][1]

    def test_unknown_tier_fails_closed(self, ctx, repo, tells, fake_harness):
        (repo / "ROSTER.md").write_text(
            "### Ghost\n- **Status:** AI\n- **Harness:** unconfigured-tier\n"
            "- **Leader:** yes\n",
            encoding="utf-8",
        )
        sent, _ = tells
        handle_message(ctx, "gerry", "s1l:ghost", "hi")
        assert not harness_calls(fake_harness)
        assert "not found" in sent[0][1]
        assert "fail closed" in sent[0][1]

    def test_missing_roster(self, ctx, repo, tells, fake_harness):
        (repo / "ROSTER.md").unlink()
        sent, _ = tells
        handle_message(ctx, "gerry", "s1l:phil", "hi")
        assert not harness_calls(fake_harness)
        assert "roster not found" in sent[0][1]

    def test_no_leader_for_bare_node(self, ctx, repo, tells, fake_harness):
        (repo / "ROSTER.md").write_text(
            "### Solo\n- **Status:** AI\n- **Harness:** junior-dev\n",
            encoding="utf-8",
        )
        sent, _ = tells
        handle_message(ctx, "gerry", "s1l", "hi")
        assert not harness_calls(fake_harness)
        assert "no leader" in sent[0][1]


class TestPins:
    def test_pin_overrides_roster_tier(self, ctx, repo, fake_harness):
        (repo / "ROSTER.md").write_text(
            "### Gerry\n- **Status:** AI\n- **Harness:** junior-dev\n"
            "- **Leader:** yes\n",
            encoding="utf-8",
        )
        handle_message(ctx, "neil", "s1l:gerry", "hi")
        text = (state.team_dir(NODE) / "velocity.csv").read_text()
        assert "gerry,leader," in text  # pinned tier, not roster's junior-dev


class TestStagingRelease:
    def test_external_release_stamps_header_and_class(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "neil")
        monkeypatch.setenv("CHATTY_BODY", "the fix is deployed")
        assert _handle(chatty_ctx, "gerry", "s1l:phil", "deploy the fix") == RAN
        envelopes = outbox_envelopes(repo)
        assert len(envelopes) == 1
        envelope = envelopes[0]
        assert envelope["to"] == "neil"
        assert envelope["x_r4t_class"] == "auto"
        task = tasks.list_tasks(NODE)[0]
        task_id, hop, auto, body = tasks.parse_header(envelope["content"])
        assert task_id == task["id"]
        assert hop == 1
        assert auto
        assert body == "the fix is deployed"
        assert not state.staging_dir(NODE, "phil").exists()

    def test_outbound_attributed_to_history(self, chatty_ctx, chatty_harness, monkeypatch):
        monkeypatch.setenv("CHATTY_TO", "neil")
        monkeypatch.setenv("CHATTY_BODY", "status: done")
        _handle(chatty_ctx, "gerry", "s1l:phil", "report status")
        history = state.read_history(NODE, "phil")
        assert "to neil" in history
        assert "status: done" in history

    def test_quota_overflow_dead_letters_and_drains_bucket(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "neil")
        monkeypatch.setenv("CHATTY_SENDS", "4")  # max_sends_per_turn is 2
        _handle(chatty_ctx, "gerry", "s1l:phil", "fan out")
        assert len(outbox_envelopes(repo)) == 2
        assert dead_reasons() == ["quota", "quota"]
        assert state.bucket_level(NODE, "phil", 8.0) == 6.0

    def test_intra_team_release_feeds_pending_and_drains(
        self, chatty_ctx, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "s1l:gerry")
        monkeypatch.setenv("CHATTY_BODY", "please review my patch")
        assert _handle(chatty_ctx, "neil", "s1l:phil", "do the work") == RAN
        assert len(state.list_pending(NODE)) == 1

        monkeypatch.setenv("CHATTY_SENDS", "0")
        assert drain_until_quiet(chatty_ctx) == 1
        _script, out = chatty_harness
        prompts = [read_prompt(p) for p in sorted(out.iterdir())]
        assert len(prompts) == 2
        assert "You are Gerry" in prompts[1]
        assert "From: s1l:phil" in prompts[1]
        assert "please review my patch" in prompts[1]
        task = tasks.list_tasks(NODE)[0]
        assert task["turns"] == 2  # same task across the delegation hop

    def test_clean_turn_earns_bucket_back(self, chatty_ctx, chatty_harness, monkeypatch):
        state.bucket_drain(NODE, "phil", 1.0, 8.0)
        monkeypatch.setenv("CHATTY_TO", "neil")
        _handle(chatty_ctx, "gerry", "s1l:phil", "one clean job")
        assert state.bucket_level(NODE, "phil", 8.0) == 7.1


class TestPairSuppression:
    def test_repeat_within_window_dead_letters(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "neil")
        monkeypatch.setenv("CHATTY_BODY", "Deploy the fix")
        _handle(chatty_ctx, "gerry", "s1l:phil", "job one")
        _handle(chatty_ctx, "gerry", "s1l:phil", "job two")
        assert len(outbox_envelopes(repo)) == 1
        assert dead_reasons() == ["pair-repeat"]
        record = state.list_dead_letters(NODE)[0]
        assert record["count"] == 2
        assert record["from"] == "s1l:phil"
        assert record["to"] == "neil"
        assert state.bucket_level(NODE, "phil", 8.0) == 7.0

    def test_different_content_passes(self, chatty_ctx, repo, chatty_harness, monkeypatch):
        monkeypatch.setenv("CHATTY_TO", "neil")
        monkeypatch.setenv("CHATTY_BODY", "unique {i}")
        _handle(chatty_ctx, "gerry", "s1l:phil", "job one")
        monkeypatch.setenv("CHATTY_BODY", "another thing entirely")
        _handle(chatty_ctx, "gerry", "s1l:phil", "job two")
        assert len(outbox_envelopes(repo)) == 2
        assert dead_reasons() == []

    def test_inbound_auto_repeat_suppressed(self, ctx, fake_harness):
        header = tasks.format_header(new_ulid(), 1, auto=True)
        assert _handle(ctx, "otherbot", "s1l:phil", f"{header} same ping") == RAN
        assert _handle(ctx, "otherbot", "s1l:phil", f"{header} same ping") == DEAD
        assert "pair-repeat" in dead_reasons()
        assert len(harness_calls(fake_harness)) == 1


class TestBulkClassMarking:
    def test_bulk_triggered_turn_posts_to_room_once_per_window(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "chatroom")
        monkeypatch.setenv("CHATTY_BODY", "posting update alpha")
        assert _handle(chatty_ctx, "chatroom", "s1l:phil", "#dev room says A") == RAN
        monkeypatch.setenv("CHATTY_BODY", "posting update beta")
        assert _handle(chatty_ctx, "chatroom", "s1l:phil", "#dev room says B") == RAN
        posts = [e for e in outbox_envelopes(repo) if e["to"] == "chatroom"]
        assert len(posts) == 1
        assert "bulk-window" in dead_reasons()

    def test_nonbulk_turn_may_post_to_room(self, chatty_ctx, repo, chatty_harness, monkeypatch):
        monkeypatch.setenv("CHATTY_TO", "chatroom")
        monkeypatch.setenv("CHATTY_BODY", "posting update alpha")
        _handle(chatty_ctx, "neil", "s1l:phil", "share this in the room")
        monkeypatch.setenv("CHATTY_BODY", "posting update beta")
        _handle(chatty_ctx, "neil", "s1l:phil", "share more in the room")
        posts = [e for e in outbox_envelopes(repo) if e["to"] == "chatroom"]
        assert len(posts) == 2


class TestBucketMute:
    def test_below_floor_records_history_without_running(self, ctx, fake_harness):
        state.bucket_drain(NODE, "phil", 4.5, 8.0)  # 3.5 < floor 4.0
        assert _handle(ctx, "gerry", "s1l:phil", "are you there?") == DEAD
        assert not harness_calls(fake_harness)
        assert "are you there?" in state.read_history(NODE, "phil")
        assert "bucket-muted" in dead_reasons()

    def test_recovers_autonomously_via_earn(self, ctx, fake_harness):
        state.bucket_drain(NODE, "phil", 4.5, 8.0)
        for i in range(5):  # each muted inbound earns 0.1 back
            assert _handle(ctx, "gerry", "s1l:phil", f"ping {i}") == DEAD
        assert state.bucket_level(NODE, "phil", 8.0) == 4.0
        assert _handle(ctx, "gerry", "s1l:phil", "back to work") == RAN
        assert len(harness_calls(fake_harness)) == 1


class TestForcedSynthesis:
    def test_budget_exhaustion_runs_one_leader_turn_and_closes(
        self, ctx, tells, fake_harness
    ):
        sent, _ = tells
        task_id = new_ulid()
        header = tasks.format_header(task_id, 0, auto=True)
        # junior-dev max_turns_per_task=2 -> each turn costs 0.5
        assert _handle(ctx, "gerry", f"{NODE}:phil", f"{header} one") == RAN
        assert _handle(ctx, "gerry", f"{NODE}:phil", f"{header} two") == RAN
        assert _handle(ctx, "gerry", f"{NODE}:phil", f"{header} three") == SYNTHESIS

        calls = harness_calls(fake_harness)
        assert len(calls) == 3
        prompt = read_prompt(calls[2])
        assert "You are Gerry" in prompt
        assert "Respond NOW to gerry" in prompt
        assert "Do not delegate" in prompt

        task = tasks.load_task(NODE, task_id)
        assert task["status"] == tasks.STATUS_CLOSED
        assert task["synthesized"]
        assert task["turns"] == 2  # the synthesis turn is not charged
        assert "budget-exhausted" in dead_reasons()
        assert sent == []  # the leader answers through its own sends, not r4t

    def test_closed_task_messages_dead_letter(self, ctx, fake_harness):
        task_id = new_ulid()
        header = tasks.format_header(task_id, 0, auto=True)
        for body in ("one", "two", "three"):
            _handle(ctx, "gerry", f"{NODE}:phil", f"{header} {body}")
        assert _handle(ctx, "gerry", f"{NODE}:phil", f"{header} four") == DEAD
        assert "task-closed" in dead_reasons()
        assert len(harness_calls(fake_harness)) == 3  # no further turns

    def test_synthesis_runs_once_per_task(self, ctx, repo, fake_harness):
        (repo / "ROSTER.md").write_text(
            "### Phil\n- **Status:** AI\n- **Harness:** junior-dev\n",
            encoding="utf-8",
        )
        task_id = new_ulid()
        header = tasks.format_header(task_id, 0, auto=True)
        for body in ("one", "two", "three"):
            _handle(ctx, "gerry", f"{NODE}:phil", f"{header} {body}")
        # No leader in this roster: synthesis is skipped but the task closes.
        task = tasks.load_task(NODE, task_id)
        assert task["status"] == tasks.STATUS_CLOSED
        assert len(harness_calls(fake_harness)) == 2


class TestDeliberateDecision:
    def test_non_auto_header_resets_budget(self, ctx, fake_harness):
        task_id = new_ulid()
        auto_header = tasks.format_header(task_id, 0, auto=True)
        _handle(ctx, "gerry", f"{NODE}:phil", f"{auto_header} one")
        _handle(ctx, "gerry", f"{NODE}:phil", f"{auto_header} two")
        assert tasks.load_task(NODE, task_id)["used"] == 1.0

        human_header = tasks.format_header(task_id, 0)
        assert _handle(ctx, "neil", f"{NODE}:phil", f"{human_header} keep going") == RAN
        task = tasks.load_task(NODE, task_id)
        assert task["used"] == 0.5  # reset, then this turn charged
        assert task["turns"] == 3

    def test_auto_header_does_not_reset(self, ctx, fake_harness):
        task_id = new_ulid()
        header = tasks.format_header(task_id, 0, auto=True)
        _handle(ctx, "gerry", f"{NODE}:phil", f"{header} one")
        _handle(ctx, "gerry", f"{NODE}:phil", f"{header} two")
        assert tasks.load_task(NODE, task_id)["used"] == 1.0

    def test_human_message_reopens_closed_task(self, ctx, fake_harness):
        task_id = new_ulid()
        auto_header = tasks.format_header(task_id, 0, auto=True)
        for body in ("one", "two", "three"):
            _handle(ctx, "gerry", f"{NODE}:phil", f"{auto_header} {body}")
        assert tasks.load_task(NODE, task_id)["status"] == tasks.STATUS_CLOSED

        human_header = tasks.format_header(task_id, 0)
        assert _handle(ctx, "neil", f"{NODE}:phil", f"{human_header} more please") == RAN
        assert tasks.load_task(NODE, task_id)["status"] == tasks.STATUS_OPEN


class TestHopLimit:
    def test_chain_cut_dead_letters_and_notifies_creator_once(
        self, ctx, tells, fake_harness
    ):
        sent, _ = tells
        task_id = new_ulid()
        tasks.ensure_task(NODE, task_id, "gerry")
        header = tasks.format_header(task_id, 2, auto=True)  # junior-dev hop_limit 2
        assert _handle(ctx, "marcus", f"{NODE}:phil", f"{header} keep going") == DEAD
        assert not harness_calls(fake_harness)
        assert "hop-cut" in dead_reasons()
        assert len(sent) == 1
        agent, body = sent[0]
        assert agent == "gerry"  # original creator, not the hop sender
        assert "cut at hop" in body and task_id in body

        _handle(ctx, "marcus", f"{NODE}:phil", f"{header} something else")
        assert len(sent) == 1  # only told once

    def test_below_limit_runs(self, ctx, fake_harness):
        header = tasks.format_header(new_ulid(), 1, auto=True)
        _handle(ctx, "gerry", f"{NODE}:phil", f"{header} ok")
        assert len(harness_calls(fake_harness)) == 1


class TestConcurrency:
    def test_tier_limit_defers_to_pending(self, ctx, tells, fake_harness):
        other = state.AgentLock(NODE, "marcus")
        assert other.acquire("junior-dev")
        assert _handle(ctx, "gerry", f"{NODE}:phil", "blocked") == DEFERRED
        assert not harness_calls(fake_harness)
        assert len(state.list_pending(NODE)) == 1

        other.release()
        assert drain(ctx) == 1
        assert len(harness_calls(fake_harness)) == 1
        assert not state.list_pending(NODE)
        assert "blocked" in read_prompt(harness_calls(fake_harness)[0])

    def test_busy_agent_defers_to_pending(self, ctx, fake_harness):
        held = state.AgentLock(NODE, "phil")
        assert held.acquire("junior-dev")
        assert _handle(ctx, "gerry", f"{NODE}:phil", "wait your turn") == DEFERRED
        assert not harness_calls(fake_harness)
        assert len(state.list_pending(NODE)) == 1
        held.release()

    def test_lock_released_after_turn(self, ctx, fake_harness):
        handle_message(ctx, "gerry", f"{NODE}:phil", "one")
        assert not state.live_locks(NODE)
        handle_message(ctx, "gerry", f"{NODE}:phil", "two")
        assert len(harness_calls(fake_harness)) == 2


def _set_throttle(config_path, **throttle):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["throttle"] = throttle
    config_path.write_text(json.dumps(config), encoding="utf-8")


class TestTeamThrottle:
    def test_max_concurrent_caps_across_tiers(self, ctx, harness_config, fake_harness):
        _set_throttle(
            harness_config, max_concurrent=1, min_seconds_between_turn_starts=0
        )
        other = state.AgentLock(NODE, "marcus")
        assert other.acquire("leader")
        assert _handle(ctx, "gerry", f"{NODE}:phil", "wait for the team slot") == DEFERRED
        assert not harness_calls(fake_harness)
        assert len(state.list_pending(NODE)) == 1

        other.release()
        assert drain(ctx) == 1
        assert len(harness_calls(fake_harness)) == 1

    def test_cadence_spaces_turn_starts(self, ctx, harness_config, fake_harness):
        _set_throttle(
            harness_config, max_concurrent=0, min_seconds_between_turn_starts=3600
        )
        assert _handle(ctx, "gerry", f"{NODE}:phil", "first") == RAN
        assert _handle(ctx, "gerry", f"{NODE}:gerry", "too soon") == DEFERRED
        assert len(harness_calls(fake_harness)) == 1
        assert len(state.list_pending(NODE)) == 1
        # No long in-process sleep: redispatch re-defers instead of blocking.
        assert drain(ctx) == 0
        assert len(harness_calls(fake_harness)) == 1
        assert len(state.list_pending(NODE)) == 1

    def test_cadence_allows_after_window(self, ctx, harness_config, fake_harness):
        _set_throttle(
            harness_config, max_concurrent=0, min_seconds_between_turn_starts=3600
        )
        state._atomic_write_text(
            state.last_turn_start_path(NODE), "2020-01-01T00:00:00Z\n"
        )
        assert _handle(ctx, "gerry", f"{NODE}:phil", "late enough") == RAN


class TestGovernedRecovery:
    def _crash_evidence(self, task_id):
        state.refresh_active(NODE, "phil", ttl=5)
        state.write_turn(
            NODE, "phil",
            {"task": task_id, "hop": 0, "sender": "gerry", "body": "finish the job"},
        )

    def test_nudge_redispatches_crashed_turn(self, ctx, fake_harness):
        task_id = new_ulid()
        self._crash_evidence(task_id)
        summary = run_idle(ctx)
        assert summary["nudged"] == ["phil"]
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert "idle recovery" in prompt
        assert "finish the job" in prompt
        assert tasks.load_task(NODE, task_id)["nudges"] == {"phil": 1}

    def test_nudge_cap_closes_task_through_synthesis(self, ctx, fake_harness):
        task_id = new_ulid()
        for _ in range(2):  # nudge_cap default is 2
            self._crash_evidence(task_id)
            assert run_idle(ctx)["nudged"] == ["phil"]

        self._crash_evidence(task_id)
        summary = run_idle(ctx)
        assert summary["nudged"] == []
        task = tasks.load_task(NODE, task_id)
        assert task["status"] == tasks.STATUS_CLOSED
        assert task["synthesized"]
        prompt = read_prompt(harness_calls(fake_harness)[-1])
        assert "You are Gerry" in prompt
        assert "Respond NOW" in prompt

    def test_quiet_agent_ages_off_watch_list(self, ctx, fake_harness):
        state.refresh_active(NODE, "phil", ttl=1)
        summary = run_idle(ctx)
        assert summary["nudged"] == []
        assert "phil" in summary["dropped"]
        assert state.load_active(NODE) == {}


class TestRunHarness:
    def test_timeout_kills_process_group(self, tmp_path):
        script = tmp_path / "sleepy.py"
        script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        tier = Tier(
            name="slow",
            invoke=[sys.executable, str(script), "{prompt}"],
            timeout_seconds=1,
        )
        code, _out, duration, timed_out = run_harness(tier, "x", tmp_path)
        assert timed_out
        assert duration < 10
        assert code != 0

    def test_spawn_failure_reports_127(self, tmp_path):
        tier = Tier(name="t", invoke=["/no/such/binary-r4t", "{prompt}"])
        code, out, _dur, timed_out = run_harness(tier, "x", tmp_path)
        assert code == 127
        assert "failed to spawn" in out
        assert not timed_out

    def test_spawn_failure_tells_sender(self, ctx, repo, tells, fake_harness, harness_config):
        config = json.loads(harness_config.read_text(encoding="utf-8"))
        config["junior-dev"]["invoke"] = ["/no/such/binary-r4t", "{prompt}"]
        harness_config.write_text(json.dumps(config), encoding="utf-8")
        sent, _ = tells
        handle_message(ctx, "gerry", f"{NODE}:phil", "hi")
        assert any("failed to start" in b for _, b in sent)


class TestCli:
    def run(self, *argv):
        return r4t_main(list(argv))

    def test_dispatch_end_to_end(self, r4t_home, repo, harness_config, fake_harness):
        rc = self.run(
            "dispatch",
            "--root", str(repo),
            "--from", "gerry",
            "--to", "s1l:phil",
            "--message", "cli job",
            "--harness-config", str(harness_config),
            "--no-notify",
        )
        assert rc == 0
        assert len(harness_calls(fake_harness)) == 1

    def test_dispatch_drains_pending_first(
        self, r4t_home, repo, harness_config, fake_harness
    ):
        state.park_pending(
            NODE,
            {"from": "gerry", "to": "s1l:phil", "task": new_ulid(), "hop": 0,
             "auto": True, "body": "parked earlier"},
        )
        self.run(
            "dispatch",
            "--root", str(repo),
            "--from", "gerry",
            "--to", "s1l:phil",
            "--message", "live one",
            "--harness-config", str(harness_config),
            "--no-notify",
        )
        calls = harness_calls(fake_harness)
        assert len(calls) == 2
        assert "parked earlier" in read_prompt(calls[0])
        assert "live one" in read_prompt(calls[1])

    def test_status(self, r4t_home, repo, harness_config, capsys):
        state.team_dir(NODE).mkdir(parents=True, exist_ok=True)
        rc = self.run(
            "status",
            "--root", str(repo),
            "--node", NODE,
            "--harness-config", str(harness_config),
            "--no-notify",
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Gerry: tier=leader (pinned)" in out
        assert "bucket=8.0/8" in out
        assert "Phil: tier=junior-dev" in out
        assert "Neil: Human, address=neil" in out
        assert "Broken: DISABLED" in out
        assert "dead letters: 0" in out

    def test_harness_list(self, r4t_home, repo, harness_config, capsys):
        rc = self.run(
            "harness", "list",
            "--root", str(repo),
            "--harness-config", str(harness_config),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "junior-dev:" in out
        assert "gerry -> leader" in out
        assert "Phil: junior-dev" in out
        assert "Neil: Human" in out

    def test_task_list_and_show(self, r4t_home, capsys):
        task = tasks.ensure_task(NODE, new_ulid(), "gerry")
        assert self.run("task", "list", "--node", NODE) == 0
        assert task["id"] in capsys.readouterr().out
        assert self.run("task", "show", task["id"], "--node", NODE) == 0
        assert '"creator": "gerry"' in capsys.readouterr().out

    def test_clear_prunes_and_expires(self, r4t_home, repo, harness_config, capsys):
        dead = state.agent_dir(NODE, "phil") / ".lock"
        dead.parent.mkdir(parents=True, exist_ok=True)
        dead.write_text(json.dumps({"pid": 99999999, "tier": "t"}), encoding="utf-8")
        stale = tasks.new_task(new_ulid(), "gerry")
        stale["updated_at"] = "2020-01-01T00:00:00Z"
        tasks.atomic_write_json(tasks.task_path(NODE, stale["id"]), stale)
        rc = self.run(
            "clear",
            "--root", str(repo),
            "--node", NODE,
            "--harness-config", str(harness_config),
            "--no-notify",
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "pruned 1 stale lock(s)" in out
        assert "expired 1 task(s)" in out
        assert tasks.load_task(NODE, stale["id"]) is None

    def test_roster_check_flags_problems(self, r4t_home, repo, harness_config, capsys):
        rc = self.run(
            "roster", "check",
            "--root", str(repo),
            "--harness-config", str(harness_config),
        )
        assert rc == 1  # the fixture roster contains the Broken member
        out = capsys.readouterr().out
        assert "Broken" in out

    def test_roster_check_clean(self, r4t_home, tmp_path, harness_config, capsys):
        root = tmp_path / "clean-repo"
        root.mkdir()
        (root / "ROSTER.md").write_text(
            textwrap.dedent(
                """\
                ### Gerry
                - **Status:** AI
                - **Harness:** leader
                - **Leader:** yes

                ### Phil
                - **Status:** AI
                - **Harness:** junior-dev
                """
            ),
            encoding="utf-8",
        )
        rc = self.run(
            "roster", "check",
            "--root", str(root),
            "--harness-config", str(harness_config),
        )
        assert rc == 0
        assert "OK" in capsys.readouterr().out

    def test_roster_check_missing_leader(self, r4t_home, tmp_path, harness_config, capsys):
        root = tmp_path / "leaderless"
        root.mkdir()
        (root / "ROSTER.md").write_text(
            "### Phil\n- **Status:** AI\n- **Harness:** junior-dev\n",
            encoding="utf-8",
        )
        rc = self.run(
            "roster", "check",
            "--root", str(root),
            "--harness-config", str(harness_config),
        )
        assert rc == 1
        assert "no leader" in capsys.readouterr().out


class TestDefault:
    def run(self, *argv):
        return r4t_main(list(argv))

    def test_no_args_shows_overview(self, r4t_home, repo, harness_config, capsys, monkeypatch):
        r4t_home.mkdir(parents=True, exist_ok=True)
        (r4t_home / "harnesses.json").write_text(
            harness_config.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        state.team_dir(NODE).mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(repo)
        rc = self.run()
        assert rc == 0
        out = capsys.readouterr().out
        assert "r4t — Roster For Teams" in out
        assert f"R4T_HOME: {r4t_home}" in out
        assert "Harness" in out
        assert "junior-dev:" in out
        assert "Commands" in out
        assert "init" in out
        assert "sandbox --fake" in out
        assert "Next steps" in out
        assert f"{NODE}:" in out
        assert "ROSTER.md" in out

    def test_no_args_missing_config_hints_init(self, tmp_path, monkeypatch, capsys):
        empty_home = tmp_path / "empty-r4t"
        monkeypatch.setenv("R4T_HOME", str(empty_home))
        monkeypatch.chdir(tmp_path)
        rc = self.run()
        assert rc == 0
        out = capsys.readouterr().out
        assert "(missing — run `r4t init`" in out
        assert "no ROSTER.md" in out


class TestInit:
    def run(self, *argv):
        return r4t_main(list(argv))

    def test_init_writes_roster_and_config(self, r4t_home, tmp_path, capsys):
        root = tmp_path / "fresh-repo"
        root.mkdir()
        rc = self.run("init", "--root", str(root))
        assert rc == 0
        out = capsys.readouterr().out
        assert (root / "ROSTER.md").is_file()
        assert (r4t_home / "harnesses.json").is_file()
        assert "a8s add fresh-repo-node" in out
        assert "a8s namespace fresh-repo fresh-repo-node" in out
        assert "a8s start fresh-repo-node" in out
        assert "tell fresh-repo:dev" in out

    def test_generated_roster_passes_check(self, r4t_home, tmp_path, capsys):
        root = tmp_path / "fresh-repo"
        root.mkdir()
        self.run("init", "--root", str(root))
        capsys.readouterr()
        rc = self.run("roster", "check", "--root", str(root))
        assert rc == 0
        assert "OK" in capsys.readouterr().out

    def test_init_is_idempotent(self, r4t_home, tmp_path, capsys):
        root = tmp_path / "fresh-repo"
        root.mkdir()
        self.run("init", "--root", str(root))
        roster_before = (root / "ROSTER.md").read_text(encoding="utf-8")
        config_before = (r4t_home / "harnesses.json").read_text(encoding="utf-8")
        capsys.readouterr()

        rc = self.run("init", "--root", str(root))
        assert rc == 0
        out = capsys.readouterr().out
        assert "left unchanged" in out
        assert (root / "ROSTER.md").read_text(encoding="utf-8") == roster_before
        assert (r4t_home / "harnesses.json").read_text(encoding="utf-8") == config_before
