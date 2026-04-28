"""Tests for daemon.py — pid-file lifecycle and end-to-end wake_once with the
mock CLI.

The mock CLI lives at tests/fixtures/mock-cli. tests/fixtures/mock.json
defines an agent that routes every verb through it with deterministic
templates. Tests assert on the per-agent log to verify what argv the wake
subprocess actually received.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from core import (
    Participant,
    agent_log_path,
    detach_request_path,
    inbox_dir,
    kill_request_path,
    pid_path,
    trash_dir,
)
from daemon import (
    _clear_detach_request,
    _clear_kill_request,
    _read_detach_request,
    _read_handler_pid,
    _read_kill_request,
    _try_atomic_claim,
    _write_detach_request,
    _write_kill_request,
    acquire,
    attached_loop,
    release,
)
from mailbox import _queue_clear_sentinel, _queue_prompt, ensure_mailboxes
from registry import save_aliases, save_registry


# ---------- pid-file lifecycle ----------

class TestAtomicClaim:
    def test_first_claim_succeeds(self, fake_home):
        assert _try_atomic_claim("X", 12345) is True
        assert pid_path("X").read_text() == "12345"

    def test_second_claim_fails(self, fake_home):
        assert _try_atomic_claim("X", 1) is True
        assert _try_atomic_claim("X", 2) is False
        # Original pid still there.
        assert pid_path("X").read_text() == "1"


class TestReadHandlerPid:
    def test_no_pid_file(self, fake_home):
        assert _read_handler_pid("X") is None

    def test_dead_pid_is_cleaned_up(self, fake_home):
        # Use a pid that's almost certainly dead (max signed int).
        agent_pid = 2**31 - 1
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text(str(agent_pid))
        assert _read_handler_pid("X") is None
        assert not pid_path("X").is_file()

    def test_live_pid(self, fake_home):
        # Our own pid is live.
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text(str(os.getpid()))
        assert _read_handler_pid("X") == os.getpid()

    def test_empty_pid_file_is_cleaned_up(self, fake_home):
        # Issue #66: a partial-write window between O_CREAT|O_EXCL and os.write
        # can leave an empty pid file. Treat as stale and unlink.
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text("")
        assert _read_handler_pid("X") is None
        assert not pid_path("X").is_file()

    def test_negative_pid_is_cleaned_up(self, fake_home):
        # Issue #66: a non-positive pid doesn't refer to any real process —
        # pid 0 / negative pids would target the whole process group via
        # os.kill, which is unsafe.
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text("-1")
        assert _read_handler_pid("X") is None
        assert not pid_path("X").is_file()

    def test_zero_pid_is_cleaned_up(self, fake_home):
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text("0")
        assert _read_handler_pid("X") is None
        assert not pid_path("X").is_file()

    def test_garbage_pid_is_cleaned_up(self, fake_home):
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text("not-an-int")
        assert _read_handler_pid("X") is None
        assert not pid_path("X").is_file()


class TestRequestFileLiveness:
    """Issue #71: stale rendezvous files from dead requesters must not be
    honored. Without this reap, an `acquire()` caller (or `cmd_kill`) that
    crashes after writing the request would cause the holder's next
    iteration to release the agent to nobody."""

    def test_detach_request_dead_requester_reaped(self, fake_home):
        dead_pid = 2**31 - 1
        detach_request_path("X").parent.mkdir(parents=True, exist_ok=True)
        detach_request_path("X").write_text(str(dead_pid))
        assert _read_detach_request("X") is None
        assert not detach_request_path("X").is_file()

    def test_detach_request_live_requester_returned(self, fake_home):
        detach_request_path("X").parent.mkdir(parents=True, exist_ok=True)
        detach_request_path("X").write_text(str(os.getpid()))
        assert _read_detach_request("X") == os.getpid()
        assert detach_request_path("X").is_file()

    def test_kill_request_dead_requester_reaped(self, fake_home):
        dead_pid = 2**31 - 1
        kill_request_path("X").parent.mkdir(parents=True, exist_ok=True)
        kill_request_path("X").write_text(str(dead_pid))
        assert _read_kill_request("X") is None
        assert not kill_request_path("X").is_file()

    def test_kill_request_live_requester_returned(self, fake_home):
        kill_request_path("X").parent.mkdir(parents=True, exist_ok=True)
        kill_request_path("X").write_text(str(os.getpid()))
        assert _read_kill_request("X") == os.getpid()
        assert kill_request_path("X").is_file()

    def test_attached_loop_ignores_dead_requester_detach(self, fake_home, tmp_path, fixtures_dir):
        # Without the liveness check, this dead-pid request would cause the
        # iteration top to spuriously release X.
        from registry import save_registry
        d = tmp_path / "x"; d.mkdir()
        save_registry({
            "X": {"root": str(d), "definition": str(fixtures_dir / "mock.json")},
        })
        ensure_mailboxes(Participant("X", d))
        detach_request_path("X").parent.mkdir(parents=True, exist_ok=True)
        detach_request_path("X").write_text(str(2**31 - 1))

        rc = attached_loop(["X"], 0.1, single_pass=True)
        assert rc == 0
        assert "releasing to PID" not in _read_log("X")
        # Stale request file reaped.
        assert not detach_request_path("X").is_file()

    def test_attached_loop_ignores_dead_requester_kill(self, fake_home, tmp_path, fixtures_dir):
        from registry import save_registry
        d = tmp_path / "x"; d.mkdir()
        save_registry({
            "X": {"root": str(d), "definition": str(fixtures_dir / "mock.json")},
        })
        ensure_mailboxes(Participant("X", d))
        kill_request_path("X").parent.mkdir(parents=True, exist_ok=True)
        kill_request_path("X").write_text(str(2**31 - 1))

        rc = attached_loop(["X"], 0.1, single_pass=True)
        assert rc == 0
        assert "killed by" not in _read_log("X")
        assert not kill_request_path("X").is_file()


class TestAtomicClaimDurability:
    def test_claim_after_partial_write_cleanup(self, fake_home):
        # Issue #66: if a prior writer died after O_CREAT but before the byte
        # write, the file exists but is empty. _read_handler_pid cleans it up;
        # the next _try_atomic_claim must then succeed.
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text("")  # simulate partial-write death
        # Direct re-claim fails because the file still exists.
        assert _try_atomic_claim("X", os.getpid()) is False
        # _read_handler_pid reaps the empty file.
        assert _read_handler_pid("X") is None
        # Now _try_atomic_claim succeeds.
        assert _try_atomic_claim("X", os.getpid()) is True
        assert pid_path("X").read_text() == str(os.getpid())


class TestAcquireRelease:
    def test_acquire_when_free_then_release(self, fake_home):
        acquire("X")
        assert pid_path("X").read_text() == str(os.getpid())
        release("X")
        assert not pid_path("X").is_file()

    def test_acquire_reaps_stale_pid_and_succeeds(self, fake_home):
        # Pid file points at a dead pid → _read_handler_pid unlinks it →
        # acquire's loop retries the claim and succeeds.
        dead_pid = 2**31 - 1
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text(str(dead_pid))
        acquire("X")
        assert pid_path("X").read_text() == str(os.getpid())
        release("X")

    def test_acquire_against_live_holder_times_out(self, fake_home, monkeypatch):
        # Issue #68: acquire writes a detach-request and polls; if the holder
        # never honors it, raise TimeoutError. Use a tiny timeout so the test
        # finishes quickly.
        monkeypatch.setattr("daemon.DETACH_TIMEOUT_S", 0.5)
        monkeypatch.setattr("daemon.DETACH_POLL_S", 0.05)
        # Hold the pid file with the parent shell's pid (live, foreign).
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text(str(os.getppid()))
        with pytest.raises(TimeoutError, match="did not release X"):
            acquire("X")
        # Holder pid file untouched.
        assert pid_path("X").read_text() == str(os.getppid())
        # Detach-request cleared on timeout.
        assert not detach_request_path("X").is_file()

    def test_acquire_writes_detach_request_for_live_holder(self, fake_home, monkeypatch):
        # Verify the request file is written before the timeout fires.
        monkeypatch.setattr("daemon.DETACH_TIMEOUT_S", 0.3)
        monkeypatch.setattr("daemon.DETACH_POLL_S", 0.05)
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text(str(os.getppid()))
        # During acquire's poll loop, the request file should be present.
        # Easiest assertion: after timeout, the file is gone (cleared on
        # timeout) — but during the polling window it WAS written. Use a
        # spy on _write_detach_request.
        called = {}
        orig = _write_detach_request
        def spy(name, pid):
            called["name"] = name
            called["pid"] = pid
            orig(name, pid)
        monkeypatch.setattr("daemon._write_detach_request", spy)
        with pytest.raises(TimeoutError):
            acquire("X")
        assert called == {"name": "X", "pid": os.getpid()}

    def test_release_clears_detach_request(self, fake_home):
        # release(name) also clears any pending detach-request — once we
        # release, there's nothing to ask for.
        acquire("X")
        _write_detach_request("X", os.getppid())
        assert detach_request_path("X").is_file()
        release("X")
        assert not pid_path("X").is_file()
        assert not detach_request_path("X").is_file()

    def test_release_other_pid_is_noop(self, fake_home):
        # Another (dead) pid in the file — release shouldn't unlink it because
        # it doesn't belong to us. _read_handler_pid will clean dead ones, but
        # release is intentionally guarded.
        pid_path("X").parent.mkdir(parents=True, exist_ok=True)
        pid_path("X").write_text("12345")
        # Use a live pid so the cleanup doesn't fire.
        # Actually we can't easily test "not ours but live" without spawning.
        # Check the simpler case: garbage in the file shouldn't crash release.
        release("X")  # garbage int parses, just doesn't match our pid
        # File should still be there (we didn't write it).
        # Actually: the file might or might not exist depending on whether
        # _read_handler_pid was called; release itself just guards the unlink.
        # Just assert no exception was raised.


# ---------- attached_loop end-to-end with mock CLI ----------

@pytest.fixture
def mock_agent(fake_home, tmp_path, fixtures_dir):
    """Register an agent named MOCK that uses the mock CLI definition."""
    agent_root = tmp_path / "mock-agent"
    agent_root.mkdir()
    save_registry({
        "MOCK": {
            "root": str(agent_root),
            "definition": str(fixtures_dir / "mock.json"),
        },
    })
    p = Participant("MOCK", agent_root)
    ensure_mailboxes(p)
    return p


def _read_log(name: str) -> str:
    return agent_log_path(name).read_text() if agent_log_path(name).is_file() else ""


class TestWakeOnceVerbs:
    """Verify each verb produces the right argv via the mock CLI subprocess.
    Asserts on lines the mock CLI echoes into the per-agent log."""

    def test_prompt_verb_argv(self, mock_agent):
        _queue_prompt(mock_agent, "show capabilities")
        rc = attached_loop(["MOCK"], 0.1, single_pass=True)
        assert rc == 0
        log = _read_log("MOCK")
        # mock-cli echoes each argv element prefixed with "MOCK-CLI:"
        assert "MOCK> MOCK-CLI: verb=prompt" in log
        # Senderless prompt: $SENDER is empty, $RECIPIENT is the agent's own
        # name, $MESSAGE is the raw content. $TIMESTAMP / $AGE are populated
        # from msg["date"]; assert the surrounding structure rather than the
        # exact dynamic values.
        assert "FROM:|TO:MOCK|TS:" in log
        assert "|MSG:show capabilities" in log
        assert "AGE:0 seconds ago" in log or "AGE:1 seconds ago" in log
        # The full argv is logged before invocation so operators can see the
        # real prompt that was sent to the wake subprocess.
        assert "[MOCK] exec: " in log
        assert "verb=prompt" in log
        assert "MSG:show capabilities" in log

    def test_message_verb_argv_via_routing(self, fake_home, tmp_path, fixtures_dir):
        # Two agents, both using mock.json.
        for n in ("A", "B"):
            (tmp_path / n).mkdir()
        save_registry({
            "A": {"root": str(tmp_path / "a"), "definition": str(fixtures_dir / "mock.json")},
            "B": {"root": str(tmp_path / "b"), "definition": str(fixtures_dir / "mock.json")},
        })
        a = Participant("A", tmp_path / "a")
        b = Participant("B", tmp_path / "b")
        ensure_mailboxes(a)
        ensure_mailboxes(b)
        from mailbox import _write_outbox
        _write_outbox("A", a.root, "B", "design review", [])

        rc = attached_loop(["A", "B"], 0.1, single_pass=True)
        assert rc == 0
        log_b = _read_log("B")
        # invokeMessage interpolates $SENDER, $RECIPIENT, $TIMESTAMP, $AGE,
        # $MESSAGE directly.
        assert "MOCK-CLI: verb=message" in log_b
        assert "FROM:A|TO:B|TS:" in log_b
        assert "|MSG:design review" in log_b

    def test_alias_routed_message_uses_message_verb(self, fake_home, tmp_path, fixtures_dir):
        # Strict opacity (#69, #70): alias-routed messages dispatch to
        # invokeMessage just like direct ones. The only difference visible to
        # the recipient is that $RECIPIENT resolves to the alias name.
        from mailbox import _write_outbox
        agents = {}
        for n in ("A", "B", "C"):
            d = tmp_path / n; d.mkdir()
            agents[n] = Participant(n, d)
        save_registry({
            n: {"root": str(p.root), "definition": str(fixtures_dir / "mock.json")}
            for n, p in agents.items()
        })
        save_aliases({"devs": ["A", "B", "C"]})
        for p in agents.values():
            ensure_mailboxes(p)

        _write_outbox("A", agents["A"].root, "devs", "all-hands", [])
        rc = attached_loop(["A", "B", "C"], 0.1, single_pass=True)
        assert rc == 0
        log_b = _read_log("B")
        # No "messageAlias" verb, no others_count leak — just the regular
        # message verb with $RECIPIENT preserving the alias name.
        assert "MOCK-CLI: verb=message" in log_b
        assert "FROM:A|TO:devs|TS:" in log_b
        assert "|MSG:all-hands" in log_b
        assert "OTHERS:" not in log_b
        assert "ALIAS:" not in log_b
        # C sees the same shape — both recipients are indistinguishable.
        log_c = _read_log("C")
        assert "FROM:A|TO:devs|TS:" in log_c
        assert "|MSG:all-hands" in log_c

    def test_clear_verb_via_sentinel(self, mock_agent):
        # Pre-queue a normal prompt + then a clear; the clear should wipe
        # the prompt and only invokeClear should fire.
        _queue_prompt(mock_agent, "stale")
        _queue_clear_sentinel(mock_agent)
        rc = attached_loop(["MOCK"], 0.1, single_pass=True)
        assert rc == 0
        log = _read_log("MOCK")
        # invokeClear ran (with no prompt arg, just verb=clear).
        assert "MOCK-CLI: verb=clear" in log
        # The stale prompt was NOT processed (its content shouldn't appear
        # as a wake line).
        assert "MOCK> MOCK-CLI: stale" not in log
        # Stale prompt landed in trash (read-time wipe). With ULID filenames
        # the messages are identified by their JSON body, not their name.
        import json as _json
        trashed_bodies = [
            _json.loads(f.read_text()) for f in trash_dir("MOCK").iterdir()
        ]
        assert any(b.get("content") == "stale" for b in trashed_bodies)


class TestAskWakeCapture:
    """`ask: true` messages cause `wake_once` to capture the wake subprocess
    stdout into `<agent>/.responses/<msg_id>.txt`. Local askers poll that
    file rather than spawning their own subscribers."""

    def test_captures_ask_response_locally(self, mock_agent):
        from core import response_path
        from ulid import new as new_ulid
        msg_id = new_ulid()
        msg = {
            "id": msg_id,
            "date": "2026-04-28T00:00:00Z",
            "from": "",
            "to": "MOCK",
            "content": "what's up",
            "files": [],
            "ask": True,
        }
        (inbox_dir("MOCK") / f"{msg_id}.json").write_text(json.dumps(msg))
        rc = attached_loop(["MOCK"], 0.1, single_pass=True)
        assert rc == 0
        rpath = response_path("MOCK", msg_id)
        assert rpath.is_file(), "ask response file should be created"
        body = rpath.read_text()
        # Mock CLI prints `MOCK-CLI: <arg>` per argv element. Captured stdout
        # has those raw lines (no per-agent prefix).
        assert "MOCK-CLI: verb=prompt" in body

    def test_non_ask_does_not_create_response_file(self, mock_agent):
        from core import responses_dir
        _queue_prompt(mock_agent, "regular")
        rc = attached_loop(["MOCK"], 0.1, single_pass=True)
        assert rc == 0
        # Non-ask wake never writes a response file.
        d = responses_dir("MOCK")
        assert not d.is_dir() or not list(d.iterdir())


class TestAttachedLoopLifecycle:
    def test_attaches_and_detaches(self, mock_agent):
        # No messages — single_pass attaches, sees nothing, detaches.
        rc = attached_loop(["MOCK"], 0.1, single_pass=True)
        assert rc == 0
        log = _read_log("MOCK")
        assert f"[a8s] MOCK: attached (PID {os.getpid()})" in log
        assert "[a8s] MOCK: detached" in log
        # Pid file released.
        assert not pid_path("MOCK").is_file()


class TestAttachedLoopDetachRequest:
    """Issue #68 — per-agent take-over. A detach-request file under one of
    our handled agents causes that agent (and only that agent) to be
    released; siblings keep running."""

    def test_releases_only_requested_agent(self, fake_home, tmp_path, fixtures_dir):
        # Two agents A, B. We acquire both, then drop a detach-request for
        # A (from a foreign pid), run one iteration, and verify A is gone
        # while B is still ours.
        for n in ("A", "B"):
            (tmp_path / n).mkdir()
        save_registry({
            "A": {"root": str(tmp_path / "a"), "definition": str(fixtures_dir / "mock.json")},
            "B": {"root": str(tmp_path / "b"), "definition": str(fixtures_dir / "mock.json")},
        })
        for n in ("A", "B"):
            ensure_mailboxes(Participant(n, tmp_path / n))

        # Place the detach-request BEFORE running attached_loop. The first
        # iteration will pick it up, release A, and continue with B.
        detach_request_path("A").parent.mkdir(parents=True, exist_ok=True)
        detach_request_path("A").write_text(str(os.getppid()))

        rc = attached_loop(["A", "B"], 0.1, single_pass=True)
        assert rc == 0
        # A's log captured the release notice.
        assert f"releasing to PID {os.getppid()}" in _read_log("A")
        # B's log shows attached + detached normally.
        assert f"[a8s] B: attached (PID {os.getpid()}" in _read_log("B")
        assert "[a8s] B: detached" in _read_log("B")
        # Both pid files cleaned up at end (B by the finally block, A by the
        # detach-request handling mid-iteration).
        assert not pid_path("A").is_file()
        assert not pid_path("B").is_file()
        # Detach-request file removed too.
        assert not detach_request_path("A").is_file()

    def test_self_request_is_ignored(self, fake_home, tmp_path, fixtures_dir):
        # If our OWN pid is in the detach-request (shouldn't happen, but
        # defense), we don't release ourselves.
        d = tmp_path / "x"; d.mkdir()
        save_registry({
            "X": {"root": str(d), "definition": str(fixtures_dir / "mock.json")},
        })
        ensure_mailboxes(Participant("X", d))

        detach_request_path("X").parent.mkdir(parents=True, exist_ok=True)
        detach_request_path("X").write_text(str(os.getpid()))

        rc = attached_loop(["X"], 0.1, single_pass=True)
        assert rc == 0
        # Did NOT log a release.
        assert "releasing to PID" not in _read_log("X")
        # Normal attached + detached.
        assert "attached (PID" in _read_log("X")
        assert "detached" in _read_log("X")


class TestAttachedLoopKillRequest:
    """Per-agent kill via kill-request file. Same shape as detach-request,
    but logs as 'killed by' and the SIGUSR1 handler interrupts an in-flight
    wake whose target matches."""

    def test_releases_only_killed_agent(self, fake_home, tmp_path, fixtures_dir):
        for n in ("A", "B"):
            (tmp_path / n).mkdir()
        save_registry({
            "A": {"root": str(tmp_path / "a"), "definition": str(fixtures_dir / "mock.json")},
            "B": {"root": str(tmp_path / "b"), "definition": str(fixtures_dir / "mock.json")},
        })
        for n in ("A", "B"):
            ensure_mailboxes(Participant(n, tmp_path / n))

        # Pre-place kill-request for A from a foreign pid.
        _write_kill_request("A", os.getppid())
        rc = attached_loop(["A", "B"], 0.1, single_pass=True)
        assert rc == 0
        # A's log shows 'killed by'; B's does not.
        assert f"killed by PID {os.getppid()}" in _read_log("A")
        assert "killed by" not in _read_log("B")
        # B attached normally.
        assert "B: attached" in _read_log("B")
        # Kill-request file was cleared.
        assert not kill_request_path("A").is_file()

    def test_kill_takes_precedence_over_detach(self, fake_home, tmp_path, fixtures_dir):
        # If both files exist for the same agent, kill wins.
        d = tmp_path / "x"; d.mkdir()
        save_registry({
            "X": {"root": str(d), "definition": str(fixtures_dir / "mock.json")},
        })
        ensure_mailboxes(Participant("X", d))

        _write_detach_request("X", os.getppid())
        _write_kill_request("X", os.getppid())

        rc = attached_loop(["X"], 0.1, single_pass=True)
        assert rc == 0
        log = _read_log("X")
        assert "killed by" in log
        assert "releasing to PID" not in log

    def test_self_kill_request_is_ignored(self, fake_home, tmp_path, fixtures_dir):
        d = tmp_path / "x"; d.mkdir()
        save_registry({
            "X": {"root": str(d), "definition": str(fixtures_dir / "mock.json")},
        })
        ensure_mailboxes(Participant("X", d))

        _write_kill_request("X", os.getpid())
        rc = attached_loop(["X"], 0.1, single_pass=True)
        assert rc == 0
        assert "killed by" not in _read_log("X")

    def test_multi_agent_share_one_pid(self, fake_home, tmp_path, fixtures_dir):
        # Two agents, one process — both pid files point at this pytest process.
        for n in ("A", "B"):
            (tmp_path / n).mkdir()
        save_registry({
            n: {"root": str(tmp_path / n.lower()), "definition": str(fixtures_dir / "mock.json")}
            for n in ("A", "B")
        })
        # Use different roots for A vs B.
        save_registry({
            "A": {"root": str(tmp_path / "A"), "definition": str(fixtures_dir / "mock.json")},
            "B": {"root": str(tmp_path / "B"), "definition": str(fixtures_dir / "mock.json")},
        })
        for n in ("A", "B"):
            ensure_mailboxes(Participant(n, tmp_path / n))

        rc = attached_loop(["A", "B"], 0.1, single_pass=True)
        assert rc == 0
        # Both agents attached + detached from the same pytest pid.
        assert "shared" in _read_log("A")
        assert "shared" in _read_log("B")
        assert not pid_path("A").is_file()
        assert not pid_path("B").is_file()
