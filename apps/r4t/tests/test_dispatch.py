from __future__ import annotations

import json
import sys
import textwrap

import state
import tasks
from dispatch import drain, handle_message, run_harness, split_recipient
from harness import Tier
from r4t import main as r4t_main
from ulid import new as new_ulid

NODE = "s1l"


def harness_calls(fake_harness):
    _script, out = fake_harness
    return sorted(out.iterdir())


def read_prompt(path):
    return path.read_text(encoding="utf-8")


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
        rc = handle_message(ctx, "gerry", "s1l:phil", "review the ECS payload")
        assert rc == 0
        calls = harness_calls(fake_harness)
        assert len(calls) == 1
        prompt = read_prompt(calls[0])
        assert "You are Phil" in prompt
        assert "Grumpy, cynical veteran" in prompt
        assert "From: gerry" in prompt
        assert "review the ECS payload" in prompt
        assert "tell s1l:gerry" in prompt
        assert "Neil (Human, tell neil)" in prompt
        assert "tell chatroom '#<room> <message>'" in prompt
        assert sent == []  # silence on success — no auto-ack

    def test_new_task_header_given_at_hop_1(self, ctx, fake_harness):
        handle_message(ctx, "gerry", "s1l:phil", "hi")
        prompt = read_prompt(harness_calls(fake_harness)[0])
        listing = tasks.list_tasks(NODE)
        assert len(listing) == 1
        task = listing[0]
        assert tasks.format_header(task["id"], 1) in prompt
        assert task["creator"] == "gerry"
        assert task["turns"] == 1

    def test_incoming_header_adopted_and_stripped(self, ctx, fake_harness):
        task_id = new_ulid()
        header = tasks.format_header(task_id, 1)
        handle_message(ctx, "gerry", "s1l:gerry", f"{header} continue please")
        prompt = read_prompt(harness_calls(fake_harness)[0])
        assert tasks.format_header(task_id, 2) in prompt
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


class TestHopLimit:
    def test_chain_cut_notifies_creator_once(self, ctx, tells, fake_harness):
        sent, _ = tells
        task_id = new_ulid()
        tasks.ensure_task(NODE, task_id, "gerry")
        header = tasks.format_header(task_id, 2)  # junior-dev hop_limit is 2
        handle_message(ctx, "marcus", f"{NODE}:phil", f"{header} keep going")
        assert not harness_calls(fake_harness)
        assert len(sent) == 1
        agent, body = sent[0]
        assert agent == "gerry"  # original creator, not the hop sender
        assert "chain cut" in body and task_id in body

        handle_message(ctx, "marcus", f"{NODE}:phil", f"{header} again")
        assert len(sent) == 1  # only told once

    def test_below_limit_runs(self, ctx, fake_harness):
        header = tasks.format_header(new_ulid(), 1)
        handle_message(ctx, "gerry", f"{NODE}:phil", f"{header} ok")
        assert len(harness_calls(fake_harness)) == 1


class TestTurnBudget:
    def test_park_notify_approve_drain(self, ctx, tells, fake_harness):
        sent, _ = tells
        task_id = new_ulid()
        header = tasks.format_header(task_id, 0)
        # junior-dev max_turns_per_task=2 -> each turn costs 0.5
        handle_message(ctx, "gerry", f"{NODE}:phil", f"{header} one")
        handle_message(ctx, "gerry", f"{NODE}:phil", f"{header} two")
        assert len(harness_calls(fake_harness)) == 2

        handle_message(ctx, "gerry", f"{NODE}:phil", f"{header} three")
        assert len(harness_calls(fake_harness)) == 2
        task = tasks.load_task(NODE, task_id)
        assert task["status"] == tasks.STATUS_PARKED
        assert len(tasks.parked_messages(NODE, task_id)) == 1
        budget_tells = [b for _, b in sent if "turn budget" in b]
        assert len(budget_tells) == 1
        assert f"task approve {task_id}" in budget_tells[0]

        handle_message(ctx, "gerry", f"{NODE}:phil", f"{header} four")
        assert len([b for _, b in sent if "turn budget" in b]) == 1  # told once

        tasks.approve(NODE, task_id, turns=2)
        dispatched = drain(ctx)
        assert dispatched == 2
        assert len(harness_calls(fake_harness)) == 4
        assert not tasks.parked_messages(NODE, task_id)

    def test_drained_message_keeps_task_identity(self, ctx, fake_harness):
        task_id = new_ulid()
        header = tasks.format_header(task_id, 1)
        handle_message(ctx, "gerry", f"{NODE}:phil", f"{header} one")
        handle_message(ctx, "gerry", f"{NODE}:phil", f"{header} two")
        handle_message(ctx, "gerry", f"{NODE}:phil", f"{header} three")
        tasks.approve(NODE, task_id, turns=1)
        drain(ctx)
        prompt = read_prompt(harness_calls(fake_harness)[-1])
        assert tasks.format_header(task_id, 2) in prompt


