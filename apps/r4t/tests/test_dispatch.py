from __future__ import annotations

import errno
import json
import os
import sys
import textwrap
import time
from dataclasses import replace

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
from rig import Rig, RigError, load_rig_config
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


TREE_ROSTER = textwrap.dedent(
    """\
    # Tree Team

    ### Vic
    - **Status:** AI
    - **Rig:** leader
    - **Leader:** yes
    - **Cell:** lead
    - **Lead:** Ned

    ### Ned
    - **Status:** Human
    - **Address:** ned

    ### Ann
    - **Status:** AI
    - **Rig:** junior-dev
    - **Cell:** design
    - **Lead:** Vic

    ### Bea
    - **Status:** AI
    - **Rig:** junior-dev
    - **Cell:** design
    - **Lead:** Ann

    ### Cal
    - **Status:** AI
    - **Rig:** junior-dev
    - **Cell:** build
    - **Lead:** Vic
    """
)


@pytest.fixture
def tree_ctx(r4t_home, tmp_path, chatty_config, tells):
    from dispatch import DispatchContext

    root = tmp_path / "tree-repo"
    root.mkdir()
    (root / "ROSTER.md").write_text(TREE_ROSTER, encoding="utf-8")
    _sent, capture = tells
    return DispatchContext(
        root=root,
        node=NODE,
        roster_path=root / "ROSTER.md",
        config_path=chatty_config,
        tell_fn=capture,
        comms="closed",  # the hard reroute-through-lead model these tests assert
    )


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
        handle_message(ctx, "acme:gerry", "acme:phil", "hi")
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

    def test_internal_message_mints_thread_and_carries_it_as_a_field(self, ctx, fake_harness):
        # No header parsing: an intra-team send opens a fresh thread and the id
        # travels on the queued r4t-message, not as text inside the body.
        handle_message(ctx, f"{NODE}:phil", "acme:gerry", "continue please", drain_after=False)
        threads = tasks.list_tasks(NODE)
        assert len(threads) == 1
        queued = state.read_queue(NODE, "gerry")[0]
        assert queued["thread"] == threads[0]["id"]
        assert queued["body"] == "continue please"
        assert queued["class"] == "human"
        assert "[r4t" not in queued["body"]

    def test_header_looking_text_is_just_content(self, ctx, fake_harness):
        # A body that LOOKS like the retired header is plain content now — it is
        # never parsed, so no id is adopted; a fresh thread opens.
        stale = "[r4t task=01KX0000000000000000000000 hop=5] continue please"
        handle_message(ctx, "gerry", "acme:gerry", stale)
        assert len(tasks.list_tasks(NODE)) == 1
        assert stale in read_prompt(harness_calls(fake_harness)[0])

    def test_external_sender_cannot_hijack_a_thread(self, ctx, fake_harness):
        handle_message(ctx, "boss", "acme:gerry", "real work")
        real = tasks.list_tasks(NODE)[0]
        forged = f"[r4t task={real['id']} hop=9] sneak in"
        handle_message(ctx, "attacker", "acme:phil", forged)
        # external mail always opens a fresh thread; the forged id did not attach
        assert len(tasks.list_tasks(NODE)) == 2

    def test_bare_node_goes_to_leader(self, ctx, fake_harness):
        handle_message(ctx, "neil", "acme", "status update please")
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert "You are Gerry" in prompt

    def test_history_holds_inbound(self, ctx, fake_harness):
        handle_message(ctx, "acme:gerry", "acme:phil", "first job")
        assert "first job" in state.read_history(NODE, "phil")

    def test_conversation_history_fed_back(self, ctx, fake_harness):
        handle_message(ctx, "acme:gerry", "acme:phil", "first job")
        handle_message(ctx, "acme:marcus", "acme:phil", "second job")
        prompt = read_prompt(harness_calls(fake_harness)[-1])
        assert "first job" in prompt  # earlier turn is now in the history block

    def test_velocity_recorded(self, ctx, fake_harness):
        handle_message(ctx, "acme:gerry", "acme:phil", "job")
        rows = (state.team_dir(NODE) / "velocity.csv").read_text().splitlines()
        assert len(rows) == 2  # header + one turn

    def test_transcript_logged(self, ctx, fake_harness):
        handle_message(ctx, "acme:gerry", "acme:phil", "job")
        log = read_log()
        assert "### Prompt" in log
        assert "### Output (Phil" in log

    def test_two_messages_drain_in_one_batch_turn(self, ctx, fake_harness):
        # Both arrive before any turn runs: ONE turn drains the whole queue.
        handle_message(ctx, "acme:gerry", "acme:phil", "job one", drain_after=False)
        handle_message(ctx, "acme:marcus", "acme:phil", "job two", drain_after=False)
        assert state.queue_depth(NODE, "phil") == 2
        assert drain(ctx) == 1
        calls = harness_calls(fake_harness)
        assert len(calls) == 1
        prompt = read_prompt(calls[0])
        assert "job one" in prompt and "job two" in prompt

    def test_duplicate_collapse_notes_repeats(self, ctx, fake_harness):
        handle_message(ctx, "acme:gerry", "acme:phil", "same ping", drain_after=False)
        handle_message(ctx, "acme:gerry", "acme:phil", "same ping", drain_after=False)
        assert state.queue_depth(NODE, "phil") == 1
        assert drain(ctx) == 1
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert "(sent 2 times)" in prompt

    def test_mid_turn_arrival_rides_the_next_turn(self, ctx, fake_harness):
        # A message that lands while a turn is in flight is not in that batch.
        def enqueue_midturn(rig, prompt, cwd, *, env=None, variant=0):
            state.enqueue(NODE, "phil", {"from": "late", "body": "arrived mid-turn", "thread": new_ulid(), "hop": 0})
            return run_harness(rig, prompt, cwd, env=env, variant=variant)

        handle_message(ctx, "acme:gerry", "acme:phil", "first", drain_after=False)
        assert drain(ctx, run_fn=enqueue_midturn) == 1
        assert "arrived mid-turn" not in read_prompt(harness_calls(fake_harness)[0])
        assert state.queue_depth(NODE, "phil") == 1  # it waits for the next turn


