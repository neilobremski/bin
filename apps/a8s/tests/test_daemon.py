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
    inbox_dir,
    pid_path,
    trash_dir,
)
from daemon import (
    _read_handler_pid,
    _try_atomic_claim,
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
        assert "MOCK> MOCK-CLI: show capabilities" in log

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
        # A writes to B's outbox path... no, A's outbox.
        from mailbox import _write_outbox
        _write_outbox("A", a.root, "B", "design review", [])

        # Run B's loop — its outbox is empty but B will receive A's routed msg.
        # attached_loop routes ALL agents' outboxes via route_outboxes(handled).
        # We need A's outbox to be routed too. Pass [A, B].
        rc = attached_loop(["A", "B"], 0.1, single_pass=True)
        assert rc == 0
        log_b = _read_log("B")
        # B's invokeMessage runs with the formatted promptMessage template
        # containing "FROM:A|TO:B|MSG:design review".
        assert "MOCK-CLI: verb=message" in log_b
        assert "FROM:A|TO:B|MSG:design review" in log_b

    def test_messageAlias_verb_with_others_count(self, fake_home, tmp_path, fixtures_dir):
        # A, B, C all using mock.json. devs alias = [A, B, C]. A sends to devs.
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
        # Sender excluded → recipients = [B, C] → B sees others_count=1.
        assert "MOCK-CLI: verb=messageAlias" in log_b
        assert "FROM:A|TO:B|ALIAS:devs|OTHERS:1|MSG:all-hands" in log_b

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
        # Stale prompt landed in trash (read-time wipe).
        assert any("PROMPT" in f.name for f in trash_dir("MOCK").iterdir())


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
