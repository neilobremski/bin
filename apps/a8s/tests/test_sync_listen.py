"""Tests for the file-based tell --sync / a8s sync_listen protocol."""
from __future__ import annotations

import json
import time
from pathlib import Path

from core import Participant, inbox_dir, outbox_dir
from mailbox import _write_outbox, ensure_mailboxes, route_outboxes
from registry import save_registry
from sync_listen import (
    A8S_CONTROL,
    build_cancel_envelope,
    build_listen_envelope,
    handle_a8s_command,
    sync_paths,
    try_sync_capture,
)
from tell import tell_main, write_outbox_control


def _setup_agents(fake_home, tmp_path):
    a_root = tmp_path / "alice"
    b_root = tmp_path / "bob"
    a_root.mkdir()
    b_root.mkdir()
    (a_root / ".outbox").mkdir()
    (b_root / ".outbox").mkdir()
    save_registry({
        "ALICE": {"root": str(a_root)},
        "BOB": {"root": str(b_root)},
    })
    alice = Participant("ALICE", a_root)
    bob = Participant("BOB", b_root)
    ensure_mailboxes(alice)
    ensure_mailboxes(bob)
    return alice, bob


class TestSyncListenCommand:
    def test_listen_writes_ack_and_registers(self, fake_home, tmp_path):
        alice, _ = _setup_agents(fake_home, tmp_path)
        session_id = "01TESTLISTEN000000000000"
        rel = sync_paths(alice.root, session_id)
        rel["base"].mkdir(parents=True, exist_ok=True)

        msg = {
            "id": "ctrl1",
            "to": A8S_CONTROL,
            "command": "sync_listen",
            "args": {
                "session_id": session_id,
                "expect_from": "BOB",
                "reply_path": f".temp/{session_id}.reply.json",
                "listen_ack_path": f".temp/{session_id}.listen.ack",
                "cancel_ack_path": f".temp/{session_id}.cancel.ack",
            },
        }
        assert handle_a8s_command(alice, msg) is True
        assert rel["listen_ack"].is_file()

        listeners_path = Path(fake_home) / ".a8s" / "agents" / "ALICE" / "sync-listeners.json"
        data = json.loads(listeners_path.read_text())
        assert len(data["listeners"]) == 1
        assert data["listeners"][0]["expect_from"] == "BOB"

    def test_cancel_writes_ack_and_removes_listener(self, fake_home, tmp_path):
        alice, _ = _setup_agents(fake_home, tmp_path)
        session_id = "01TESTCANCEL000000000000"
        rel = sync_paths(alice.root, session_id)
        rel["base"].mkdir(parents=True, exist_ok=True)

        listen = build_listen_envelope(
            session_id,
            "BOB",
            f".temp/{session_id}.reply.json",
            f".temp/{session_id}.listen.ack",
            f".temp/{session_id}.cancel.ack",
        )
        handle_a8s_command(alice, {"id": "l1", **listen})

        cancel = build_cancel_envelope(session_id, f".temp/{session_id}.cancel.ack")
        assert handle_a8s_command(alice, {"id": "c1", **cancel}) is True
        assert rel["cancel_ack"].is_file()

        listeners_path = Path(fake_home) / ".a8s" / "agents" / "ALICE" / "sync-listeners.json"
        assert json.loads(listeners_path.read_text())["listeners"] == []


class TestSyncCapture:
    def test_capture_writes_reply_file_not_inbox(self, fake_home, tmp_path):
        alice, _ = _setup_agents(fake_home, tmp_path)
        session_id = "01TESTCAPTURE00000000000"
        rel = sync_paths(alice.root, session_id)
        rel["base"].mkdir(parents=True, exist_ok=True)

        handle_a8s_command(
            alice,
            {
                "id": "l1",
                **build_listen_envelope(
                    session_id,
                    "BOB",
                    f".temp/{session_id}.reply.json",
                    f".temp/{session_id}.listen.ack",
                    f".temp/{session_id}.cancel.ack",
                ),
            },
        )

        reply_msg = {
            "id": "reply1",
            "from": "BOB",
            "to": "ALICE",
            "content": "here is the answer",
            "files": [],
        }
        assert try_sync_capture(alice, reply_msg) is True
        assert rel["reply"].is_file()
        assert json.loads(rel["reply"].read_text())["content"] == "here is the answer"
        assert not list(inbox_dir("ALICE").glob("*.json"))

    def test_route_outboxes_captures_instead_of_inbox(self, fake_home, tmp_path):
        alice, bob = _setup_agents(fake_home, tmp_path)
        session_id = "01TESTROUTE0000000000000"
        rel = sync_paths(alice.root, session_id)
        rel["base"].mkdir(parents=True, exist_ok=True)

        write_outbox_control(
            outbox_dir(alice.root),
            build_listen_envelope(
                session_id,
                "BOB",
                f".temp/{session_id}.reply.json",
                f".temp/{session_id}.listen.ack",
                f".temp/{session_id}.cancel.ack",
            ),
            from_name="ALICE",
        )
        route_outboxes([alice], all_agents=[alice, bob])
        assert rel["listen_ack"].is_file()

        _write_outbox("BOB", bob.root, "ALICE", "sync reply body", [])
        route_outboxes([bob], all_agents=[alice, bob])

        assert rel["reply"].is_file()
        assert json.loads(rel["reply"].read_text())["content"] == "sync reply body"
        assert not list(inbox_dir("ALICE").glob("*.json"))

    def test_scan_inbox_drains_pending_on_listen(self, fake_home, tmp_path):
        alice, bob = _setup_agents(fake_home, tmp_path)
        session_id = "01TESTSCAN0000000000000"
        rel = sync_paths(alice.root, session_id)
        rel["base"].mkdir(parents=True, exist_ok=True)

        _write_outbox("BOB", bob.root, "ALICE", "already waiting", [])
        route_outboxes([bob], all_agents=[alice, bob])
        assert len(list(inbox_dir("ALICE").glob("*.json"))) == 1

        handle_a8s_command(
            alice,
            {
                "id": "l1",
                **build_listen_envelope(
                    session_id,
                    "BOB",
                    f".temp/{session_id}.reply.json",
                    f".temp/{session_id}.listen.ack",
                    f".temp/{session_id}.cancel.ack",
                ),
            },
        )
        assert rel["reply"].is_file()
        assert json.loads(rel["reply"].read_text())["content"] == "already waiting"
        assert not list(inbox_dir("ALICE").glob("*.json"))


