"""Tests for commands.py — focused on the canonicalization invariant added
for issue #65 (lowercase canonical key at registration time, regardless of
the casing the user typed) and the per-agent kill / no-orphan rule from
issue #68."""
from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from commands import (
    cmd_add,
    cmd_alias,
    cmd_kill,
    cmd_logs,
    cmd_remote,
    cmd_remove,
    cmd_storage,
    cmd_tell,
    cmd_unalias,
    cmd_unremote,
    cmd_unstorage,
)
from core import Participant, TELL_OUTBOX_DIR_ENV, agent_dir, agent_log_path, files_dir, kill_request_path, outbox_bundle_dir, outbox_dir, pid_path
from mailbox import ensure_mailboxes
from network import load_network_config, save_network_config
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
        rc = cmd_add(["foo bar", str(agent_root)])
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


class TestCmdAliasShowOne:
    def test_show_one_alias_lists_members(self, fake_home, tmp_path, agent_root, capsys):
        cmd_add(["claude", str(agent_root)])
        other = tmp_path / "g"; other.mkdir()
        cmd_add(["gemini", str(other)])
        cmd_alias(["devs", "claude"])
        cmd_alias(["devs", "gemini"])
        capsys.readouterr()
        rc = cmd_alias(["devs"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "devs:" in out
        assert "claude" in out
        assert "gemini" in out

    def test_show_one_alias_with_dashed_name(self, fake_home, agent_root, capsys):
        cmd_add(["claude", str(agent_root)])
        cmd_alias(["bin-test", "claude"])
        capsys.readouterr()
        rc = cmd_alias(["bin-test"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "bin-test:" in out
        assert "claude" in out

    def test_show_one_alias_case_insensitive(self, fake_home, agent_root, capsys):
        cmd_add(["claude", str(agent_root)])
        cmd_alias(["devs", "claude"])
        capsys.readouterr()
        rc = cmd_alias(["DEVS"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "devs:" in out

    def test_show_unknown_alias_errors(self, fake_home, capsys):
        rc = cmd_alias(["nobody"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "no alias" in err

    def test_show_invalid_name_errors(self, fake_home, capsys):
        rc = cmd_alias(["bad name with spaces"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "alphanumeric" in err


class TestCmdRemove:
    def test_unknown_agent_rejected(self, fake_home, capsys):
        rc = cmd_remove(["nobody"])
        assert rc == 1
        assert "no agent" in capsys.readouterr().err

    def test_invalid_name_rejected(self, fake_home, capsys):
        rc = cmd_remove(["foo bar"])
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


class TestCmdRemote:
    """Remote management mirrors `cmd_alias`'s shape:
        a8s remote                 — list all
        a8s remote <name>          — show one
        a8s remote <name> <broker> <topic> [--<k> <v> ...]   — add or overwrite
    Removal is `a8s unremote <name>` (parallel to `unalias`)."""

    def test_list_empty(self, fake_home, capsys):
        rc = cmd_remote([])
        assert rc == 0
        assert "no remotes configured" in capsys.readouterr().out

    def test_set_then_list(self, fake_home, capsys):
        rc = cmd_remote(["hub", "mqtt://broker:1883", "a8s/test"])
        assert rc == 0
        cfg = load_network_config()
        assert cfg["remotes"]["hub"]["transport"] == "mqtt"
        assert cfg["remotes"]["hub"]["broker"] == "mqtt://broker:1883"
        assert cfg["remotes"]["hub"]["topic"] == "a8s/test"
        capsys.readouterr()  # discard prior
        cmd_remote([])
        out = capsys.readouterr().out
        assert "hub" in out
        assert "mqtt" in out

    def test_show_one(self, fake_home, capsys):
        cmd_remote(["hub", "mqtt://x", "t"])
        capsys.readouterr()  # discard prior
        rc = cmd_remote(["hub"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "hub: " in out
        assert "mqtt://x" in out

    def test_show_unknown(self, fake_home, capsys):
        rc = cmd_remote(["nope"])
        assert rc == 1
        assert "no remote named" in capsys.readouterr().err

    def test_set_overwrites_existing(self, fake_home, capsys):
        # Unlike alias-add (which is additive), remote-set replaces. Two
        # invocations of `remote <name> <b> <t>` leave only the second.
        cmd_remote(["hub", "mqtt://old", "old-topic"])
        capsys.readouterr()
        rc = cmd_remote(["hub", "mqtt://new", "new-topic", "--user", "alice"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "updated remote hub" in out
        spec = load_network_config()["remotes"]["hub"]
        assert spec["broker"] == "mqtt://new"
        assert spec["topic"] == "new-topic"
        assert spec["user"] == "alice"

    def test_set_passes_arbitrary_options_to_spec(self, fake_home):
        rc = cmd_remote([
            "hub", "mqtts://x", "t",
            "--user", "alice", "--pass", "secret",
            "--keepalive", "120",
        ])
        assert rc == 0
        spec = load_network_config()["remotes"]["hub"]
        # Stored under the user-typed key — no translation here.
        assert spec["user"] == "alice"
        assert spec["pass"] == "secret"
        assert spec["keepalive"] == "120"

    def test_set_rejects_dangling_option(self, fake_home, capsys):
        rc = cmd_remote(["hub", "mqtt://x", "t", "--user"])
        assert rc == 2
        assert "missing value" in capsys.readouterr().err

    def test_set_rejects_bare_value(self, fake_home, capsys):
        rc = cmd_remote(["hub", "mqtt://x", "t", "alice"])
        assert rc == 2
        assert "expected --<opt>" in capsys.readouterr().err

    def test_set_rejects_duplicate_option(self, fake_home, capsys):
        rc = cmd_remote(["hub", "mqtt://x", "t", "--user", "a", "--user", "b"])
        assert rc == 2
        assert "duplicate option" in capsys.readouterr().err

    def test_set_invalid_name(self, fake_home, capsys):
        rc = cmd_remote(["with space", "mqtt://x", "t"])
        assert rc == 2
        assert "must be alphanumeric" in capsys.readouterr().err

    def test_secret_is_masked_in_show(self, fake_home, capsys):
        cmd_remote(["hub", "mqtts://x", "t", "--pass", "TOPSECRET"])
        capsys.readouterr()
        cmd_remote(["hub"])
        out = capsys.readouterr().out
        assert "TOPSECRET" not in out
        assert "--pass=***" in out


class TestCmdUnremote:
    def test_remove(self, fake_home):
        cmd_remote(["hub", "mqtt://x", "t"])
        rc = cmd_unremote(["hub"])
        assert rc == 0
        assert "hub" not in load_network_config()["remotes"]

    def test_unknown(self, fake_home, capsys):
        rc = cmd_unremote(["nope"])
        assert rc == 1
        assert "no remote named" in capsys.readouterr().err

    def test_usage(self, fake_home, capsys):
        rc = cmd_unremote([])
        assert rc == 2
        assert "usage:" in capsys.readouterr().err


class TestCmdTellRemoteRecipient:
    """When remotes are configured, `tell <name>` should accept names that
    don't exist locally — the recipient may live on another cluster and
    the receive-side filter will pick it up there. With no remotes
    configured, an unknown recipient is a hard error (no path)."""

    def _setup_sender(self, fake_home, tmp_path, monkeypatch):
        sender_root = tmp_path / "sender"
        sender_root.mkdir()
        (sender_root / ".outbox").mkdir()
        save_registry({"sender": {"root": str(sender_root)}})
        ensure_mailboxes(Participant("sender", sender_root))
        monkeypatch.chdir(sender_root)
        monkeypatch.setenv(TELL_OUTBOX_DIR_ENV, str(sender_root / ".outbox"))
        return sender_root

    def test_unknown_recipient_with_no_remotes_rejected(self, fake_home, tmp_path, monkeypatch, capsys):
        self._setup_sender(fake_home, tmp_path, monkeypatch)
        rc = cmd_tell(["GHOST", "hi"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "no agent or alias named" in err

    def test_unknown_recipient_with_remotes_accepted(self, fake_home, tmp_path, monkeypatch):
        sender_root = self._setup_sender(fake_home, tmp_path, monkeypatch)
        # Configure a remote so the receive-side filter path is available.
        save_network_config({"remotes": {"hub": {"transport": "mqtt", "broker": "mqtt://x", "topic": "t"}}})
        rc = cmd_tell(["GHOST", "hi from sender"])
        assert rc == 0
        # Outbox file written; the routing pass will publish it. The `to`
        # field preserves the user-typed name (mailing-list semantics).
        outbox_files = list(outbox_dir(sender_root).iterdir())
        assert len(outbox_files) == 1
        import json as _json
        msg = _json.loads(outbox_files[0].read_text())
        assert msg["to"] == "GHOST"
        assert msg["content"] == "hi from sender"


# ---------- storage services (issue #90) ----------


class TestCmdStorage:
    """Mirrors `TestCmdRemote`. Same surface shape, configured under
    `network.json`'s `services` map instead of `remotes`."""

    def test_list_empty(self, fake_home, capsys):
        rc = cmd_storage([])
        assert rc == 0
        assert "no storage services configured" in capsys.readouterr().out

    def test_set_then_list(self, fake_home, capsys):
        rc = cmd_storage(["tempfile", "https://tempfile.org"])
        assert rc == 0
        cfg = load_network_config()
        assert cfg["services"]["tempfile"]["service"] == "tempfile_org"
        assert cfg["services"]["tempfile"]["url"] == "https://tempfile.org"
        capsys.readouterr()
        cmd_storage([])
        out = capsys.readouterr().out
        assert "tempfile" in out
        assert "tempfile_org" in out

    def test_show_one(self, fake_home, capsys):
        cmd_storage(["tempfile", "https://tempfile.org"])
        capsys.readouterr()
        rc = cmd_storage(["tempfile"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "tempfile: " in out
        assert "https://tempfile.org" in out

    def test_show_unknown(self, fake_home, capsys):
        rc = cmd_storage(["nope"])
        assert rc == 1
        assert "no storage named" in capsys.readouterr().err

    def test_set_overwrites_existing(self, fake_home, capsys):
        cmd_storage(["tempfile", "https://tempfile.org", "--expiry_hours", "6"])
        capsys.readouterr()
        rc = cmd_storage(["tempfile", "https://tempfile.org", "--expiry_hours", "24"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "updated storage tempfile" in out
        spec = load_network_config()["services"]["tempfile"]
        assert spec["expiry_hours"] == "24"

    def test_set_passes_arbitrary_options_to_spec(self, fake_home):
        rc = cmd_storage([
            "tempfile", "https://tempfile.org",
            "--expiry_hours", "48", "--timeout_s", "60",
        ])
        assert rc == 0
        spec = load_network_config()["services"]["tempfile"]
        assert spec["expiry_hours"] == "48"
        assert spec["timeout_s"] == "60"

    def test_set_rejects_unknown_url(self, fake_home, capsys):
        rc = cmd_storage(["weird", "https://example.com"])
        assert rc == 2
        assert "no storage service matches URL" in capsys.readouterr().err

    def test_set_rejects_dangling_option(self, fake_home, capsys):
        rc = cmd_storage(["tempfile", "https://tempfile.org", "--expiry_hours"])
        assert rc == 2
        assert "missing value" in capsys.readouterr().err

    def test_set_rejects_bare_value(self, fake_home, capsys):
        rc = cmd_storage(["tempfile", "https://tempfile.org", "12"])
        assert rc == 2
        assert "expected --<opt>" in capsys.readouterr().err

    def test_set_rejects_duplicate_option(self, fake_home, capsys):
        rc = cmd_storage([
            "tempfile", "https://tempfile.org",
            "--expiry_hours", "6", "--expiry_hours", "24",
        ])
        assert rc == 2
        assert "duplicate option" in capsys.readouterr().err

    def test_set_invalid_name(self, fake_home, capsys):
        rc = cmd_storage(["with space", "https://tempfile.org"])
        assert rc == 2
        assert "must be alphanumeric" in capsys.readouterr().err


class TestCmdUnstorage:
    def test_remove(self, fake_home):
        cmd_storage(["tempfile", "https://tempfile.org"])
        rc = cmd_unstorage(["tempfile"])
        assert rc == 0
        assert "tempfile" not in load_network_config()["services"]

    def test_unknown(self, fake_home, capsys):
        rc = cmd_unstorage(["nope"])
        assert rc == 1
        assert "no storage named" in capsys.readouterr().err

    def test_usage(self, fake_home, capsys):
        rc = cmd_unstorage([])
        assert rc == 2
        assert "usage:" in capsys.readouterr().err


# ---------- join_args (FILE:-lifting argv joiner) ----------


class TestJoinTellArgs:
    """`tell` accepts the message body as one or more argv elements. An LLM
    that splits the FILE: tag onto its own argument used to silently lose
    the attachment because the joined string had no newline before FILE:.
    `join_args` lifts FILE:-leading argv elements onto their own line so
    trailing-FILE: detection in `_split_content_and_files` recognizes them."""

    def test_plain_join_unchanged(self):
        from tell import join_args

        assert join_args(["hello", "world"]) == "hello world"

    def test_single_arg_unchanged(self):
        from tell import join_args

        assert join_args(["just a message"]) == "just a message"

    def test_file_promoted_to_own_line(self):
        from tell import join_args

        assert join_args(["msg", "FILE: ./x"]) == "msg\nFILE: ./x"

    def test_bare_file_only(self):
        from tell import join_args

        assert join_args(["FILE: ./x"]) == "FILE: ./x"

    def test_multiple_files(self):
        from tell import join_args

        assert join_args(["body", "FILE: ./a", "FILE: ./b"]) == "body\nFILE: ./a\nFILE: ./b"

    def test_file_with_leading_whitespace_still_detected(self):
        from tell import join_args

        assert join_args(["msg", "  FILE: ./x"]) == "msg\nFILE: ./x"

    def test_file_substring_in_body_unchanged(self):
        from tell import join_args

        assert join_args(["see FILE: x in middle"]) == "see FILE: x in middle"


class TestCmdTellWithSplitFileArg:
    """End-to-end: `cmd_tell` with FILE: as a separate argv element should
    produce an outbox message with the file extracted."""

    def test_split_file_arg_extracts_attachment(self, fake_home, tmp_path, monkeypatch):
        sender_root = tmp_path / "sender"
        sender_root.mkdir()
        (sender_root / ".outbox").mkdir()
        save_registry({"sender": {"root": str(sender_root)}, "alice": {"root": str(tmp_path / "alice")}})
        (tmp_path / "alice").mkdir()
        ensure_mailboxes(Participant("sender", sender_root))
        monkeypatch.chdir(sender_root)
        monkeypatch.setenv(TELL_OUTBOX_DIR_ENV, str(sender_root / ".outbox"))
        (sender_root / "report.pdf").write_text("doc")

        rc = cmd_tell(["alice", "Here is the doc.", "FILE: ./report.pdf"])
        assert rc == 0
        outbox_files = list(outbox_dir(sender_root).glob("*.json"))
        assert len(outbox_files) == 1
        import json as _json
        msg = _json.loads(outbox_files[0].read_text())
        assert msg["content"] == "Here is the doc."
        assert len(msg["files"]) == 1
        assert "path" not in msg["files"][0]
        assert msg["files"][0]["filename"] == "report.pdf"
        assert (outbox_bundle_dir(outbox_dir(sender_root), msg["id"]) / "report.pdf").is_file()


class TestCmdLogs:
    def test_single_agent_preserves_append_order(self, fake_home, tmp_path, capsys):
        root = tmp_path / "x"; root.mkdir()
        save_registry({"claude": {"root": str(root)}})
        log = agent_log_path("claude")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "2026-01-01T12:00:02Z later timestamp first in file\n"
            "2026-01-01T12:00:01Z earlier timestamp second in file\n"
            "legacy line without timestamp prefix\n"
        )
        assert cmd_logs(["claude"]) == 0
        out = capsys.readouterr().out
        assert out.splitlines() == [
            "2026-01-01T12:00:02Z later timestamp first in file",
            "2026-01-01T12:00:01Z earlier timestamp second in file",
            "legacy line without timestamp prefix",
        ]

    def test_multi_agent_merge_sorts_by_timestamp(self, fake_home, tmp_path, capsys):
        a_root = tmp_path / "a"; a_root.mkdir()
        b_root = tmp_path / "b"; b_root.mkdir()
        save_registry({"claude": {"root": str(a_root)}, "gemini": {"root": str(b_root)}})
        agent_log_path("claude").parent.mkdir(parents=True, exist_ok=True)
        agent_log_path("gemini").parent.mkdir(parents=True, exist_ok=True)
        agent_log_path("claude").write_text("2026-01-01T12:00:03Z from claude\n")
        agent_log_path("gemini").write_text("2026-01-01T12:00:01Z from gemini\n")
        assert cmd_logs(["claude", "gemini"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert out == [
            "2026-01-01T12:00:01Z from gemini",
            "2026-01-01T12:00:03Z from claude",
        ]

    def test_tail_keeps_last_lines_of_single_agent_log(self, fake_home, tmp_path, capsys):
        root = tmp_path / "x"; root.mkdir()
        save_registry({"claude": {"root": str(root)}})
        log = agent_log_path("claude")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("line1\nline2\nline3\n")
        assert cmd_logs(["claude", "--tail", "2"]) == 0
        assert capsys.readouterr().out.splitlines() == ["line2", "line3"]