class TestRejections:
    def test_unknown_member_dead_letters_and_tells(self, ctx, tells, fake_harness):
        sent, _ = tells
        handle_message(ctx, "acme:gerry", "acme:nobody", "hi")
        assert not harness_calls(fake_harness)
        assert any("no team member named" in b for _, b in sent)
        assert "unknown-recipient" in dead_reasons()

    def test_human_message_parks_in_seat(self, ctx, tells, fake_harness):
        handle_message(ctx, "acme:gerry", "acme:neil", "hi")
        assert not harness_calls(fake_harness)
        assert [m["from"] for m in seat_messages()] == ["acme:gerry"]

    def test_doorbell_copy_is_headerless_egress(self, ctx, tells, fake_harness):
        sent, _ = tells
        handle_message(ctx, f"{NODE}:gerry", "acme:neil", "ship report")
        assert ("neil", "ship report") in sent

    def test_human_doorbell_skipped_when_attached(self, ctx, tells, fake_harness):
        sent, _ = tells
        state.touch_seat_presence(NODE, "Neil")
        handle_message(ctx, "acme:gerry", "acme:neil", "hi")
        assert not sent

    def test_disabled_member_dead_letters(self, ctx, tells, fake_harness):
        sent, _ = tells
        handle_message(ctx, "acme:gerry", "acme:broken", "hi")
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
        handle_message(ctx, "acme:gerry", "acme:phil", "hi")
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
        # Only the top leader (Gerry) may egress, so drive the external send
        # through him — an external message enters at the top and wakes Gerry.
        monkeypatch.setenv("CHATTY_TO", "outsider")
        monkeypatch.setenv("CHATTY_BODY", "the fix is deployed")
        assert run_one(chatty_ctx, "boss", "acme", "deploy the fix") == 1
        envelopes = outbox_envelopes(repo)
        assert len(envelopes) == 1
        envelope = envelopes[0]
        assert envelope["to"] == "outsider"
        assert envelope["x_r4t_class"] == "auto"
        assert envelope["content"] == "the fix is deployed"
        assert not envelope["content"].startswith("[r4t")
        assert not state.staging_dir(NODE, "gerry").exists()

    def test_outbound_attributed_to_history(self, chatty_ctx, chatty_harness, monkeypatch):
        monkeypatch.setenv("CHATTY_TO", "neil")
        monkeypatch.setenv("CHATTY_BODY", "status: done")
        run_one(chatty_ctx, "acme:gerry", "acme:phil", "report status")
        history = state.read_history(NODE, "phil")
        assert "to neil" in history
        assert "status: done" in history

    def test_quota_overflow_dead_letters(self, chatty_ctx, repo, chatty_harness, monkeypatch):
        # Quota is orthogonal to egress; drive it on an intra-team fan-out from
        # phil so the top-leader egress gate never enters the picture.
        monkeypatch.setenv("CHATTY_TO", "gerry")
        monkeypatch.setenv("CHATTY_SENDS", "4")  # max_sends_per_turn is 2
        run_one(chatty_ctx, "acme:gerry", "acme:phil", "fan out")
        assert state.queue_depth(NODE, "gerry") == 2
        assert dead_reasons() == ["quota", "quota"]
        assert outbox_envelopes(repo) == []

    def test_intra_team_release_enqueues_and_drains(
        self, chatty_ctx, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "acme:gerry")
        monkeypatch.setenv("CHATTY_BODY", "please review my patch")
        assert run_one(chatty_ctx, "acme:gerry", "acme:phil", "do the work") == 1
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
        assert run_one(chatty_ctx, "acme:gerry", "acme:phil", "do the work") == 1
        assert outbox_envelopes(repo) == []
        assert state.queue_depth(NODE, "gerry") == 1

    def test_human_recipient_parks_in_seat(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "acme:neil")
        monkeypatch.setenv("CHATTY_BODY", "shipped")
        assert run_one(chatty_ctx, "acme:gerry", "acme:phil", "ship it") == 1
        assert outbox_envelopes(repo) == []
        parked = seat_messages()
        assert [m["from"] for m in parked] == ["acme:phil"]
        assert "shipped" in parked[0]["content"]

    def test_bare_unknown_name_passes_through_external(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "chatroom")
        monkeypatch.setenv("CHATTY_BODY", "#dev hello")
        assert run_one(chatty_ctx, "boss", "acme", "post an update") == 1
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
        assert run_one(chatty_ctx, "acme:gerry", "acme:phil", "movement is broken") == 1
        assert state.queue_depth(NODE, "gerry") == 1
        monkeypatch.setenv("CHATTY_SENDS", "0")
        assert drain_until_quiet(chatty_ctx) == 1
        assert dead_reasons() == []

    def test_reply_elsewhere_leaves_thread_open(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "chatroom")
        monkeypatch.setenv("CHATTY_BODY", "#dev progress update")
        assert run_one(chatty_ctx, "boss", "acme", "post an update") == 1
        assert tasks.list_tasks(NODE)[0]["status"] == tasks.STATUS_OPEN

    def test_released_envelope_claims_namespaced_sender(
        self, chatty_ctx, repo, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "outsider")
        monkeypatch.setenv("CHATTY_BODY", "status: done")
        run_one(chatty_ctx, "boss", "acme", "report status")  # egress via top leader
        assert [e["from"] for e in outbox_envelopes(repo)] == ["acme:gerry"]

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
        handle_message(ctx, "acme:gerry", "acme:phil", "job")
        assert member_budget(ctx, "phil") == pytest.approx(99.0, abs=0.3)
        team = state.budget_level(
            NODE, state.CELL_BUDGET_KEY,
            config.cell_budget_max, config.cell_budget_earn_per_hour,
        )
        assert team == pytest.approx(199.0, abs=0.3)

    def test_empty_member_budget_rests_and_holds_queue(self, ctx, fake_harness):
        empty_member_budget(ctx, "phil")
        handle_message(ctx, "acme:gerry", "acme:phil", "job")
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
        handle_message(ctx, "acme:gerry", "acme:phil", "job")
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
        handle_message(ctx, "acme:gerry", "acme:phil", "job")
        assert len(harness_calls(fake_harness)) == 1

    def test_seat_send_reports_resting(self, ctx):
        from chat import send_as_human

        empty_member_budget(ctx, "phil")
        human = next(m for m in load_roster(ctx.roster_path).members if m.is_human)
        note = send_as_human(ctx, human, "acme:phil", "you there?")
        assert note is not None
        assert "resting" in note and "Phil" in note
        assert state.queue_depth(NODE, "phil") == 1  # message safely queued


