from __future__ import annotations

import errno
import json
import sys
import textwrap
import time

import pytest

import dispatch
import state
import tasks
from dispatch import (
    DEAD,
    RAN,
    drain,
    drain_until_quiet,
    handle_message,
    run_harness,
    run_idle,
    split_recipient,
)
from rig import Rig, load_rig_config
from roster import load_roster
from r4t import main as r4t_main
from ulid import new as new_ulid

NODE = "acme"


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


def seat_messages(name="neil"):
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in state.list_seat_messages(NODE, name)
    ]


def read_log():
    files = (state.team_dir(NODE) / "log").glob("*.md")
    return "".join(f.read_text(encoding="utf-8") for f in files)


def run_one(ctx, sender, to, message, run_fn=run_harness):
    """Enqueue one message, then run a single drain pass. Returns the number
    of turns that ran (1 when the addressed member ran its batch)."""
    handle_message(ctx, sender, to, message, run_fn=run_fn, drain_after=False)
    return drain(ctx, run_fn=run_fn)


def member_budget(ctx, name):
    config = load_rig_config(ctx.config_path)
    member = load_roster(ctx.roster_path).find(name)
    rig, _e, _p = config.rig_for(member)
    return state.budget_level(NODE, name, rig.budget_max, rig.budget_earn_per_hour)


def empty_member_budget(ctx, name):
    config = load_rig_config(ctx.config_path)
    member = load_roster(ctx.roster_path).find(name)
    rig, _e, _p = config.rig_for(member)
    state.budget_charge(NODE, name, rig.budget_max, rig.budget_earn_per_hour, rig.budget_max + 5)


class TestSplitRecipient:
    def test_sub_address(self):
        assert split_recipient("acme:phil") == ("acme", "phil")

    def test_bare(self):
        assert split_recipient("acme") == ("acme", "")

    def test_first_colon_only(self):
        assert split_recipient("acme:a:b") == ("acme", "a:b")


class TestIngressAndTurn:
    def test_member_turn_runs_fake_harness(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "acme:phil", "hi")
        assert len(harness_calls(fake_harness)) == 1

    def test_prompt_batches_messages_without_headers(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "acme:phil", "hi")
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert "You are Phil" in prompt
        assert "## Messages since your last turn" in prompt
        assert "This is one turn" in prompt
        assert "[r4t task=" not in prompt

    def test_new_thread_ledger_created(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "acme:phil", "hi")
        tasks_list = tasks.list_tasks(NODE)
        assert len(tasks_list) == 1
        assert tasks_list[0]["creator"] == "gerry"

    def test_internal_header_adopted_and_stripped(self, ctx, fake_harness):
        task_id = new_ulid()
        header = tasks.format_header(task_id, 2)
        handle_message(ctx, f"{NODE}:phil", "acme:gerry", f"{header} continue please")
        assert tasks.load_task(NODE, task_id) is not None
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert "continue please" in prompt
        assert f"thread {task_id}" in prompt

    def test_external_header_is_untrusted_content(self, ctx, fake_harness):
        task_id = new_ulid()
        header = tasks.format_header(task_id, 5)
        handle_message(ctx, "gerry", "acme:gerry", f"{header} continue please")
        # external sender: the header is treated as content, a fresh thread opens
        assert tasks.load_task(NODE, task_id) is None
        assert len(tasks.list_tasks(NODE)) == 1

    def test_forged_header_cannot_hijack_a_thread(self, ctx, fake_harness):
        handle_message(ctx, "boss", "acme:gerry", "real work")
        real = tasks.list_tasks(NODE)[0]
        forged = tasks.format_header(real["id"], 9)
        handle_message(ctx, "attacker", "acme:phil", f"{forged} sneak in")
        # a second, distinct thread opened; the forged id did not attach
        assert len(tasks.list_tasks(NODE)) == 2

    def test_bare_node_goes_to_leader(self, ctx, fake_harness):
        handle_message(ctx, "neil", "acme", "status update please")
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert "You are Gerry" in prompt

    def test_history_holds_inbound(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "acme:phil", "first job")
        assert "first job" in state.read_history(NODE, "phil")

    def test_conversation_history_fed_back(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "acme:phil", "first job")
        handle_message(ctx, "marcus", "acme:phil", "second job")
        prompt = read_prompt(harness_calls(fake_harness)[-1])
        assert "first job" in prompt  # earlier turn is now in the history block

    def test_velocity_recorded(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "acme:phil", "job")
        rows = (state.team_dir(NODE) / "velocity.csv").read_text().splitlines()
        assert len(rows) == 2  # header + one turn

    def test_transcript_logged(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "acme:phil", "job")
        log = read_log()
        assert "### Prompt" in log
        assert "### Output (Phil" in log

    def test_two_messages_drain_in_one_batch_turn(self, ctx, fake_harness):
        # Both arrive before any turn runs: ONE turn drains the whole queue.
        handle_message(ctx, "gerry", "acme:phil", "job one", drain_after=False)
        handle_message(ctx, "marcus", "acme:phil", "job two", drain_after=False)
        assert state.queue_depth(NODE, "phil") == 2
        assert drain(ctx) == 1
        calls = harness_calls(fake_harness)
        assert len(calls) == 1
        prompt = read_prompt(calls[0])
        assert "job one" in prompt and "job two" in prompt

    def test_duplicate_collapse_notes_repeats(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "acme:phil", "same ping", drain_after=False)
        handle_message(ctx, "gerry", "acme:phil", "same ping", drain_after=False)
        assert state.queue_depth(NODE, "phil") == 1
        assert drain(ctx) == 1
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert "(sent 2 times)" in prompt

    def test_mid_turn_arrival_rides_the_next_turn(self, ctx, fake_harness):
        # A message that lands while a turn is in flight is not in that batch.
        def enqueue_midturn(rig, prompt, cwd, *, env=None, variant=0):
            state.enqueue(NODE, "phil", {"from": "late", "body": "arrived mid-turn", "task": new_ulid(), "hop": 0})
            return run_harness(rig, prompt, cwd, env=env, variant=variant)

        handle_message(ctx, "gerry", "acme:phil", "first", drain_after=False)
        assert drain(ctx, run_fn=enqueue_midturn) == 1
        assert "arrived mid-turn" not in read_prompt(harness_calls(fake_harness)[0])
        assert state.queue_depth(NODE, "phil") == 1  # it waits for the next turn


