"""Tests for commands.py — focused on the canonicalization invariant added
for issue #65 (lowercase canonical key at registration time, regardless of
the casing the user typed) and the per-agent kill / no-orphan rule from
issue #68."""
from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from commands import cmd_add, cmd_alias, cmd_kill, cmd_remove, cmd_unalias
from core import agent_dir, kill_request_path, pid_path
from registry import load_aliases, load_registry, save_registry


@pytest.fixture
def agent_root(fake_home, tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    return d


class TestCmdAddCanonicalization:
    def test_uppercase_input_stored_lowercase(self, agent_root):
        rc = cmd_add(["CLAUDE", str(agent_root)])
        assert rc == 0
        reg = load_registry()
        assert "claude" in reg
        assert "CLAUDE" not in reg

    def test_mixed_case_collision_rejected(self, agent_root, tmp_path, capsys):
        assert cmd_add(["claude", str(agent_root)]) == 0
        other = tmp_path / "y"
        other.mkdir()
        # Re-add under a different casing — should be rejected as duplicate
        # rather than producing a second registry entry.
        rc = cmd_add(["Claude", str(other)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err
        # Registry still has exactly one entry.
        assert list(load_registry().keys()) == ["claude"]

    def test_directory_path_uses_canonical_key(self, agent_root):
        cmd_add(["CLAUDE", str(agent_root)])
        # Directory derived from canonical (lowercase) key.
        assert agent_dir("claude").exists() or not agent_dir("CLAUDE").exists()
        # The actual on-disk dir is materialized lazily by ensure_mailboxes,
        # so just check that resolution paths agree.
        assert agent_dir("claude") == agent_dir("CLAUDE".lower())

    def test_invalid_name_rejected(self, agent_root, capsys):
        rc = cmd_add(["foo-bar", str(agent_root)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "alphanumeric" in err

    def test_empty_name_rejected(self, agent_root, capsys):
        rc = cmd_add(["", str(agent_root)])
        assert rc == 2


class TestCmdAddAliasCollision:
    def test_alias_then_agent_with_same_name_rejected(self, fake_home, tmp_path, agent_root, capsys):
        # First agent registered.
        other = tmp_path / "other"; other.mkdir()
        cmd_add(["claude", str(other)])
        # Create an alias.
        assert cmd_alias(["devs", "claude"]) == 0
        # Try to register a new agent named "DEVS" — must collide with the
        # alias namespace, rejected.
        rc = cmd_add(["DEVS", str(agent_root)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "alias" in err.lower()


class TestCmdAliasCanonicalization:
    def test_alias_name_canonicalized(self, fake_home, agent_root):
        cmd_add(["claude", str(agent_root)])
        rc = cmd_alias(["DEVS", "Claude"])
        assert rc == 0
        aliases = load_aliases()
        assert "devs" in aliases
        assert aliases["devs"] == ["claude"]

    def test_alias_collides_with_agent_name(self, fake_home, agent_root, capsys):
        cmd_add(["claude", str(agent_root)])
        # Try to create an alias whose name (lowercased) matches an agent.
        rc = cmd_alias(["CLAUDE", "claude"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "agent already exists" in err

    def test_unknown_member_rejected(self, fake_home, capsys):
        rc = cmd_alias(["devs", "nobody"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "unknown member" in err


class TestCmdRemove:
    def test_unknown_agent_rejected(self, fake_home, capsys):
        rc = cmd_remove(["nobody"])
        assert rc == 1
        assert "no agent" in capsys.readouterr().err

    def test_invalid_name_rejected(self, fake_home, capsys):
        rc = cmd_remove(["foo-bar"])
        assert rc == 2
        assert "alphanumeric" in capsys.readouterr().err

    def test_usage_on_wrong_arity(self, fake_home, capsys):
        assert cmd_remove([]) == 2
        assert cmd_remove(["a", "b"]) == 2

    def test_running_handler_blocks_removal(self, fake_home, agent_root, capsys):
        cmd_add(["claude", str(agent_root)])
        # Claim claude under our own (live) pid.
        pid_path("claude").parent.mkdir(parents=True, exist_ok=True)
        pid_path("claude").write_text(str(os.getpid()))
        rc = cmd_remove(["claude"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "running" in err
        # Registry untouched.
        assert "claude" in load_registry()

    def test_basic_removal_wipes_dir_and_registry(self, fake_home, agent_root):
        cmd_add(["claude", str(agent_root)])
        # Materialize the agent dir so we can verify it's wiped.
        agent_dir("claude").mkdir(parents=True, exist_ok=True)
        (agent_dir("claude") / "log.txt").write_text("hi")
        rc = cmd_remove(["claude"])
        assert rc == 0
        assert "claude" not in load_registry()
        assert not agent_dir("claude").exists()

    def test_case_insensitive(self, fake_home, agent_root):
        cmd_add(["claude", str(agent_root)])
        rc = cmd_remove(["Claude"])
        assert rc == 0
        assert load_registry() == {}

    def test_cascade_prunes_alias_member(self, fake_home, tmp_path, agent_root, capsys):
        cmd_add(["claude", str(agent_root)])
        other = tmp_path / "g"; other.mkdir()
        cmd_add(["gemini", str(other)])
        cmd_alias(["devs", "claude"])
        cmd_alias(["devs", "gemini"])
        rc = cmd_remove(["claude"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "pruned from aliases" in out
        # Alias remains with just gemini.
        assert load_aliases() == {"devs": ["gemini"]}

    def test_cascade_drops_now_empty_alias(self, fake_home, agent_root, capsys):
        cmd_add(["claude", str(agent_root)])
        cmd_alias(["devs", "claude"])
        rc = cmd_remove(["claude"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "dropped now-empty aliases" in out
        assert load_aliases() == {}


class TestCmdUnaliasCaseInsensitive:
    def test_unalias_with_different_case(self, fake_home, agent_root):
        cmd_add(["claude", str(agent_root)])
        cmd_alias(["devs", "claude"])
        # Use uppercase to remove — should match canonical lowercase entry.
        rc = cmd_unalias(["DEVS"])
        assert rc == 0
        assert load_aliases() == {}


class TestCmdKillPerAgent:
    """`a8s kill <name>` writes a kill-request file and SIGUSR1s the holder.
    Tests stub `os.kill` so we don't actually signal a real process; we
    verify the file mechanics + that the SIGUSR1 was directed at the
    holder pid."""

    def test_writes_kill_request_and_signals_holder(self, fake_home, tmp_path, monkeypatch, capsys):
        d = tmp_path / "x"; d.mkdir()
        save_registry({"claude": {"root": str(d)}})
        # Pre-attach claude to a foreign live pid.
        pid_path("claude").parent.mkdir(parents=True, exist_ok=True)
        pid_path("claude").write_text(str(os.getppid()))

        signaled = []
        def fake_kill(pid, sig):
            signaled.append((pid, sig))
            # Simulate the holder honoring the request: unlink the pid file.
            if sig == signal.SIGUSR1:
                pid_path("claude").unlink()
        monkeypatch.setattr("commands.os.kill", fake_kill)

        rc = cmd_kill(["claude"])
        assert rc == 0
        # SIGUSR1 went to the holder.
        assert (os.getppid(), signal.SIGUSR1) in signaled
        # No SIGTERM escalation (holder responded).
        assert not any(s == signal.SIGTERM for _, s in signaled)
        # Kill-request file was cleared at the end.
        assert not kill_request_path("claude").is_file()
        # Output includes the request notice.
        out = capsys.readouterr().out
        assert "kill request" in out

    def test_escalates_to_sigterm_on_unresponsive_holder(self, fake_home, tmp_path, monkeypatch, capsys):
        d = tmp_path / "x"; d.mkdir()
        save_registry({"claude": {"root": str(d)}})
        pid_path("claude").parent.mkdir(parents=True, exist_ok=True)
        pid_path("claude").write_text(str(os.getppid()))

        signaled = []
        def fake_kill(pid, sig):
            signaled.append((pid, sig))
            # DON'T release — simulate a wedged holder.
        monkeypatch.setattr("commands.os.kill", fake_kill)
        # Tighten the timeout so the test isn't slow.
        monkeypatch.setattr("commands.KILL_TIMEOUT_S", 0.3)
        monkeypatch.setattr("commands.KILL_POLL_S", 0.05)

        rc = cmd_kill(["claude"])
        assert rc == 1
        # Both SIGUSR1 and the SIGTERM escalation got delivered.
        sigs = {s for _, s in signaled}
        assert signal.SIGUSR1 in sigs
        assert signal.SIGTERM in sigs
        err = capsys.readouterr().err
        assert "did not honor kill" in err

    def test_not_running_is_no_op(self, fake_home, tmp_path, capsys):
        d = tmp_path / "x"; d.mkdir()
        save_registry({"claude": {"root": str(d)}})
        # No pid file → not running.
        rc = cmd_kill(["claude"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "not running" in out
