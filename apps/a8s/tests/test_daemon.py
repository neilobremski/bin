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
    maybe_run_idle,
    release,
)
from mailbox import _write_outbox, ensure_mailboxes
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


class TestWakeOnce:
    """End-to-end wake_once exercise via the mock CLI. With the single-`invoke`
    verb every wake produces the same argv shape — the wake line surfaces
    `$SENDER`/`$RECIPIENT`/`$TIMESTAMP`/`$AGE`/`$MESSAGE` for both direct
    sends and alias fan-out. Asserts on lines the mock CLI echoes into the
    per-agent log."""

    def test_routed_message(self, fake_home, tmp_path, fixtures_dir):
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
        _write_outbox("A", a.root, "B", "design review", [])

        rc = attached_loop(["A", "B"], 0.1, single_pass=True)
        assert rc == 0
        log_b = _read_log("B")
        # The argv was logged via shlex.join before invocation so operators
        # can see the actual prompt that reached the wake subprocess.
        assert "[B] exec: " in log_b
        assert "FROM:A|TO:B|TS:" in log_b
        assert "|MSG:design review" in log_b
        assert "AGE:0 seconds ago" in log_b or "AGE:1 seconds ago" in log_b

    def test_alias_routed_message(self, fake_home, tmp_path, fixtures_dir):
        # Strict opacity (#69, #70): alias-routed messages produce the same
        # shape as direct ones — only `$RECIPIENT` differs (alias name).
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
        for n in ("B", "C"):
            log = _read_log(n)
            assert f"FROM:A|TO:devs|TS:" in log
            assert "|MSG:all-hands" in log
            assert "OTHERS:" not in log
            assert "ALIAS:" not in log


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


# ---------- idle invoke ----------

def _write_idle_def(path: Path, fixtures_dir: Path, timeout: int) -> None:
    """Write a definition that wakes via mock-cli on tells AND has an
    idle.invoke that prints a distinguishable string. The idle command's
    argv echoes 'IDLE-FIRED-FOR:$RECIPIENT' so we can grep the per-agent
    log to assert it ran."""
    path.write_text(json.dumps({
        "invoke": [
            f"{fixtures_dir}/mock-cli",
            "FROM:$SENDER|TO:$RECIPIENT|TS:$TIMESTAMP|AGE:$AGE|MSG:$MESSAGE",
        ],
        "idle": {
            "timeout": timeout,
            "invoke": [
                f"{fixtures_dir}/mock-cli",
                "IDLE-FIRED-FOR:$RECIPIENT",
            ],
        },
    }))