class TestRejections:
    def test_unknown_member_dead_letters_and_tells(self, ctx, tells, fake_harness):
        sent, _ = tells
        handle_message(ctx, "gerry", "acme:nobody", "hi")
        assert not harness_calls(fake_harness)
        assert any("no team member named" in b for _, b in sent)
        assert "unknown-recipient" in dead_reasons()

    def test_human_message_parks_in_seat(self, ctx, tells, fake_harness):
        handle_message(ctx, "gerry", "acme:neil", "hi")
        assert not harness_calls(fake_harness)
        assert [m["from"] for m in seat_messages()] == ["gerry"]

    def test_doorbell_copy_is_headerless_egress(self, ctx, tells, fake_harness):
        sent, _ = tells
        header = tasks.format_header(new_ulid(), 0, auto=True)
        handle_message(ctx, f"{NODE}:gerry", "acme:neil", f"{header} ship report")
        assert ("neil", "ship report") in sent

    def test_human_doorbell_skipped_when_attached(self, ctx, tells, fake_harness):
        sent, _ = tells
        state.touch_seat_presence(NODE, "Neil")
        handle_message(ctx, "gerry", "acme:neil", "hi")
        assert not sent

    def test_disabled_member_dead_letters(self, ctx, tells, fake_harness):
        sent, _ = tells
        handle_message(ctx, "gerry", "acme:broken", "hi")
        assert any("disabled" in b for _, b in sent)
        assert "member-disabled" in dead_reasons()

    def test_unknown_rig_dead_letters(self, ctx, repo, tells, fake_harness):
        (repo / "ROSTER.md").write_text(
            "### Ghost\n- **Status:** AI\n- **Rig:** phantom\n- **Leader:** yes\n",
            encoding="utf-8",
        )
        sent, _ = tells
        handle_message(ctx, "gerry", "acme:ghost", "hi")
        assert any("cannot run" in b for _, b in sent)
        assert "no-rig" in dead_reasons()

    def test_missing_roster(self, ctx, repo, tells, fake_harness):
        (repo / "ROSTER.md").unlink()
        sent, _ = tells
        handle_message(ctx, "gerry", "acme:phil", "hi")
        assert any("cannot dispatch" in b for _, b in sent)

    def test_no_leader_for_bare_node(self, ctx, repo, tells, fake_harness):
        (repo / "ROSTER.md").write_text(
            "### Phil\n- **Status:** AI\n- **Rig:** junior-dev\n", encoding="utf-8"
        )
        sent, _ = tells
        handle_message(ctx, "gerry", "acme", "hi")
        assert any("no leader" in b for _, b in sent)
        assert "no-leader" in dead_reasons()


class TestPins:
    def test_pin_overrides_roster_rig(self, ctx, repo, fake_harness):
        # Gerry is pinned to `leader` in the fixture config.
        handle_message(ctx, "neil", "acme:gerry", "hi")
        assert "rig leader" in read_log()