def _local_base_config(script) -> dict:
    """A minimal rig config built inline — no `from conftest import`, so it
    resolves the same whether the r4t suite runs alone or alongside a8s, whose
    tests/conftest.py shares the bare module name `conftest`."""
    invoke = [sys.executable, str(script), "{prompt}"]
    return {
        "throttle": {"max_concurrent": 0, "min_seconds_between_turn_starts": 0},
        "cell_budget_max": 200,
        "cell_budget_earn_per_hour": 100,
        "leader": {"invoke": invoke, "timeout_seconds": 30, "budget_max": 100},
        "junior-dev": {"invoke": invoke, "timeout_seconds": 30, "budget_max": 100},
        "pins": {"gerry": "leader"},
    }


def rig_budget_ctx(repo, tmp_path, tells, *, rig_max=2.0, rig_earn=0.001, name="rig-rigs.json"):
    """A ctx whose junior-dev rig declares a small machine-global rig bucket."""
    script = tmp_path / "fake-harness.py"  # created by the fake_harness fixture
    config = _local_base_config(script)
    config["junior-dev"]["rig_budget_max"] = rig_max
    config["junior-dev"]["rig_budget_earn_per_hour"] = rig_earn
    path = tmp_path / name
    path.write_text(json.dumps(config), encoding="utf-8")
    _sent, capture = tells
    return make_ctx(repo, path, capture)


class TestRigBudgets:
    def test_turn_charges_the_rig_bucket(self, repo, fake_harness, tells, tmp_path, r4t_home):
        ctx = rig_budget_ctx(repo, tmp_path, tells, rig_max=10)
        assert run_one(ctx, "acme:gerry", "acme:phil", "job") == 1
        assert state.rig_budget_level("junior-dev", 10, 0.001) == pytest.approx(9.0, abs=0.3)

    def test_empty_rig_bucket_rests_and_holds_queue(self, repo, fake_harness, tells, tmp_path, r4t_home):
        ctx = rig_budget_ctx(repo, tmp_path, tells)
        state.rig_budget_charge("junior-dev", 2, 0.001, 5)  # drain it
        handle_message(ctx, "acme:gerry", "acme:phil", "job")
        assert not harness_calls(fake_harness)
        assert state.queue_depth(NODE, "phil") == 1
        log = read_log()
        assert "RESTING phil" in log and "rig junior-dev exhausted" in log

    def test_rig_bucket_is_shared_across_two_teams(self, repo, fake_harness, tells, tmp_path, r4t_home):
        repo2 = tmp_path / "repo2"
        repo2.mkdir()
        (repo2 / "ROSTER.md").write_text(
            (repo / "ROSTER.md").read_text(encoding="utf-8"), encoding="utf-8"
        )
        _sent, capture = tells
        ctx_a = rig_budget_ctx(repo, tmp_path, tells, rig_max=2, name="a.json")
        cfg_b = tmp_path / "a.json"  # same rig config -> same rig, same bucket
        ctx_b = dispatch.DispatchContext(
            root=repo2, node="beta", roster_path=repo2 / "ROSTER.md",
            config_path=cfg_b, tell_fn=capture,
        )
        # Two turns on two DIFFERENT teams spend the ONE shared rig bucket (2 -> 0).
        assert run_one(ctx_a, "acme:gerry", "acme:phil", "one") == 1
        assert run_one(ctx_b, "beta:gerry", "beta:phil", "two") == 1
        assert state.rig_budget_level("junior-dev", 2, 0.001) == pytest.approx(0.0, abs=0.3)
        # A third turn, on either team, now rests on the exhausted rig.
        handle_message(ctx_a, "acme:gerry", "acme:phil", "three")
        assert state.queue_depth("acme", "phil") == 1

    def test_blank_response_drains_the_rig_bucket(self, repo, fake_harness, tells, tmp_path, r4t_home):
        ctx = rig_budget_ctx(repo, tmp_path, tells, rig_max=10)

        def blank(rig, prompt, cwd, *, env=None, variant=0):
            return 0, "", 1.0, False  # exit 0, not one byte — the quota signal

        run_one(ctx, "acme:gerry", "acme:phil", "hello?", run_fn=blank)
        assert state.rig_budget_level("junior-dev", 10, 0.001) == pytest.approx(0.0, abs=0.05)
        log = read_log()
        assert "QUOTA-SUSPECT phil" in log and "bucket drained" in log

    def test_chrome_only_does_not_drain_the_rig_bucket(self, repo, fake_harness, tells, tmp_path, r4t_home):
        ctx = rig_budget_ctx(repo, tmp_path, tells, rig_max=10)
        chrome = "\x1b[0m\n> build · qwen3:0.6b\n\x1b[0m\n→ Read sample.txt\n"

        def chrome_only(rig, prompt, cwd, *, env=None, variant=0):
            return 0, chrome, 1.0, False

        run_one(ctx, "acme:gerry", "acme:phil", "hello?", run_fn=chrome_only)
        # A turn was charged (phil ran) but the rig bucket only lost its 1 unit,
        # not drained to 0 — chrome is a quiet-but-alive member, not a blank.
        assert state.rig_budget_level("junior-dev", 10, 0.001) == pytest.approx(9.0, abs=0.3)
        assert "QUOTA-SUSPECT" not in read_log()

    def test_blank_without_rig_budget_logs_but_does_not_crash(self, ctx, r4t_home):
        def blank(rig, prompt, cwd, *, env=None, variant=0):
            return 0, "", 1.0, False

        run_one(ctx, "acme:gerry", "acme:phil", "hello?", run_fn=blank)
        assert "QUOTA-SUSPECT phil" in read_log()


def fail_run(rig, prompt, cwd, *, env=None, variant=0):
    return 1, "boom", 0.05, False


def ok_run(rig, prompt, cwd, *, env=None, variant=0):
    return 0, "ok", 0.05, False


def timeout_run(rig, prompt, cwd, *, env=None, variant=0):
    return 0, "hung", 0.05, True


def trip_breaker(ctx, agent="phil", cap=5):
    for i in range(cap):
        assert run_one(ctx, "acme:gerry", f"acme:{agent}", f"attempt {i}", run_fn=fail_run) == 1