class TestConcurrency:
    def test_tier_limit_parks_to_pending(self, ctx, tells, fake_harness):
        # junior-dev concurrency=1; hold a live lock on another agent.
        other = state.AgentLock(NODE, "marcus")
        assert other.acquire("junior-dev")
        handle_message(ctx, "gerry", f"{NODE}:phil", "blocked")
        assert not harness_calls(fake_harness)
        assert len(state.list_pending(NODE)) == 1

        other.release()
        assert drain(ctx) == 1
        assert len(harness_calls(fake_harness)) == 1
        assert not state.list_pending(NODE)
        assert "blocked" in read_prompt(harness_calls(fake_harness)[0])

    def test_busy_agent_parks_to_pending(self, ctx, fake_harness):
        held = state.AgentLock(NODE, "phil")
        assert held.acquire("junior-dev")
        handle_message(ctx, "gerry", f"{NODE}:phil", "wait your turn")
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
    def test_max_concurrent_caps_across_tiers(
        self, ctx, harness_config, fake_harness
    ):
        _set_throttle(harness_config, max_concurrent=1)
        # A live LEADER-tier lock blocks a junior-dev dispatch: the cap is
        # team-wide, not per tier (junior-dev's own concurrency is free).
        other = state.AgentLock(NODE, "marcus")
        assert other.acquire("leader")
        handle_message(ctx, "gerry", f"{NODE}:phil", "wait for the team slot")
        assert not harness_calls(fake_harness)
        assert len(state.list_pending(NODE)) == 1

        other.release()
        assert drain(ctx) == 1
        assert len(harness_calls(fake_harness)) == 1

    def test_cadence_spaces_turn_starts(self, ctx, harness_config, fake_harness):
        _set_throttle(harness_config, min_seconds_between_turn_starts=3600)
        handle_message(ctx, "gerry", f"{NODE}:phil", "first")
        assert len(harness_calls(fake_harness)) == 1
        handle_message(ctx, "gerry", f"{NODE}:gerry", "too soon")
        assert len(harness_calls(fake_harness)) == 1
        assert len(state.list_pending(NODE)) == 1
        # No long in-process sleep: the message is parked, not blocked on.
        assert drain(ctx) == 1  # redispatch re-parks (still too soon)
        assert len(harness_calls(fake_harness)) == 1
        assert len(state.list_pending(NODE)) == 1

    def test_cadence_allows_after_window(self, ctx, harness_config, fake_harness):
        _set_throttle(harness_config, min_seconds_between_turn_starts=3600)
        state._atomic_write_text(
            state.last_turn_start_path(NODE), "2020-01-01T00:00:00Z\n"
        )
        handle_message(ctx, "gerry", f"{NODE}:phil", "late enough")
        assert len(harness_calls(fake_harness)) == 1


class TestHarnessPools:
    def test_round_robin_rotation_persists(
        self, ctx, r4t_home, repo, tmp_path, fake_harness, tells
    ):
        script, _out = fake_harness
        pool_config = tmp_path / "pool-harnesses.json"
        pool_config.write_text(
            json.dumps(
                {
                    "junior-dev": {
                        "invoke": [
                            [sys.executable, str(script), "{prompt}"],
                            [sys.executable, str(script), "variant-b", "{prompt}"],
                        ],
                        "timeout_seconds": 30,
                        "max_turns_per_task": 10,
                    },
                    "leader": {"invoke": [sys.executable, str(script), "{prompt}"]},
                }
            ),
            encoding="utf-8",
        )
        ctx.config_path = pool_config
        for msg in ("one", "two", "three"):
            handle_message(ctx, "gerry", f"{NODE}:phil", msg)
        calls = harness_calls(fake_harness)
        assert len(calls) == 3
        # Variant B's extra argv shifts the prompt to argv[2]; the recorded
        # argv[1] for those calls is the literal marker "variant-b".
        contents = [read_prompt(c) for c in calls]
        assert contents[0] != "variant-b" and "one" in contents[0]
        assert contents[1] == "variant-b"
        assert contents[2] != "variant-b" and "three" in contents[2]

    def test_single_argv_tier_skips_rotation_state(self, ctx, fake_harness):
        handle_message(ctx, "gerry", f"{NODE}:phil", "plain")
        assert not (state.team_dir(NODE) / "rotation.json").exists()


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
            NODE, {"from": "gerry", "to": "s1l:phil", "task": new_ulid(), "hop": 0, "body": "parked earlier"}
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
        assert "Gerry: tier=leader (pinned)  [leader]" in out
        assert "Phil: tier=junior-dev" in out
        assert "Neil: Human, address=neil" in out
        assert "Broken: DISABLED" in out

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

    def test_task_approve_cli(self, r4t_home, repo, harness_config, capsys):
        task = tasks.ensure_task(NODE, new_ulid(), "gerry")
        rc = self.run("task", "approve", task["id"], "--turns", "3", "--node", NODE)
        assert rc == 0
        assert "approved 3" in capsys.readouterr().out
        assert tasks.load_task(NODE, task["id"])["budget"] > 1.0

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