class TestStagingRelease:
    def test_external_release_strips_header_keeps_class(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "outsider")
        monkeypatch.setenv("CHATTY_BODY", "the fix is deployed")
        assert run_one(chatty_ctx, "gerry", "acme:phil", "deploy the fix") == 1
        envelopes = outbox_envelopes(repo)
        assert len(envelopes) == 1
        envelope = envelopes[0]
        assert envelope["to"] == "outsider"
        assert envelope["x_r4t_class"] == "auto"
        assert envelope["content"] == "the fix is deployed"
        assert not envelope["content"].startswith("[r4t")
        assert not state.staging_dir(NODE, "phil").exists()

    def test_outbound_attributed_to_history(self, chatty_ctx, chatty_harness, monkeypatch):
        monkeypatch.setenv("CHATTY_TO", "neil")
        monkeypatch.setenv("CHATTY_BODY", "status: done")
        run_one(chatty_ctx, "gerry", "acme:phil", "report status")
        history = state.read_history(NODE, "phil")
        assert "to neil" in history
        assert "status: done" in history

    def test_quota_overflow_dead_letters(self, chatty_ctx, repo, chatty_harness, monkeypatch):
        monkeypatch.setenv("CHATTY_TO", "outsider")
        monkeypatch.setenv("CHATTY_SENDS", "4")  # max_sends_per_turn is 2
        run_one(chatty_ctx, "gerry", "acme:phil", "fan out")
        assert len(outbox_envelopes(repo)) == 2
        assert dead_reasons() == ["quota", "quota"]

    def test_intra_team_release_enqueues_and_drains(
        self, chatty_ctx, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "acme:gerry")
        monkeypatch.setenv("CHATTY_BODY", "please review my patch")
        assert run_one(chatty_ctx, "neil", "acme:phil", "do the work") == 1
        assert state.queue_depth(NODE, "gerry") == 1  # delegation queued for gerry
        monkeypatch.setenv("CHATTY_SENDS", "0")
        assert drain_until_quiet(chatty_ctx) == 1
        _script, out = chatty_harness
        prompts = [read_prompt(p) for p in sorted(out.iterdir())]
        assert len(prompts) == 2
        assert "You are Gerry" in prompts[1]
        assert "From: phil" in prompts[1]
        assert "please review my patch" in prompts[1]
        assert len(tasks.list_tasks(NODE)) == 1  # one thread across the hop

    def test_bare_teammate_name_enqueues_internal(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "Gerry")
        monkeypatch.setenv("CHATTY_BODY", "please review my patch")
        assert run_one(chatty_ctx, "neil", "acme:phil", "do the work") == 1
        assert outbox_envelopes(repo) == []
        assert state.queue_depth(NODE, "gerry") == 1

    def test_human_recipient_parks_in_seat(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "acme:neil")
        monkeypatch.setenv("CHATTY_BODY", "shipped")
        assert run_one(chatty_ctx, "gerry", "acme:phil", "ship it") == 1
        assert outbox_envelopes(repo) == []
        parked = seat_messages()
        assert [m["from"] for m in parked] == ["acme:phil"]
        assert "shipped" in parked[0]["content"]

    def test_bare_unknown_name_passes_through_external(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "chatroom")
        monkeypatch.setenv("CHATTY_BODY", "#dev hello")
        assert run_one(chatty_ctx, "neil", "acme:phil", "post an update") == 1
        assert [e["to"] for e in outbox_envelopes(repo)] == ["chatroom"]

    def test_reply_to_human_creator_closes_thread(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "neil")
        monkeypatch.setenv("CHATTY_BODY", "done: shipped and verified")
        assert run_one(chatty_ctx, "acme:neil", "acme:phil", "ship it") == 1
        task = tasks.list_tasks(NODE)[0]
        assert task["status"] == tasks.STATUS_CLOSED
        assert task["answered"]

    def test_reply_to_external_creator_closes_thread(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "boss-agent")
        monkeypatch.setenv("CHATTY_BODY", "done: shipped and verified")
        assert run_one(chatty_ctx, "boss-agent", "acme:phil", "ship it") == 1
        assert [e["to"] for e in outbox_envelopes(repo)] == ["boss-agent"]
        assert tasks.list_tasks(NODE)[0]["status"] == tasks.STATUS_CLOSED

    def test_delegation_runs_even_after_originator_answered(
        self, chatty_ctx, chatty_harness, monkeypatch
    ):
        # The leader answers the human AND delegates in one turn. Closing the
        # thread on the answer must not drop the delegation — closed threads
        # still accept mail; nothing dead-letters.
        monkeypatch.setenv("CHATTY_TO", "neil,gerry")
        monkeypatch.setenv("CHATTY_SENDS", "2")
        monkeypatch.setenv("CHATTY_BODY", "P0 logged, dispatching ({i})")
        assert run_one(chatty_ctx, "neil", "acme:phil", "movement is broken") == 1
        assert state.queue_depth(NODE, "gerry") == 1
        monkeypatch.setenv("CHATTY_SENDS", "0")
        assert drain_until_quiet(chatty_ctx) == 1
        assert dead_reasons() == []

    def test_reply_elsewhere_leaves_thread_open(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "chatroom")
        monkeypatch.setenv("CHATTY_BODY", "#dev progress update")
        assert run_one(chatty_ctx, "neil", "acme:phil", "post an update") == 1
        assert tasks.list_tasks(NODE)[0]["status"] == tasks.STATUS_OPEN

    def test_released_envelope_claims_namespaced_sender(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "outsider")
        monkeypatch.setenv("CHATTY_BODY", "status: done")
        run_one(chatty_ctx, "gerry", "acme:phil", "report status")
        assert [e["from"] for e in outbox_envelopes(repo)] == ["acme:phil"]

    def test_reply_closes_only_the_answered_thread(self, ctx, repo, r4t_home):
        # Two originators queue work for gerry; gerry answers only neil.
        handle_message(ctx, "acme:neil", "acme:gerry", "task from neil", drain_after=False)
        handle_message(ctx, "boss", "acme:gerry", "task from boss", drain_after=False)

        def reply_neil(rig, prompt, cwd, *, env=None, variant=0):
            outbox = dispatch.Path(env["TELL_OUTBOX_DIR"])
            mid = new_ulid()
            (outbox / f"{mid}.json").write_text(
                json.dumps({"id": mid, "to": "neil", "content": "done for neil, verified"}),
                encoding="utf-8",
            )
            return 0, "", 1.0, False

        assert drain(ctx, run_fn=reply_neil) == 1
        by_creator = {t["creator"]: t for t in tasks.list_tasks(NODE)}
        assert by_creator["acme:neil"]["status"] == tasks.STATUS_CLOSED
        assert by_creator["boss"]["status"] == tasks.STATUS_OPEN


