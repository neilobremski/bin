"""r4t seat CLI tests — the orchestrator-facing mailbox/voice surface."""
from __future__ import annotations

import json
import os

import state
import tasks
from dispatch import drain
from r4t import main as r4t_main

NODE = "acme"


def _seat(repo, rig_config, *args):
    return r4t_main(
        [
            "seat",
            *args,
            "--node",
            NODE,
            "--root",
            str(repo),
            "--rig-config",
            str(rig_config),
            "--simulate-tell",
        ]
    )


def test_seat_summary(repo, rig_config, r4t_home, capsys):
    state.park_seat_message(NODE, "Neil", "acme:gerry", "hello")
    assert _seat(repo, rig_config) == 0
    out = capsys.readouterr().out
    assert "seat: Neil on acme" in out
    assert "unread: 1" in out
    assert "attached: no" in out
    assert "doorbell: neil" in out


def test_seat_inbox_reads_and_marks(repo, rig_config, r4t_home, capsys):
    state.park_seat_message(NODE, "Neil", "acme:gerry", "first")
    state.park_seat_message(NODE, "Neil", "acme:phil", "second")
    assert _seat(repo, rig_config, "inbox") == 0
    out = capsys.readouterr().out
    assert "from acme:gerry" in out and "first" in out
    assert "from acme:phil" in out and "second" in out
    assert state.list_seat_messages(NODE, "neil") == []
    assert len(state.list_seat_messages(NODE, "neil", read=True)) == 2


def test_seat_inbox_peek_and_json(repo, rig_config, r4t_home, capsys):
    state.park_seat_message(NODE, "Neil", "acme:gerry", "hello")
    assert _seat(repo, rig_config, "inbox", "--peek", "--json") == 0
    envelope = json.loads(capsys.readouterr().out.strip())
    assert envelope["from"] == "acme:gerry"
    assert envelope["content"] == "hello"
    assert len(state.list_seat_messages(NODE, "neil")) == 1


def test_seat_send_creates_task_as_human(repo, rig_config, r4t_home, fake_harness):
    assert _seat(repo, rig_config, "send", "--to", "phil", "ship", "it") == 0
    listing = tasks.list_tasks(NODE)
    assert len(listing) == 1
    assert listing[0]["creator"] == "acme:neil"
    _script, out = fake_harness
    assert len(sorted(out.iterdir())) == 1


def test_seat_send_defaults_to_leader(repo, rig_config, r4t_home, fake_harness, capsys):
    assert _seat(repo, rig_config, "send", "hello") == 0
    _script, out = fake_harness
    calls = sorted(out.iterdir())
    assert len(calls) == 1
    assert "You are Gerry" in calls[0].read_text(encoding="utf-8")


def test_seat_send_rejects_unknown_member(repo, rig_config, r4t_home, capsys):
    assert _seat(repo, rig_config, "send", "--to", "nobody", "hi") == 2
    assert "no dispatchable member" in capsys.readouterr().err


def test_drain_claim_is_exclusive(ctx, r4t_home, fake_harness, monkeypatch):
    state.park_pending(NODE, {"from": "boss", "to": "acme:phil", "body": "go"})
    path = state.list_pending(NODE)[0]
    real_rename = os.rename
    stolen = {}

    def racing_rename(src, dst):
        # A second drainer wins the race for this envelope just before us.
        if not stolen and src == str(path):
            stolen["by"] = path.with_name(f"{path.name}.claim-99")
            real_rename(src, str(stolen["by"]))
        return real_rename(src, dst)

    monkeypatch.setattr("dispatch.os.rename", racing_rename)
    assert drain(ctx) == 0  # lost the claim: no duplicate redispatch
    assert not harness_calls_exist(fake_harness)


def test_drain_recovers_claims_from_dead_drainer(ctx, r4t_home, fake_harness):
    state.park_pending(NODE, {"from": "boss", "to": "acme:phil", "body": "go"})
    path = state.list_pending(NODE)[0]
    os.rename(path, path.with_name(f"{path.name}.claim-999999"))
    assert state.list_pending(NODE) == []
    assert drain(ctx) == 1  # dead drainer's claim re-queued and dispatched
    assert harness_calls_exist(fake_harness)


def harness_calls_exist(fake_harness) -> bool:
    _script, out = fake_harness
    return bool(sorted(out.iterdir()))