class TestFailureBreaker:
    def test_failures_counted_and_cleared_by_clean_turn(self, ctx):
        assert run_one(ctx, "acme:gerry", "acme:phil", "one", run_fn=fail_run) == 1
        assert run_one(ctx, "acme:gerry", "acme:phil", "two", run_fn=timeout_run) == 1
        assert state.read_meta(NODE, "phil")["consecutive_failures"] == 2
        assert run_one(ctx, "acme:gerry", "acme:phil", "three", run_fn=ok_run) == 1
        assert state.read_meta(NODE, "phil")["consecutive_failures"] == 0

    def test_trips_at_cap_then_holds_queue(self, ctx):
        trip_breaker(ctx)
        handle_message(ctx, "acme:gerry", "acme:phil", "blocked", drain_after=False)
        assert drain(ctx, run_fn=ok_run) == 0  # breaker open: no turn
        assert state.queue_depth(NODE, "phil") >= 1  # messages held, not dropped
        assert "BREAKER phil" in read_log()
        assert dead_reasons() == []  # nothing dead-lettered

    def test_half_open_probe_success_closes(self, ctx):
        trip_breaker(ctx)
        state.update_meta(NODE, "phil", last_failure_at="2020-01-01T00:00:00Z")
        assert run_one(ctx, "acme:gerry", "acme:phil", "probe", run_fn=ok_run) == 1
        assert state.read_meta(NODE, "phil")["consecutive_failures"] == 0

    def test_failed_probe_reopens(self, ctx):
        trip_breaker(ctx)
        state.update_meta(NODE, "phil", last_failure_at="2020-01-01T00:00:00Z")
        assert run_one(ctx, "acme:gerry", "acme:phil", "probe", run_fn=fail_run) == 1
        handle_message(ctx, "acme:gerry", "acme:phil", "again", drain_after=False)
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
        handle_message(
            ctx, f"{NODE}:gerry", "acme:phil", "question",
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
        assert parked[0]["content"] == ANSWER.strip()
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
        script, _out = fake_harness
        config = _local_base_config(script)
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
        handle_message(ctx, "acme:gerry", "acme:phil", "job", drain_after=False)
        assert drain(ctx) == 0  # throttle blocks the second start
        assert state.queue_depth(NODE, "phil") == 1
        live.release()
        assert drain(ctx) == 1

    def test_cadence_spaces_turn_starts(self, repo, fake_harness, tells, tmp_path, r4t_home):
        ctx = self._ctx(repo, fake_harness, tells, tmp_path,
                        max_concurrent=0, min_seconds_between_turn_starts=30)
        assert run_one(ctx, "acme:gerry", "acme:phil", "one") == 1
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


class TestMissionReview:
    def _review(self, ctx):
        return run_idle(ctx)["mission_review"]

    def test_no_fire_when_a_queue_is_nonempty(self, ctx, fake_harness):
        # A resting member holds its queue: work remains, so never stalled.
        empty_member_budget(ctx, "phil")
        handle_message(ctx, "acme:gerry", "acme:phil", "job", drain_after=False)
        assert self._review(ctx)["fired"] is False
        assert self._review(ctx)["fired"] is False
        assert not harness_calls(fake_harness)

    def test_no_fire_with_an_open_thread(self, ctx, fake_harness):
        tasks.ensure_task(NODE, new_ulid(), "acme:neil")  # open, but no queued work
        self._review(ctx)
        assert self._review(ctx)["fired"] is False

    def test_no_fire_when_leader_is_resting(self, ctx, fake_harness):
        empty_member_budget(ctx, "gerry")  # the top leader is broke
        self._review(ctx)
        review = self._review(ctx)  # second tick reaches the threshold, then budget-gates
        assert review["fired"] is False and review.get("resting") is True
        assert not harness_calls(fake_harness)

    def test_fires_on_confirmed_stall_with_no_human_comms_line(self, ctx, fake_harness):
        assert self._review(ctx)["fired"] is False  # first stalled tick: below threshold
        review = self._review(ctx)  # second tick fires
        assert review["fired"] is True and review["leader"] == "Gerry"
        prompt = read_prompt(harness_calls(fake_harness)[-1])
        assert "You are Gerry" in prompt
        assert "No communication to the human NEEDS to happen" in prompt

    def test_backoff_resets_on_real_work(self, ctx, fake_harness):
        self._review(ctx)
        assert self._review(ctx)["fired"] is True
        assert state.read_mission_review(NODE)["silent_reviews"] == 1
        handle_message(ctx, "acme:gerry", "acme:phil", "real work")  # a real turn flows
        self._review(ctx)
        st = state.read_mission_review(NODE)
        assert st["silent_reviews"] == 0 and st["stalls"] == 0

    def test_k_silent_reviews_go_dormant(self, ctx, fake_harness):
        # Seed just below the third fire: stalls 7, two prior silent reviews.
        state.write_mission_review(
            NODE, {"stalls": 7, "silent_reviews": 2, "dormant": False, "mission_mtime": 0.0}
        )
        review = self._review(ctx)  # stalls -> 8 == threshold (2<<2); fires, third silent
        assert review["fired"] is True and review["dormant"] is True
        assert state.read_mission_review(NODE)["dormant"] is True
        harness_before = len(harness_calls(fake_harness))
        assert self._review(ctx)["fired"] is False  # dormant: no more nudges
        assert len(harness_calls(fake_harness)) == harness_before

    def test_dormant_rearms_on_mission_change(self, ctx, fake_harness):
        (ctx.root / "MISSION.md").write_text("the mission", encoding="utf-8")
        # Dormant, but with a stale recorded mtime — a MISSION.md change re-arms.
        state.write_mission_review(
            NODE, {"stalls": 0, "silent_reviews": 3, "dormant": True, "mission_mtime": 1.0}
        )
        assert self._review(ctx)["fired"] is False  # re-arm this tick (below threshold again)
        st = state.read_mission_review(NODE)
        assert st["dormant"] is False and st["silent_reviews"] == 0


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

    def test_agy_model_resolved_live_before_turn(self, tmp_path, monkeypatch):
        # The stored argv carries a {model} placeholder; run_harness resolves it
        # against the live list right before spawning.
        seen = {}

        def fake_resolve(query, **_):
            seen["query"] = query
            return "Claude Sonnet 4.6 (Thinking)"

        monkeypatch.setattr(dispatch, "resolve_agy_model", fake_resolve)
        rig = Rig(
            name="brain",
            invoke=["printf", "%s\\n", "--model", "{model}", "{prompt}"],
            model="sonnet",
            model_resolver="agy-live",
        )
        code, out, _dur, timed_out = run_harness(rig, "hello", tmp_path)
        assert code == 0
        assert not timed_out
        assert seen["query"] == "sonnet"
        assert "Claude Sonnet 4.6 (Thinking)" in out
        assert "{model}" not in out

    def test_agy_resolution_failure_fails_turn_loudly(self, tmp_path, monkeypatch):
        def boom(query, **_):
            raise RigError("--model 'banana' matched no agy model")

        monkeypatch.setattr(dispatch, "resolve_agy_model", boom)
        rig = Rig(
            name="brain",
            invoke=["echo", "--model", "{model}", "{prompt}"],
            model="banana",
            model_resolver="agy-live",
        )
        code, out, _dur, _timed = run_harness(rig, "x", tmp_path)
        assert code == 127
        assert "did not resolve" in out
        assert "banana" in out

    def _break_junior_rig(self, rig_config):
        config = json.loads(rig_config.read_text(encoding="utf-8"))
        config["junior-dev"]["invoke"] = ["/no/such/binary-r4t", "{prompt}"]
        rig_config.write_text(json.dumps(config), encoding="utf-8")

    def test_spawn_failure_to_external_sender_tells(self, ctx, repo, tells, fake_harness, rig_config):
        # An external sender enters at the top (gerry, the leader), whose rig is
        # fine — so break the leader rig to force the 127 on the leader's turn.
        config = json.loads(rig_config.read_text(encoding="utf-8"))
        config["leader"]["invoke"] = ["/no/such/binary-r4t", "{prompt}"]
        rig_config.write_text(json.dumps(config), encoding="utf-8")
        sent, _ = tells
        handle_message(ctx, "boss", "acme", "hi")
        assert any("failed to start" in b for _, b in sent)

    def test_spawn_failure_to_intra_team_sender_feeds_error_in_band(
        self, ctx, repo, tells, fake_harness, rig_config
    ):
        # #160: an operational error to an INTRA-team sender is not a headerless
        # a8s tell that mints a fresh task — it is an in-band class=error message
        # on the ORIGINATING thread. No tell leaves the garden; no new thread.
        self._break_junior_rig(rig_config)
        sent, _ = tells
        handle_message(ctx, "acme:gerry", f"{NODE}:phil", "hi", drain_after=False)
        thread = tasks.list_tasks(NODE)[0]["id"]
        assert drain(ctx) == 1  # phil's one turn fails to spawn (127)
        assert sent == []  # nothing left via a8s tell
        errs = state.read_queue(NODE, "gerry")
        assert errs and errs[0]["class"] == "error"
        assert errs[0]["thread"] == thread  # rides the original thread
        assert "failed to start" in errs[0]["body"]
        assert len(tasks.list_tasks(NODE)) == 1  # no fresh thread minted


class TestTeammateScoping:
    def test_flat_roster_lists_whole_team(self, ctx):
        roster = load_roster(ctx.roster_path)
        lines = "\n".join(dispatch._teammate_lines(ctx, roster, roster.find("phil")))
        assert "Gerry" in lines and "Neil" in lines
        assert "Broken" not in lines  # errored member is excluded

    def test_tree_roster_hides_non_adjacent(self, tree_ctx):
        roster = load_roster(tree_ctx.roster_path)
        lines = "\n".join(dispatch._teammate_lines(tree_ctx, roster, roster.find("ann")))
        assert "Vic" in lines and "Bea" in lines and "Ned" in lines
        assert "Cal" not in lines  # the build cell is invisible to a design IC


MISSION_TEXT = "# Mission\n\nBuild the thing. Done when a stranger loves it.\n"
MISSION_HEADING = "## The mission (MISSION.md — outranks every other document)"


class TestMissionInjection:
    def _prompt(self, ctx, name):
        roster = load_roster(ctx.roster_path)
        return dispatch.build_prompt(ctx, roster, roster.find(name), [], Rig(name="t"))

    def test_lead_with_reports_gets_mission(self, tree_ctx):
        (tree_ctx.root / "MISSION.md").write_text(MISSION_TEXT, encoding="utf-8")
        prompt = self._prompt(tree_ctx, "Vic")  # Ann and Cal report to Vic
        assert MISSION_HEADING in prompt
        assert "Done when a stranger loves it" in prompt

    def test_ic_never_gets_mission(self, tree_ctx):
        (tree_ctx.root / "MISSION.md").write_text(MISSION_TEXT, encoding="utf-8")
        prompt = self._prompt(tree_ctx, "Cal")  # Cal has no reports
        assert MISSION_HEADING not in prompt
        assert "Done when a stranger loves it" not in prompt

    def test_flat_roster_injects_only_the_leader(self, ctx):
        (ctx.root / "MISSION.md").write_text(MISSION_TEXT, encoding="utf-8")
        assert MISSION_HEADING in self._prompt(ctx, "Gerry")  # Leader: yes
        assert MISSION_HEADING not in self._prompt(ctx, "Phil")  # plain IC

    def test_missing_mission_no_section_no_error(self, tree_ctx):
        assert not (tree_ctx.root / "MISSION.md").exists()
        prompt = self._prompt(tree_ctx, "Vic")
        assert MISSION_HEADING not in prompt

    def test_prompt_carries_the_two_doctrine_lines(self, ctx):
        prompt = self._prompt(ctx, "Phil")
        assert "the only thing the recipient sees" in prompt
        assert "not done until it is committed" in prompt


class TestTreeEnforcement:
    def test_non_adjacent_tell_reroutes_to_lead(self, tree_ctx, monkeypatch):
        monkeypatch.setenv("CHATTY_TO", "Cal")
        monkeypatch.setenv("CHATTY_BODY", "hey can you help with build")
        # Vic wakes Ann; Ann tries to reach Cal (build cell) — not tree-adjacent.
        assert run_one(tree_ctx, "acme:vic", "acme:ann", "work the design") == 1
        assert state.queue_depth(NODE, "cal") == 0  # Cal never hears from Ann
        assert state.queue_depth(NODE, "vic") == 1  # rerouted up to Ann's lead
        assert "REROUTED" in read_log()
        rerouted = state.claim_queue(NODE, "vic")
        assert rerouted[0]["body"].startswith("[r4t rerouted: Ann -> Cal]")

    def test_reply_to_batch_sender_is_never_rerouted(self, tree_ctx, monkeypatch):
        monkeypatch.setenv("CHATTY_TO", "Cal")
        monkeypatch.setenv("CHATTY_BODY", "here is the answer you asked for")
        # Cal (non-adjacent) messaged Ann this turn; answering Cal is allowed.
        assert run_one(tree_ctx, "acme:cal", "acme:ann", "quick design question") == 1
        assert state.queue_depth(NODE, "cal") == 1  # reply delivered to Cal
        assert state.queue_depth(NODE, "vic") == 0
        assert "REROUTED" not in read_log()

    def test_seat_is_always_reachable(self, tree_ctx, monkeypatch):
        monkeypatch.setenv("CHATTY_TO", "Ned")
        monkeypatch.setenv("CHATTY_BODY", "status update for the seat")
        assert run_one(tree_ctx, "acme:vic", "acme:ann", "report up") == 1
        parked = seat_messages("ned")
        assert parked and "status update" in parked[0]["content"]
        assert "REROUTED" not in read_log()

    def test_adjacent_tell_passes_through(self, tree_ctx, monkeypatch):
        monkeypatch.setenv("CHATTY_TO", "Bea")
        monkeypatch.setenv("CHATTY_BODY", "cell-mate, take a look")
        assert run_one(tree_ctx, "acme:vic", "acme:ann", "work the design") == 1
        assert state.queue_depth(NODE, "bea") == 1  # same-cell delivery, no reroute
        assert "REROUTED" not in read_log()


def _tree_ctx(tmp_path, config_path, tells, **settings):
    root = tmp_path / "tree-repo"
    if not root.exists():
        root.mkdir()
        (root / "ROSTER.md").write_text(TREE_ROSTER, encoding="utf-8")
    _sent, capture = tells
    return dispatch.DispatchContext(
        root=root, node=NODE, roster_path=root / "ROSTER.md",
        config_path=config_path, tell_fn=capture, **settings,
    )


class TestCommsSetting:
    def test_open_delivers_non_adjacent(self, r4t_home, tmp_path, chatty_config, tells, monkeypatch):
        ctx = _tree_ctx(tmp_path, chatty_config, tells)  # comms defaults to open
        monkeypatch.setenv("CHATTY_TO", "Cal")
        monkeypatch.setenv("CHATTY_BODY", "hey can you help with build")
        # Ann -> Cal is not tree-adjacent, but open comms delivers it directly.
        assert run_one(ctx, "acme:vic", "acme:ann", "work the design") == 1
        assert state.queue_depth(NODE, "cal") == 1
        assert "REROUTED" not in read_log()

    def test_open_still_dead_letters_unknown_name(
        self, r4t_home, tmp_path, chatty_config, tells, monkeypatch
    ):
        ctx = _tree_ctx(tmp_path, chatty_config, tells)
        # An explicit internal sub-address that names no member still dead-letters
        # (a bare unknown name is an external address, not an intra-team miss).
        monkeypatch.setenv("CHATTY_TO", "acme:nobody")
        monkeypatch.setenv("CHATTY_BODY", "who are you")
        assert run_one(ctx, "acme:vic", "acme:ann", "work the design") == 1
        assert "unknown-recipient" in dead_reasons()

    def test_closed_reroutes_non_adjacent(
        self, r4t_home, tmp_path, chatty_config, tells, monkeypatch
    ):
        ctx = _tree_ctx(tmp_path, chatty_config, tells, comms="closed")
        monkeypatch.setenv("CHATTY_TO", "Cal")
        monkeypatch.setenv("CHATTY_BODY", "hey can you help with build")
        assert run_one(ctx, "acme:vic", "acme:ann", "work the design") == 1
        assert state.queue_depth(NODE, "cal") == 0
        assert state.queue_depth(NODE, "vic") == 1
        assert "REROUTED" in read_log()


class TestLeaderSeesLateral:
    def test_off_no_copy_to_lead(self, r4t_home, tmp_path, chatty_config, tells, monkeypatch):
        ctx = _tree_ctx(tmp_path, chatty_config, tells)  # leader_sees_lateral off
        monkeypatch.setenv("CHATTY_TO", "Bea")
        monkeypatch.setenv("CHATTY_BODY", "cell-mate take a look")
        assert run_one(ctx, "acme:vic", "acme:ann", "work the design") == 1
        assert "lateral" not in state.read_history(NODE, "vic")

    def test_on_copies_history_no_turn_burned(
        self, r4t_home, tmp_path, chatty_config, tells, monkeypatch
    ):
        ctx = _tree_ctx(tmp_path, chatty_config, tells, leader_sees_lateral=True)
        monkeypatch.setenv("CHATTY_TO", "Bea")
        monkeypatch.setenv("CHATTY_BODY", "cell-mate take a look")
        # Ann -> Bea is lateral; Ann's lead is Vic, who gets a read-only copy.
        assert run_one(ctx, "acme:vic", "acme:ann", "work the design") == 1
        assert state.queue_depth(NODE, "vic") == 0  # no turn queued for the lead
        history = state.read_history(NODE, "vic")
        assert "lateral Ann -> bea" in history
        assert "cell-mate take a look" in history
        assert "LATERAL-COPY" in read_log()


class TestEgressSetting:
    def test_top_leader_egress_on_releases_external(
        self, r4t_home, tmp_path, repo, chatty_config, tells, monkeypatch
    ):
        ctx = _tree_ctx(tmp_path, chatty_config, tells)  # egress defaults on
        monkeypatch.setenv("CHATTY_TO", "outsider")
        monkeypatch.setenv("CHATTY_BODY", "the org speaks")
        assert run_one(ctx, "boss", "acme:vic", "report out") == 1  # vic is top leader
        assert [e["to"] for e in outbox_envelopes(ctx.root)] == ["outsider"]

    def test_egress_off_top_leader_dead_letters(
        self, r4t_home, tmp_path, chatty_config, tells, monkeypatch
    ):
        ctx = _tree_ctx(tmp_path, chatty_config, tells, egress=False)
        monkeypatch.setenv("CHATTY_TO", "outsider")
        monkeypatch.setenv("CHATTY_BODY", "the org tries to speak")
        assert run_one(ctx, "boss", "acme:vic", "report out") == 1
        assert outbox_envelopes(ctx.root) == []
        assert "egress-disabled" in dead_reasons()

    def test_non_top_external_redirects_to_top_leader(
        self, r4t_home, tmp_path, chatty_config, tells, monkeypatch
    ):
        ctx = _tree_ctx(tmp_path, chatty_config, tells)  # egress on, but ann is not top
        monkeypatch.setenv("CHATTY_TO", "outsider")
        monkeypatch.setenv("CHATTY_BODY", "let me tell the world")
        assert run_one(ctx, "acme:vic", "acme:ann", "work the design") == 1
        assert outbox_envelopes(ctx.root) == []
        assert state.queue_depth(NODE, "vic") == 1  # redirected up to the top leader
        assert "EGRESS-REDIRECT" in read_log()


class TestPromptOverrides:
    def _ctx_with_prompts(self, repo, rig_config, tells, tmp_path, prompts):
        p = tmp_path / "defn.json"
        p.write_text(json.dumps({"invoke": ["x"], "prompts": prompts}), encoding="utf-8")
        _sent, capture = tells
        return dispatch.DispatchContext(
            root=repo, node=NODE, roster_path=repo / "ROSTER.md",
            config_path=rig_config, tell_fn=capture, definition_path=p,
        )

    def _prompt(self, ctx, name="phil"):
        roster = load_roster(ctx.roster_path)
        return dispatch.build_prompt(ctx, roster, roster.find(name), [], Rig(name="t"))

    def test_sparse_override_replaces_only_that_key(
        self, r4t_home, repo, rig_config, tells, tmp_path
    ):
        ctx = self._ctx_with_prompts(
            repo, rig_config, tells, tmp_path, {"work_commit": "- COMMIT OR ELSE."}
        )
        prompt = self._prompt(ctx)
        assert "- COMMIT OR ELSE." in prompt
        assert "not done until it is committed" not in prompt
        assert "the only thing the recipient sees" in prompt  # untouched keys default

    def test_intro_substitution_fields_fill_in(
        self, r4t_home, repo, rig_config, tells, tmp_path
    ):
        ctx = self._ctx_with_prompts(
            repo, rig_config, tells, tmp_path, {"intro": "I am {name} on {node}."}
        )
        assert "I am Phil on acme." in self._prompt(ctx)

    def test_missing_definition_yields_all_defaults(self, ctx):
        assert ctx.definition_path is None
        prompt = self._prompt(ctx)
        assert "not done until it is committed" in prompt
        assert "You are Phil" in prompt

    def test_load_overrides_tolerates_absence(self, tmp_path):
        no_prompts = tmp_path / "d.json"
        no_prompts.write_text(json.dumps({"invoke": ["x"]}), encoding="utf-8")
        assert dispatch._load_prompt_overrides(no_prompts) == {}
        assert dispatch._load_prompt_overrides(None) == {}
        assert dispatch._load_prompt_overrides(tmp_path / "nope.json") == {}

    def test_quiet_nudge_override_reaches_the_leader(
        self, r4t_home, repo, rig_config, tells, tmp_path
    ):
        ctx = self._ctx_with_prompts(
            repo, rig_config, tells, tmp_path, {"quiet_nudge": "PING {creator} re {thread}"}
        )
        task = tasks.new_task(new_ulid(), "acme:neil")
        task["updated_at"] = "2020-01-01T00:00:00Z"
        state.atomic_write_json(tasks.task_path(NODE, task["id"]), task)
        roster = load_roster(ctx.roster_path)
        config = load_rig_config(ctx.config_path)
        assert dispatch._quiet_task_sweep(ctx, config, roster) == [task["id"]]
        assert f"PING acme:neil re {task['id']}" in state.read_queue(NODE, "gerry")[0]["body"]


class TestHistoryRigKnobs:
    def _rig(self, tmp_path, **knobs):
        invoke = ["echo", "{prompt}"]
        config = {"worker": {"invoke": invoke, **knobs}}
        path = tmp_path / "rigs.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return load_rig_config(path).rigs["worker"]

    def test_defaults(self, tmp_path):
        rig = self._rig(tmp_path)
        assert rig.history_max_bytes == 8192
        assert rig.history_body_max == 2000
        assert rig.prompt_body_max == 4000

    def test_parsed_values(self, tmp_path):
        rig = self._rig(
            tmp_path, history_max_bytes=65536, history_body_max=8000, prompt_body_max=16000
        )
        assert rig.history_max_bytes == 65536
        assert rig.history_body_max == 8000
        assert rig.prompt_body_max == 16000

    def test_bad_value_flags_rig_error(self, tmp_path):
        rig = self._rig(tmp_path, history_max_bytes=-1)
        assert rig.error and "history_max_bytes" in rig.error

    def test_prompt_body_max_truncates_per_rig(self, ctx):
        roster = load_roster(ctx.roster_path)
        big = "z" * 50
        batch = [{"from": "boss", "thread": "t", "body": big, "class": "human"}]
        prompt = dispatch.build_prompt(
            ctx, roster, roster.find("phil"), batch, Rig(name="t", prompt_body_max=10)
        )
        assert "message truncated by r4t" in prompt


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
            NODE, "gerry",
            {"from": "boss", "to": "acme", "thread": new_ulid(), "hop": 0,
             "class": "auto", "body": "parked earlier"},
        )
        self.run(
            "dispatch", "--root", str(repo), "--from", "boss",
            "--to", "acme", "--message", "live one",
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

    def test_roster_check_warns_on_oversized_cell(self, r4t_home, tmp_path, rig_config, capsys):
        root = tmp_path / "bigcell"
        root.mkdir()
        text = "### Boss\n- **Status:** AI\n- **Rig:** leader\n- **Leader:** yes\n- **Cell:** hq\n"
        for i in range(7):
            text += (
                f"### M{i}\n- **Status:** AI\n- **Rig:** junior-dev\n"
                f"- **Cell:** c\n- **Lead:** Boss\n"
            )
        (root / "ROSTER.md").write_text(text, encoding="utf-8")
        rc = self.run("roster", "check", "--root", str(root), "--rig-config", str(rig_config))
        out = capsys.readouterr().out
        assert rc == 0  # a warning does not fail the check
        assert "warning" in out and "soft cap 6" in out
        assert "1 warning(s)" in out

    def test_roster_check_errors_on_unknown_lead(self, r4t_home, tmp_path, rig_config, capsys):
        root = tmp_path / "badlead"
        root.mkdir()
        (root / "ROSTER.md").write_text(
            "### Boss\n- **Status:** AI\n- **Rig:** leader\n- **Leader:** yes\n"
            "### Kid\n- **Status:** AI\n- **Rig:** junior-dev\n- **Lead:** Ghost\n",
            encoding="utf-8",
        )
        rc = self.run("roster", "check", "--root", str(root), "--rig-config", str(rig_config))
        assert rc == 1
        assert "Ghost" in capsys.readouterr().out

    def _clean_roster(self, root):
        (root / "ROSTER.md").write_text(
            "### Gerry\n- **Status:** AI\n- **Rig:** leader\n- **Leader:** yes\n"
            "### Phil\n- **Status:** AI\n- **Rig:** junior-dev\n",
            encoding="utf-8",
        )

    def test_roster_check_warns_on_oversized_mission(self, r4t_home, tmp_path, rig_config, capsys):
        root = tmp_path / "bigmission"
        root.mkdir()
        self._clean_roster(root)
        (root / "MISSION.md").write_text("\n".join(f"line {i}" for i in range(41)), encoding="utf-8")
        rc = self.run("roster", "check", "--root", str(root), "--rig-config", str(rig_config))
        out = capsys.readouterr().out
        assert rc == 0  # a warning does not fail the check
        assert "MISSION.md is 41 lines" in out
        assert "1 warning(s)" in out

    def test_roster_check_quiet_under_mission_limit(self, r4t_home, tmp_path, rig_config, capsys):
        root = tmp_path / "smallmission"
        root.mkdir()
        self._clean_roster(root)
        # 40 non-blank lines padded with blanks — blank lines are not counted.
        body = "\n".join(f"line {i}" for i in range(40)) + "\n\n\n\n"
        (root / "MISSION.md").write_text(body, encoding="utf-8")
        rc = self.run("roster", "check", "--root", str(root), "--rig-config", str(rig_config))
        out = capsys.readouterr().out
        assert rc == 0
        assert "MISSION.md" not in out
        assert "OK" in out


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


class TestExternalIngressEntersAtTheTop:
    def test_external_member_address_routes_to_leader(self, ctx, fake_harness):
        # An outside agent cannot reach a member by namespace: the topmost
        # leader IS the garden from outside, so a sub-address is ignored.
        handle_message(ctx, "boss", "acme:phil", "do this")
        assert state.queue_depth(NODE, "phil") == 0
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert "You are Gerry" in prompt
        assert tasks.list_tasks(NODE)[0]["creator"] == "boss"  # sender unchanged

    def test_external_bare_node_reaches_leader(self, ctx, fake_harness):
        handle_message(ctx, "boss", "acme", "status?")
        assert "You are Gerry" in read_prompt(harness_calls(fake_harness)[0])

    def test_internal_sender_still_reaches_named_member(self, ctx, fake_harness):
        handle_message(ctx, "acme:gerry", "acme:phil", "do this")
        assert "You are Phil" in read_prompt(harness_calls(fake_harness)[0])

    def test_doorbell_reply_lands_in_seat_path_and_routes_to_leader(self, ctx, fake_harness):
        # "neil" is the roster human's Address: a reply from it is the human
        # speaking, re-stamped to the seat and routed to the leader.
        handle_message(ctx, "neil", "acme:phil", "please do X")
        assert state.queue_depth(NODE, "phil") == 0
        assert "You are Gerry" in read_prompt(harness_calls(fake_harness)[0])
        assert tasks.list_tasks(NODE)[0]["creator"] == "acme:neil"

    def test_doorbell_reply_thread_closes_on_answer(
        self, chatty_ctx, chatty_harness, monkeypatch
    ):
        monkeypatch.setenv("CHATTY_TO", "neil")
        monkeypatch.setenv("CHATTY_BODY", "done: shipped and verified")
        assert run_one(chatty_ctx, "neil", "acme:gerry", "please ship") == 1
        task = tasks.list_tasks(NODE)[0]
        assert task["creator"] == "acme:neil"
        assert task["status"] == tasks.STATUS_CLOSED

    def test_reply_to_human_address_parks_in_seat_and_closes(
        self, chatty_ctx, repo, chatty_harness, tells, monkeypatch
    ):
        # Gerry answers the doorbell reply by its a8s Address ("neil"), the
        # human's other name: it parks in the seat, rings the doorbell, and
        # closes the human's thread — no envelope leaves the garden.
        sent, _ = tells
        monkeypatch.setenv("CHATTY_TO", "neil")
        monkeypatch.setenv("CHATTY_BODY", "done: shipped and verified")
        assert run_one(chatty_ctx, "neil", "acme", "please ship") == 1
        assert outbox_envelopes(repo) == []
        assert [m["from"] for m in seat_messages()] == ["acme:gerry"]
        assert ("neil", "done: shipped and verified") in sent
        task = tasks.list_tasks(NODE)[0]
        assert task["creator"] == "acme:neil"
        assert task["status"] == tasks.STATUS_CLOSED


class TestLiveLogTee:
    def test_run_harness_tees_output_to_live_log(self, tmp_path):
        script = tmp_path / "chatter.py"
        script.write_text("print('line one')\nprint('line two')\n", encoding="utf-8")
        rig = Rig(
            name="t", invoke=[sys.executable, str(script), "{prompt}"], timeout_seconds=10
        )
        live = tmp_path / "live.log"
        env = {**os.environ, "R4T_LIVE_LOG": str(live)}
        code, out, _dur, timed = run_harness(rig, "x", tmp_path, env=env)
        assert code == 0 and not timed
        streamed = live.read_text(encoding="utf-8")
        assert "line one" in streamed and "line two" in streamed
        assert "line one" in out  # still returned in full for staging

    def test_turn_streams_to_member_live_log(self, ctx, fake_harness):
        handle_message(ctx, "acme:gerry", "acme:phil", "hi")
        text = state.live_log_path(NODE, "phil").read_text(encoding="utf-8")
        assert "fake harness ran" in text