class TestBudgets:
    def test_turn_charges_member_and_team_one_unit(self, ctx, fake_harness):
        config = load_rig_config(ctx.config_path)
        handle_message(ctx, "gerry", "acme:phil", "job")
        assert member_budget(ctx, "phil") == pytest.approx(99.0, abs=0.3)
        team = state.budget_level(
            NODE, state.CELL_BUDGET_KEY,
            config.cell_budget_max, config.cell_budget_earn_per_hour,
        )
        assert team == pytest.approx(199.0, abs=0.3)

    def test_empty_member_budget_rests_and_holds_queue(self, ctx, fake_harness):
        empty_member_budget(ctx, "phil")
        handle_message(ctx, "gerry", "acme:phil", "job")
        assert not harness_calls(fake_harness)
        assert state.queue_depth(NODE, "phil") == 1
        assert "RESTING phil" in read_log()

    def test_empty_cell_budget_rests_everyone(self, ctx, fake_harness):
        config = load_rig_config(ctx.config_path)
        state.budget_charge(
            NODE, state.CELL_BUDGET_KEY,
            config.cell_budget_max, config.cell_budget_earn_per_hour,
            config.cell_budget_max + 5,
        )
        handle_message(ctx, "gerry", "acme:phil", "job")
        assert not harness_calls(fake_harness)
        assert state.queue_depth(NODE, "phil") == 1

    def test_budget_refills_and_runs_later(self, ctx, fake_harness):
        config = load_rig_config(ctx.config_path)
        rig = config.rigs["junior-dev"]
        # emptied an hour ago; at 50/hour it has refilled well past 1 unit
        state.budget_charge(
            NODE, "phil", rig.budget_max, rig.budget_earn_per_hour,
            rig.budget_max, now=time.time() - 3600,
        )
        handle_message(ctx, "gerry", "acme:phil", "job")
        assert len(harness_calls(fake_harness)) == 1

    def test_seat_send_reports_resting(self, ctx):
        from chat import send_as_human

        empty_member_budget(ctx, "phil")
        human = next(m for m in load_roster(ctx.roster_path).members if m.is_human)
        note = send_as_human(ctx, human, "acme:phil", "you there?")
        assert note is not None
        assert "resting" in note and "Phil" in note
        assert state.queue_depth(NODE, "phil") == 1  # message safely queued


def fail_run(rig, prompt, cwd, *, env=None, variant=0):
    return 1, "boom", 0.05, False


def ok_run(rig, prompt, cwd, *, env=None, variant=0):
    return 0, "ok", 0.05, False


def timeout_run(rig, prompt, cwd, *, env=None, variant=0):
    return 0, "hung", 0.05, True


def trip_breaker(ctx, agent="phil", cap=5):
    for i in range(cap):
        assert run_one(ctx, "neil", f"acme:{agent}", f"attempt {i}", run_fn=fail_run) == 1


class TestFailureBreaker:
    def test_failures_counted_and_cleared_by_clean_turn(self, ctx):
        assert run_one(ctx, "neil", "acme:phil", "one", run_fn=fail_run) == 1
        assert run_one(ctx, "neil", "acme:phil", "two", run_fn=timeout_run) == 1
        assert state.read_meta(NODE, "phil")["consecutive_failures"] == 2
        assert run_one(ctx, "neil", "acme:phil", "three", run_fn=ok_run) == 1
        assert state.read_meta(NODE, "phil")["consecutive_failures"] == 0

    def test_trips_at_cap_then_holds_queue(self, ctx):
        trip_breaker(ctx)
        handle_message(ctx, "neil", "acme:phil", "blocked", drain_after=False)
        assert drain(ctx, run_fn=ok_run) == 0  # breaker open: no turn
        assert state.queue_depth(NODE, "phil") >= 1  # messages held, not dropped
        assert "BREAKER phil" in read_log()
        assert dead_reasons() == []  # nothing dead-lettered

    def test_half_open_probe_success_closes(self, ctx):
        trip_breaker(ctx)
        state.update_meta(NODE, "phil", last_failure_at="2020-01-01T00:00:00Z")
        assert run_one(ctx, "neil", "acme:phil", "probe", run_fn=ok_run) == 1
        assert state.read_meta(NODE, "phil")["consecutive_failures"] == 0

    def test_failed_probe_reopens(self, ctx):
        trip_breaker(ctx)
        state.update_meta(NODE, "phil", last_failure_at="2020-01-01T00:00:00Z")
        assert run_one(ctx, "neil", "acme:phil", "probe", run_fn=fail_run) == 1
        handle_message(ctx, "neil", "acme:phil", "again", drain_after=False)
        assert drain(ctx, run_fn=ok_run) == 0  # reopened for another cooldown


ANSWER = "Here is my long detailed answer about the payload format. " * 3


def stdout_only(rig, prompt, cwd, *, env=None, variant=0):
    return 0, ANSWER, 1.0, False