class TestSyncListenerExpiry:
    def test_listen_stores_expires_at(self, fake_home, tmp_path):
        alice, _ = _setup_agents(fake_home, tmp_path)
        session_id = "01TESTEXPIRES0000000000"
        rel = sync_paths(alice.root, session_id)
        rel["base"].mkdir(parents=True, exist_ok=True)

        msg = {
            "id": "ctrl1",
            **build_listen_envelope(
                session_id,
                "BOB",
                f".temp/{session_id}.reply.json",
                f".temp/{session_id}.listen.ack",
                f".temp/{session_id}.cancel.ack",
                timeout_sec=60,
            ),
        }
        assert handle_a8s_command(alice, msg) is True

        listeners_path = Path(fake_home) / ".a8s" / "agents" / "ALICE" / "sync-listeners.json"
        listener = json.loads(listeners_path.read_text())["listeners"][0]
        assert listener["timeout_sec"] == 60
        assert listener["expires_at"].endswith("Z")

    def test_expire_stale_listeners_removes_expired(self, fake_home, tmp_path):
        alice, _ = _setup_agents(fake_home, tmp_path)
        from sync_listen import _save_listeners, expire_stale_listeners

        _save_listeners(
            "ALICE",
            [
                {
                    "session_id": "stale",
                    "expect_from": "BOB",
                    "expect_from_names": ["bob"],
                    "reply_path": ".temp/stale.reply.json",
                    "expires_at": "2020-01-01T00:00:00Z",
                },
                {
                    "session_id": "fresh",
                    "expect_from": "BOB",
                    "expect_from_names": ["bob"],
                    "reply_path": ".temp/fresh.reply.json",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            ],
        )
        removed = expire_stale_listeners(alice)
        assert removed == 1
        from sync_listen import _load_listeners

        remaining = _load_listeners("ALICE")
        assert len(remaining) == 1
        assert remaining[0]["session_id"] == "fresh"

    def test_expired_listener_does_not_capture(self, fake_home, tmp_path):
        alice, _ = _setup_agents(fake_home, tmp_path)
        from sync_listen import _save_listeners, expire_stale_listeners

        _save_listeners(
            "ALICE",
            [
                {
                    "session_id": "stale",
                    "expect_from": "BOB",
                    "expect_from_names": ["bob"],
                    "reply_path": ".temp/stale.reply.json",
                    "expires_at": "2020-01-01T00:00:00Z",
                },
            ],
        )
        reply_msg = {
            "id": "reply1",
            "from": "BOB",
            "to": "ALICE",
            "content": "too late",
            "files": [],
        }
        assert try_sync_capture(alice, reply_msg) is False

    def test_route_pass_expires_stale_listeners(self, fake_home, tmp_path):
        alice, bob = _setup_agents(fake_home, tmp_path)
        from sync_listen import _save_listeners, _load_listeners

        _save_listeners(
            "ALICE",
            [
                {
                    "session_id": "stale",
                    "expect_from": "BOB",
                    "expect_from_names": ["bob"],
                    "reply_path": ".temp/stale.reply.json",
                    "expires_at": "2020-01-01T00:00:00Z",
                },
            ],
        )
        route_outboxes([alice, bob], all_agents=[alice, bob])
        assert _load_listeners("ALICE") == []


    def test_receive_envelope_captures_remote_reply(self, fake_home, tmp_path):
        alice, _ = _setup_agents(fake_home, tmp_path)
        session_id = "01TESTREMOTE00000000000"
        rel = sync_paths(alice.root, session_id)
        rel["base"].mkdir(parents=True, exist_ok=True)

        handle_a8s_command(
            alice,
            {
                "id": "l1",
                **build_listen_envelope(
                    session_id,
                    "donahue",
                    f".temp/{session_id}.reply.json",
                    f".temp/{session_id}.listen.ack",
                    f".temp/{session_id}.cancel.ack",
                ),
            },
        )

        from network import receive_envelope
        from ulid import new as new_ulid

        envelope = json.dumps(
            {
                "id": new_ulid(),
                "from": "donahue",
                "to": "ALICE",
                "content": "remote sync reply",
                "files": [],
            }
        ).encode()

        receive_envelope(envelope, [alice], services=None)

        assert rel["reply"].is_file()
        assert json.loads(rel["reply"].read_text())["content"] == "remote sync reply"
        assert not list(inbox_dir("ALICE").glob("*.json"))


class TestTellSyncE2E:
    def test_tell_sync_round_trip(self, fake_home, tmp_path, monkeypatch, capsys):
        alice, bob = _setup_agents(fake_home, tmp_path)
        monkeypatch.chdir(alice.root)

        import tell as tell_mod

        sent_reply = {"done": False}

        def poll_and_route(path: Path, deadline: float, **kwargs: object) -> bool:
            while time.monotonic() < deadline:
                route_outboxes([alice, bob], all_agents=[alice, bob])
                if (
                    not sent_reply["done"]
                    and any((alice.root / ".temp").glob("*.listen.ack"))
                ):
                    sent_reply["done"] = True
                    _write_outbox("BOB", bob.root, "ALICE", "bob says hi", [])
                    route_outboxes([bob], all_agents=[alice, bob])
                if path.is_file():
                    return True
                time.sleep(0.001)
            return False

        monkeypatch.setattr(tell_mod, "_poll_until", poll_and_route)
        monkeypatch.setattr(tell_mod, "SYNC_POLL_INTERVAL", 0.001)

        rc = tell_main(["BOB", "--sync", "--timeout", "10", "hello bob"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "bob says hi" in out

    def test_tell_sync_timeout_cancels_listener(self, fake_home, tmp_path, monkeypatch, capsys):
        alice, bob = _setup_agents(fake_home, tmp_path)
        monkeypatch.chdir(alice.root)

        import tell as tell_mod

        def poll_and_route(path: Path, deadline: float, **kwargs: object) -> bool:
            while time.monotonic() < deadline:
                route_outboxes([alice, bob], all_agents=[alice, bob])
                if path.is_file():
                    return True
                time.sleep(0.001)
            return False

        monkeypatch.setattr(tell_mod, "_poll_until", poll_and_route)
        monkeypatch.setattr(tell_mod, "SYNC_POLL_INTERVAL", 0.001)

        rc = tell_main(["BOB", "--sync", "--timeout", "0.5", "hello"])
        assert rc == 1
        assert "timed out" in capsys.readouterr().err

        listeners_path = Path(fake_home) / ".a8s" / "agents" / "ALICE" / "sync-listeners.json"
        if listeners_path.is_file():
            assert json.loads(listeners_path.read_text())["listeners"] == []

    def test_tell_sync_interrupt_drops_cancel(self, fake_home, tmp_path, monkeypatch, capsys):
        alice, bob = _setup_agents(fake_home, tmp_path)
        monkeypatch.chdir(alice.root)

        import signal
        import tell as tell_mod

        cancels: list[tuple] = []
        real_drop = tell_mod._drop_sync_cancel
        handlers: dict[int, object] = {}
        real_signal = signal.signal

        def track_signal(sig, handler):
            handlers[sig] = handler
            return real_signal(sig, handler)

        def track_cancel(*args):
            cancels.append(args)
            return real_drop(*args)

        monkeypatch.setattr(signal, "signal", track_signal)
        monkeypatch.setattr(tell_mod, "_drop_sync_cancel", track_cancel)

        poll_calls = {"n": 0}

        def poll_and_route(path: Path, deadline: float, interrupted=None) -> bool:
            poll_calls["n"] += 1
            route_outboxes([alice, bob], all_agents=[alice, bob])
            if poll_calls["n"] == 1 and path.name.endswith(".listen.ack"):
                return path.is_file()
            if poll_calls["n"] > 1 and path.name.endswith(".reply.json"):
                handler = handlers.get(signal.SIGINT)
                if callable(handler):
                    handler(signal.SIGINT, None)
                return False
            return path.is_file()

        monkeypatch.setattr(tell_mod, "_poll_until", poll_and_route)
        monkeypatch.setattr(tell_mod, "SYNC_POLL_INTERVAL", 0.001)

        rc = tell_main(["BOB", "--sync", "--timeout", "10", "hello"])
        assert rc == 130
        assert "interrupted" in capsys.readouterr().err
        assert len(cancels) == 1