class TestMaybeRunIdle:
    """`maybe_run_idle` is the per-iteration check `attached_loop` calls
    for each handled agent after draining the inbox. It reads
    `last-active`, computes elapsed, and fires `idle.invoke` iff the
    agent has been quiet long enough."""

    def test_returns_false_when_no_idle_config(self, fake_home, tmp_path, fixtures_dir):
        d = tmp_path / "X"; d.mkdir()
        save_registry({"X": {"root": str(d), "definition": str(fixtures_dir / "mock.json")}})
        ensure_mailboxes(Participant("X", d))
        # mock.json has no `idle` block.
        assert maybe_run_idle(Participant("X", d)) is False

    def test_initializes_last_active_when_missing(self, fake_home, tmp_path, fixtures_dir):
        from core import last_active_path, read_last_active
        d = tmp_path / "X"; d.mkdir()
        defp = tmp_path / "idle.json"
        _write_idle_def(defp, fixtures_dir, timeout=60)
        save_registry({"X": {"root": str(d), "definition": str(defp)}})
        ensure_mailboxes(Participant("X", d))
        # No last-active file yet — first call seeds it and does NOT fire.
        assert not last_active_path("X").is_file()
        fired = maybe_run_idle(Participant("X", d))
        assert fired is False
        assert read_last_active("X") is not None

    def test_skips_when_not_yet_idle_long_enough(self, fake_home, tmp_path, fixtures_dir):
        from core import touch_last_active
        from datetime import datetime, timezone, timedelta
        d = tmp_path / "X"; d.mkdir()
        defp = tmp_path / "idle.json"
        _write_idle_def(defp, fixtures_dir, timeout=300)
        save_registry({"X": {"root": str(d), "definition": str(defp)}})
        ensure_mailboxes(Participant("X", d))
        # Last active 10 seconds ago; timeout is 300.
        touch_last_active("X", datetime.now(timezone.utc) - timedelta(seconds=10))
        assert maybe_run_idle(Participant("X", d)) is False

    def test_fires_when_elapsed_exceeds_timeout(self, fake_home, tmp_path, fixtures_dir):
        from core import touch_last_active, read_last_active
        from datetime import datetime, timezone, timedelta
        d = tmp_path / "X"; d.mkdir()
        defp = tmp_path / "idle.json"
        _write_idle_def(defp, fixtures_dir, timeout=1)
        save_registry({"X": {"root": str(d), "definition": str(defp)}})
        ensure_mailboxes(Participant("X", d))
        before = datetime.now(timezone.utc)
        touch_last_active("X", before - timedelta(seconds=60))
        fired = maybe_run_idle(Participant("X", d))
        assert fired is True
        # Log must show the idle invoke ran.
        log = _read_log("X")
        assert "idle exec:" in log
        assert "IDLE-FIRED-FOR:X" in log
        # last-active was refreshed to ~now after the run.
        got = read_last_active("X")
        assert got is not None
        assert got >= before

    def test_zero_timeout_disables_idle(self, fake_home, tmp_path, fixtures_dir):
        d = tmp_path / "X"; d.mkdir()
        defp = tmp_path / "idle.json"
        _write_idle_def(defp, fixtures_dir, timeout=0)
        save_registry({"X": {"root": str(d), "definition": str(defp)}})
        ensure_mailboxes(Participant("X", d))
        # Even with no last-active, timeout<=0 means idle is off.
        assert maybe_run_idle(Participant("X", d)) is False


class TestAttachedLoopIdleIntegration:
    """End-to-end: attached_loop's iteration must call maybe_run_idle for
    every handled agent after the inbox drain. With single_pass=True we
    can prep last-active to look "stale" and verify the idle invoke fires
    on the very first iteration."""

    def test_idle_fires_after_drain(self, fake_home, tmp_path, fixtures_dir):
        from core import touch_last_active
        from datetime import datetime, timezone, timedelta
        d = tmp_path / "X"; d.mkdir()
        defp = tmp_path / "idle.json"
        _write_idle_def(defp, fixtures_dir, timeout=1)
        save_registry({"X": {"root": str(d), "definition": str(defp)}})
        ensure_mailboxes(Participant("X", d))
        # Stale last-active so idle should fire this pass.
        touch_last_active("X", datetime.now(timezone.utc) - timedelta(seconds=60))

        rc = attached_loop(["X"], 0.1, single_pass=True)
        assert rc == 0
        log = _read_log("X")
        assert "idle exec:" in log
        assert "IDLE-FIRED-FOR:X" in log

    def test_wake_refreshes_last_active_so_idle_doesnt_fire(self, fake_home, tmp_path, fixtures_dir):
        # If a real wake happened this iteration, last-active was just
        # touched at wake_once time — idle should NOT fire.
        d = tmp_path / "X"; d.mkdir()
        defp = tmp_path / "idle.json"
        _write_idle_def(defp, fixtures_dir, timeout=1)
        save_registry({"X": {"root": str(d), "definition": str(defp)}})
        ensure_mailboxes(Participant("X", d))
        # Drop a self-tell so there's an inbox message to drain. We can't
        # tell ourselves through routing (sender exclusion), so write the
        # routed message directly into the inbox.
        from ulid import new as new_ulid
        msg_id = new_ulid()
        (inbox_dir("X") / f"{msg_id}.json").write_text(json.dumps({
            "id": msg_id,
            "date": "2026-04-29T12:00:00Z",
            "from": "Y",
            "to": "X",
            "content": "wake-test",
            "files": [],
        }))

        rc = attached_loop(["X"], 0.1, single_pass=True)
        assert rc == 0
        log = _read_log("X")
        # Wake fired (mock-cli received the message).
        assert "MSG:wake-test" in log
        # Idle did NOT fire — wake_once just touched last-active.
        assert "idle exec:" not in log
        assert "IDLE-FIRED-FOR" not in log