class TestStdoutFallback:
    def test_stdout_becomes_reply_to_external_sender(self, ctx, repo, r4t_home):
        handle_message(ctx, "boss", "acme:gerry", "question", run_fn=stdout_only)
        envelopes = outbox_envelopes(repo)
        assert [e["to"] for e in envelopes] == ["boss"]
        assert envelopes[0]["content"] == ANSWER.strip()
        assert envelopes[0]["x_r4t_class"] == "auto"
        assert "[r4t" not in envelopes[0]["content"]
        assert "r4t: STDOUT-REPLY gerry" in read_log()

    def test_stdout_reply_to_creator_answers_the_thread(self, ctx, r4t_home):
        handle_message(ctx, "boss", "acme:gerry", "question", run_fn=stdout_only)
        assert tasks.list_tasks(NODE)[0]["status"] == tasks.STATUS_CLOSED
        assert "r4t: ANSWERED" in read_log()

    def test_tell_always_wins(self, ctx, repo, r4t_home):
        def tell_and_chatter(rig, prompt, cwd, *, env=None, variant=0):
            outbox = dispatch.Path(env["TELL_OUTBOX_DIR"])
            msg_id = new_ulid()
            (outbox / f"{msg_id}.json").write_text(
                json.dumps({"id": msg_id, "to": "outsider", "content": "the real reply"}),
                encoding="utf-8",
            )
            return 0, ANSWER, 1.0, False

        handle_message(ctx, "boss", "acme:gerry", "question", run_fn=tell_and_chatter)
        envelopes = outbox_envelopes(repo)
        assert [e["to"] for e in envelopes] == ["outsider"]
        assert envelopes[0]["content"] == "the real reply"
        assert "STDOUT-REPLY" not in read_log()

    def test_chrome_only_stdout_stays_silent(self, ctx, repo, r4t_home):
        chrome = (
            "\x1b[0m\n> build · qwen3:0.6b\n\x1b[0m\n"
            '\x1b[0m✱ \x1b[0mGlob "**/sample.txt"\x1b[90m 1 match\x1b[0m\n'
            "\x1b[0m→ \x1b[0mRead sample.txt\n"
            "Shell cwd was reset to /Users/neilo/bin\n"
        )

        def chrome_only(rig, prompt, cwd, *, env=None, variant=0):
            return 0, chrome, 1.0, False

        handle_message(ctx, "boss", "acme:gerry", "question", run_fn=chrome_only)
        assert outbox_envelopes(repo) == []
        text = read_log()
        assert "r4t: SILENT gerry" in text
        assert "STDOUT-REPLY" not in text

    def test_reply_is_cleaned_of_chrome(self, ctx, repo, r4t_home):
        noisy = (
            "\x1b[0m\n> build · qwen3.6:latest\n\x1b[0m\n"
            "\x1b[0m→ \x1b[0mRead GOAL.md\n"
            + ANSWER
            + "\nShell cwd was reset to /Users/neilo/bin\n"
        )

        def noisy_answer(rig, prompt, cwd, *, env=None, variant=0):
            return 0, noisy, 1.0, False

        handle_message(ctx, "boss", "acme:gerry", "question", run_fn=noisy_answer)
        assert outbox_envelopes(repo)[0]["content"] == ANSWER.strip()

    def test_fallback_reply_to_internal_sender_wakes_them(self, ctx, fake_harness, r4t_home):
        task_id = new_ulid()
        header = tasks.format_header(task_id, 1, auto=True)
        handle_message(
            ctx, f"{NODE}:gerry", "acme:phil", f"{header} question",
            run_fn=stdout_only, drain_after=False,
        )
        assert drain(ctx, run_fn=stdout_only) == 1  # phil answers gerry on stdout
        drain(ctx)  # gerry's turn runs on the fake harness (no reply loop)
        prompts = [read_prompt(p) for p in harness_calls(fake_harness)]
        assert any("From: phil" in p and ANSWER.strip() in p for p in prompts)

    def test_fallback_reply_to_human_parks_in_seat(self, ctx, fake_harness, r4t_home):
        handle_message(ctx, "acme:neil", "acme:gerry", "question", run_fn=stdout_only)
        parked = seat_messages()
        assert [m["from"] for m in parked] == ["acme:gerry"]
        _, _, _, body = tasks.parse_header(parked[0]["content"])
        assert body == ANSWER.strip()
        assert tasks.list_tasks(NODE)[0]["status"] == tasks.STATUS_CLOSED

    def test_two_stdout_replies_are_not_suppressed(self, ctx, repo, r4t_home):
        handle_message(ctx, "boss", "acme:gerry", "question one", run_fn=stdout_only)
        handle_message(ctx, "boss", "acme:gerry", "question two", run_fn=stdout_only)
        assert len(outbox_envelopes(repo)) == 2  # duplicate collapse is inbound-only
        assert dead_reasons() == []

    def test_short_stdout_is_just_a_quiet_turn(self, ctx, repo, r4t_home):
        def terse(rig, prompt, cwd, *, env=None, variant=0):
            return 0, "ok.", 1.0, False

        handle_message(ctx, "boss", "acme:gerry", "question", run_fn=terse)
        assert outbox_envelopes(repo) == []
        text = read_log()
        assert "SILENT" not in text and "STDOUT-REPLY" not in text

    def test_failed_turn_gets_no_fallback(self, ctx, repo, r4t_home):
        def crashed(rig, prompt, cwd, *, env=None, variant=0):
            return 1, ANSWER, 1.0, False

        handle_message(ctx, "boss", "acme:gerry", "question", run_fn=crashed)
        assert outbox_envelopes(repo) == []
        assert "STDOUT-REPLY" not in read_log()


class TestCleanTranscript:
    def test_strips_ansi_and_chrome(self):
        raw = (
            "\x1b[0m\n> build · qwen3.6:latest\n"
            '\x1b[0m✱ \x1b[0mGlob "**/x.txt"\x1b[90m 1 match\x1b[0m\n'
            "\x1b[0m→ \x1b[0mRead x.txt\n"
            "The contents are fine.\n"
            "Shell cwd was reset to /Users/neilo/bin\n"
        )
        assert dispatch.clean_transcript(raw) == "The contents are fine."

    def test_keeps_markdown_blockquotes(self):
        raw = "As GOAL.md says:\n> the game must exit 0\nDone."
        assert dispatch.clean_transcript(raw) == raw

    def test_keeps_plain_text_untouched(self):
        assert dispatch.clean_transcript("hello\nworld") == "hello\nworld"


def make_ctx(repo, config_path, tell_fn):
    return dispatch.DispatchContext(
        root=repo, node=NODE, roster_path=repo / "ROSTER.md",
        config_path=config_path, tell_fn=tell_fn,
    )


class TestTeamThrottle:
    def _ctx(self, repo, fake_harness, tells, tmp_path, **throttle):
        from conftest import base_config

        script, _out = fake_harness
        config = base_config(script)
        config["throttle"] = throttle
        path = tmp_path / "throttle-rigs.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        _sent, capture = tells
        return make_ctx(repo, path, capture)

    def test_max_concurrent_holds_queue(self, repo, fake_harness, tells, tmp_path, r4t_home):
        ctx = self._ctx(repo, fake_harness, tells, tmp_path,
                        max_concurrent=1, min_seconds_between_turn_starts=0)
        live = state.AgentLock(NODE, "gerry")
        assert live.acquire("leader")  # one turn already live
        handle_message(ctx, "neil", "acme:phil", "job", drain_after=False)
        assert drain(ctx) == 0  # throttle blocks the second start
        assert state.queue_depth(NODE, "phil") == 1
        live.release()
        assert drain(ctx) == 1

    def test_cadence_spaces_turn_starts(self, repo, fake_harness, tells, tmp_path, r4t_home):
        ctx = self._ctx(repo, fake_harness, tells, tmp_path,
                        max_concurrent=0, min_seconds_between_turn_starts=30)
        assert run_one(ctx, "neil", "acme:phil", "one") == 1
        handle_message(ctx, "neil", "acme:gerry", "two", drain_after=False)
        assert drain(ctx) == 0  # cadence window still shut
        assert state.queue_depth(NODE, "gerry") == 1


class TestAttachmentRelease:
    def _rc(self, ctx):
        return load_roster(ctx.roster_path), load_rig_config(ctx.config_path)

    def test_bundle_is_visible_before_envelope(self, ctx, tmp_path, monkeypatch):
        roster, config = self._rc(ctx)
        staging = tmp_path / "staging"
        bundle = staging / "message-1"
        bundle.mkdir(parents=True)
        (bundle / "report.txt").write_text("result", encoding="utf-8")
        outbox = tmp_path / "outbox"
        real_write = state.atomic_write_json

        def observing_write(path, payload):
            released = outbox / "message-1"
            assert released.is_dir()
            assert (released / "report.txt").read_text(encoding="utf-8") == "result"
            real_write(path, payload)

        monkeypatch.setattr(state, "atomic_write_json", observing_write)
        dispatch._release_one(
            ctx, outbox, staging,
            {"id": "message-1", "to": "outside", "files": ["report.txt"]},
            f"{NODE}:phil", new_ulid(), 1, "result", roster, config,
        )
        assert (outbox / "message-1.json").is_file()

    def test_cross_filesystem_bundle_fallback_still_publishes_last(
        self, ctx, tmp_path, monkeypatch
    ):
        roster, config = self._rc(ctx)
        staging = tmp_path / "staging"
        bundle = staging / "message-2"
        bundle.mkdir(parents=True)
        (bundle / "report.txt").write_text("result", encoding="utf-8")
        outbox = tmp_path / "outbox"
        real_replace = dispatch.os.replace
        attempted = False

        def exdev_once(source, destination):
            nonlocal attempted
            if source == bundle and not attempted:
                attempted = True
                raise OSError(errno.EXDEV, "cross-device link")
            return real_replace(source, destination)

        monkeypatch.setattr(dispatch.os, "replace", exdev_once)
        dispatch._release_one(
            ctx, outbox, staging,
            {"id": "message-2", "to": "outside", "files": ["report.txt"]},
            f"{NODE}:phil", new_ulid(), 1, "result", roster, config,
        )
        assert attempted
        assert not bundle.exists()
        assert (outbox / "message-2" / "report.txt").read_text() == "result"
        assert (outbox / "message-2.json").is_file()

    def test_failed_cross_filesystem_copy_is_clean_and_retryable(
        self, ctx, tmp_path, monkeypatch
    ):
        roster, config = self._rc(ctx)
        staging = tmp_path / "staging"
        bundle = staging / "message-3"
        bundle.mkdir(parents=True)
        (bundle / "report.txt").write_text("result", encoding="utf-8")
        outbox = tmp_path / "outbox"
        real_replace = dispatch.os.replace
        real_copytree = dispatch.shutil.copytree

        def force_exdev(source, destination):
            if source == bundle:
                raise OSError(errno.EXDEV, "cross-device link")
            return real_replace(source, destination)

        def partial_copy(source, destination):
            destination.mkdir(parents=True)
            (destination / "partial.txt").write_text("partial", encoding="utf-8")
            raise OSError("copy failed")

        monkeypatch.setattr(dispatch.os, "replace", force_exdev)
        monkeypatch.setattr(dispatch.shutil, "copytree", partial_copy)
        envelope = {"id": "message-3", "to": "outside", "files": ["report.txt"]}
        try:
            dispatch._release_one(
                ctx, outbox, staging, envelope, f"{NODE}:phil",
                new_ulid(), 1, "result", roster, config,
            )
        except OSError as exc:
            assert str(exc) == "copy failed"
        else:
            raise AssertionError("partial copy unexpectedly succeeded")

        assert bundle.is_dir()
        assert not (outbox / "message-3.json").exists()
        assert not list(outbox.glob(".message-3.*.tmp"))

        monkeypatch.setattr(dispatch.shutil, "copytree", real_copytree)
        dispatch._release_one(
            ctx, outbox, staging, envelope, f"{NODE}:phil",
            new_ulid(), 1, "result", roster, config,
        )
        assert (outbox / "message-3" / "report.txt").read_text() == "result"
        assert (outbox / "message-3.json").is_file()

    def test_staging_cleanup_failure_does_not_hide_published_envelope(
        self, ctx, tmp_path, monkeypatch
    ):
        roster, config = self._rc(ctx)
        staging = tmp_path / "staging"
        bundle = staging / "message-4"
        bundle.mkdir(parents=True)
        (bundle / "report.txt").write_text("result", encoding="utf-8")
        outbox = tmp_path / "outbox"
        real_replace = dispatch.os.replace
        real_rmtree = dispatch.shutil.rmtree

        def force_exdev(source, destination):
            if source == bundle:
                raise OSError(errno.EXDEV, "cross-device link")
            return real_replace(source, destination)

        def fail_source_cleanup(path, *args, **kwargs):
            if path == bundle:
                return None
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(dispatch.os, "replace", force_exdev)
        monkeypatch.setattr(dispatch.shutil, "rmtree", fail_source_cleanup)
        dispatch._release_one(
            ctx, outbox, staging,
            {"id": "message-4", "to": "outside", "files": ["report.txt"]},
            f"{NODE}:phil", new_ulid(), 1, "result", roster, config,
        )
        assert bundle.is_dir()
        assert (outbox / "message-4" / "report.txt").is_file()
        assert (outbox / "message-4.json").is_file()


class TestQuietSweep:
    def _quiet_thread(self, creator="acme:neil"):
        task = tasks.new_task(new_ulid(), creator)
        task["updated_at"] = "2020-01-01T00:00:00Z"
        state.atomic_write_json(tasks.task_path(NODE, task["id"]), task)
        return task["id"]

    def test_quiet_thread_nudges_the_leader(self, ctx, fake_harness):
        thread_id = self._quiet_thread()
        summary = run_idle(ctx)
        assert summary["quiet_nudged"] == [thread_id]
        prompts = [read_prompt(p) for p in harness_calls(fake_harness)]
        assert any("You are Gerry" in p and "gone quiet" in p for p in prompts)
        # nudged to report, not force-closed
        assert tasks.load_task(NODE, thread_id)["status"] == tasks.STATUS_OPEN

    def test_recent_thread_left_alone(self, ctx, fake_harness):
        task = tasks.ensure_task(NODE, new_ulid(), "acme:neil")
        summary = run_idle(ctx)
        assert summary["quiet_nudged"] == []
        assert tasks.load_task(NODE, task["id"])["status"] == tasks.STATUS_OPEN

    def test_answered_thread_skipped(self, ctx, fake_harness):
        thread_id = self._quiet_thread()
        tasks.close_task(NODE, thread_id)  # originator already answered
        summary = run_idle(ctx)
        assert summary["quiet_nudged"] == []

    def test_live_turn_defers_the_sweep(self, ctx, fake_harness):
        self._quiet_thread()
        live = state.AgentLock(NODE, "gerry")
        assert live.acquire("leader")
        try:
            summary = run_idle(ctx)
        finally:
            live.release()
        assert summary["quiet_nudged"] == []


class TestRunHarness:
    def test_timeout_kills_process_group(self, tmp_path):
        script = tmp_path / "sleepy.py"
        script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        rig = Rig(
            name="slow",
            invoke=[sys.executable, str(script), "{prompt}"],
            timeout_seconds=1,
        )
        code, _out, duration, timed_out = run_harness(rig, "x", tmp_path)
        assert timed_out
        assert duration < 10
        assert code != 0

    def test_spawn_failure_reports_127(self, tmp_path):
        rig = Rig(name="t", invoke=["/no/such/binary-r4t", "{prompt}"])
        code, out, _dur, timed_out = run_harness(rig, "x", tmp_path)
        assert code == 127
        assert "failed to spawn" in out
        assert not timed_out

    def test_spawn_failure_tells_sender(self, ctx, repo, tells, fake_harness, rig_config):
        config = json.loads(rig_config.read_text(encoding="utf-8"))
        config["junior-dev"]["invoke"] = ["/no/such/binary-r4t", "{prompt}"]
        rig_config.write_text(json.dumps(config), encoding="utf-8")
        sent, _ = tells
        handle_message(ctx, "gerry", f"{NODE}:phil", "hi")
        assert any("failed to start" in b for _, b in sent)


class TestCli:
    def run(self, *argv):
        return r4t_main(list(argv))

    def test_dispatch_end_to_end(self, r4t_home, repo, rig_config, fake_harness):
        rc = self.run(
            "dispatch", "--root", str(repo), "--from", "gerry",
            "--to", "acme:phil", "--message", "cli job",
            "--rig-config", str(rig_config), "--no-notify",
        )
        assert rc == 0
        assert len(harness_calls(fake_harness)) == 1

    def test_dispatch_batches_queued_with_live(self, r4t_home, repo, rig_config, fake_harness):
        state.enqueue(
            NODE, "phil",
            {"from": "gerry", "to": "acme:phil", "task": new_ulid(), "hop": 0,
             "auto": True, "body": "parked earlier"},
        )
        self.run(
            "dispatch", "--root", str(repo), "--from", "gerry",
            "--to", "acme:phil", "--message", "live one",
            "--rig-config", str(rig_config), "--no-notify",
        )
        calls = harness_calls(fake_harness)
        assert len(calls) == 1  # the whole queue drains in ONE batch turn
        prompt = read_prompt(calls[0])
        assert "parked earlier" in prompt and "live one" in prompt

    def test_status(self, r4t_home, repo, rig_config, capsys):
        state.team_dir(NODE).mkdir(parents=True, exist_ok=True)
        rc = self.run(
            "status", "--root", str(repo), "--node", NODE,
            "--rig-config", str(rig_config), "--no-notify",
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Roster  (repo settings:" in out
        assert "Rigs  (your configuration:" in out
        assert "Activity" in out
        assert "rig=leader (pinned)" in out
        assert "cell=leadership" in out
        assert "budget=100/100" in out
        assert "✓ Phil" in out and "rig=junior-dev" in out
        assert "Human  address=neil" in out
        assert "✗ Broken" in out and "disabled:" in out
        assert "(try: fix ROSTER.md)" in out
        assert "dead letters  0" in out

    def test_rig_list(self, r4t_home, repo, rig_config, capsys):
        rc = self.run("rig", "list", "--root", str(repo), "--rig-config", str(rig_config))
        assert rc == 0
        out = capsys.readouterr().out
        assert "junior-dev:" in out
        assert "gerry -> leader" in out
        assert "Phil: junior-dev" in out
        assert "Neil: Human" in out

    def test_rig_presets(self, capsys):
        rc = self.run("rig", "presets")
        assert rc == 0
        out = capsys.readouterr().out
        assert "claude" in out and "opencode" in out and "cursor" in out
        assert "headless:" in out and "r4t rig add" in out

    def test_rig_add(self, tmp_path, capsys):
        config_path = tmp_path / "rigs.json"
        rc = self.run("rig", "add", "reviewer", "claude", "--rig-config", str(config_path))
        assert rc == 0
        out = capsys.readouterr().out
        assert "added rig 'reviewer'" in out
        assert "Rig:** reviewer" in out
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["reviewer"]["invoke"][0] == "claude"

    def test_rig_add_fresh_config_has_no_phantom_rigs(self, tmp_path):
        config_path = tmp_path / "rigs.json"
        rc = self.run("rig", "add", "leader", "agy", "--rig-config", str(config_path))
        assert rc == 0
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert [k for k in data if not k.startswith("_")] == ["leader"]

    def test_rig_bare_shows_overview(self, r4t_home, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        rc = self.run("rig")
        assert rc == 0
        out = capsys.readouterr().out
        assert "r4t rig —" in out and "(missing)" in out and "no rigs yet" in out
        assert "Commands" in out and "Next steps" in out
        assert "r4t rig add leader <preset>" in out

    def test_rigs_alias(self, r4t_home, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert self.run("rigs") == 0
        assert "r4t rig —" in capsys.readouterr().out

    def test_rig_add_duplicate_fails(self, tmp_path, capsys):
        config_path = tmp_path / "rigs.json"
        config_path.write_text(
            json.dumps({"worker": {"invoke": ["x", "{prompt}"]}}), encoding="utf-8"
        )
        rc = self.run("rig", "add", "worker", "opencode", "--rig-config", str(config_path))
        assert rc == 1
        assert "already exists" in capsys.readouterr().err

    def test_task_list_and_show(self, r4t_home, capsys):
        task = tasks.ensure_task(NODE, new_ulid(), "gerry")
        assert self.run("task", "list", "--node", NODE) == 0
        assert task["id"] in capsys.readouterr().out
        assert self.run("task", "show", task["id"], "--node", NODE) == 0
        assert '"creator": "gerry"' in capsys.readouterr().out

    def test_clear_prunes_and_expires(self, r4t_home, repo, rig_config, capsys):
        dead = state.agent_dir(NODE, "phil") / ".lock"
        dead.parent.mkdir(parents=True, exist_ok=True)
        dead.write_text(json.dumps({"pid": 99999999, "rig": "t"}), encoding="utf-8")
        stale = tasks.new_task(new_ulid(), "gerry")
        stale["updated_at"] = "2020-01-01T00:00:00Z"
        tasks.atomic_write_json(tasks.task_path(NODE, stale["id"]), stale)
        rc = self.run(
            "clear", "--root", str(repo), "--node", NODE,
            "--rig-config", str(rig_config), "--no-notify",
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "pruned 1 stale lock(s)" in out
        assert "expired 1 thread(s)" in out
        assert tasks.load_task(NODE, stale["id"]) is None

    def test_roster_check_flags_problems(self, r4t_home, repo, rig_config, capsys):
        rc = self.run("roster", "check", "--root", str(repo), "--rig-config", str(rig_config))
        assert rc == 1
        assert "Broken" in capsys.readouterr().out

    def test_roster_check_clean(self, r4t_home, tmp_path, rig_config, capsys):
        root = tmp_path / "clean-repo"
        root.mkdir()
        (root / "ROSTER.md").write_text(
            textwrap.dedent(
                """\
                ### Gerry
                - **Status:** AI
                - **Rig:** leader
                - **Leader:** yes

                ### Phil
                - **Status:** AI
                - **Rig:** junior-dev
                """
            ),
            encoding="utf-8",
        )
        rc = self.run("roster", "check", "--root", str(root), "--rig-config", str(rig_config))
        assert rc == 0
        assert "OK" in capsys.readouterr().out

    def test_roster_check_missing_leader(self, r4t_home, tmp_path, rig_config, capsys):
        root = tmp_path / "leaderless"
        root.mkdir()
        (root / "ROSTER.md").write_text(
            "### Phil\n- **Status:** AI\n- **Rig:** junior-dev\n", encoding="utf-8"
        )
        rc = self.run("roster", "check", "--root", str(root), "--rig-config", str(rig_config))
        assert rc == 1
        assert "no leader" in capsys.readouterr().out


class TestDefault:
    def run(self, *argv):
        return r4t_main(list(argv))

    def test_no_args_shows_overview(self, r4t_home, repo, rig_config, capsys, monkeypatch):
        r4t_home.mkdir(parents=True, exist_ok=True)
        (r4t_home / "rigs.json").write_text(rig_config.read_text(encoding="utf-8"), encoding="utf-8")
        state.team_dir(NODE).mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(repo)
        rc = self.run()
        assert rc == 0
        out = capsys.readouterr().out
        assert "r4t — Roster For Teams" in out
        assert f"R4T_HOME: {r4t_home}" in out
        assert "Rigs" in out and "junior-dev:" in out
        assert "Commands" in out and "init" in out
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
        assert "(no rigs yet — try: r4t rig add" in out
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
        assert (r4t_home / "rigs.json").is_file()
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
        config_before = (r4t_home / "rigs.json").read_text(encoding="utf-8")
        capsys.readouterr()
        rc = self.run("init", "--root", str(root))
        assert rc == 0
        out = capsys.readouterr().out
        assert "left unchanged" in out
        assert (root / "ROSTER.md").read_text(encoding="utf-8") == roster_before
        assert (r4t_home / "rigs.json").read_text(encoding="utf-8") == config_before
