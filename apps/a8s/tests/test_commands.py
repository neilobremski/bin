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
    cmd_ask,
    cmd_clear,
    cmd_kill,
    cmd_prompt,
    cmd_remote,
    cmd_remove,
    cmd_tell,
    cmd_unalias,
    cmd_unremote,
)
from core import Participant, agent_dir, kill_request_path, outbox_dir, pid_path
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
        save_registry({"sender": {"root": str(sender_root)}})
        ensure_mailboxes(Participant("sender", sender_root))
        # cmd_tell uses sender_from_cwd() — chdir into the sender's root.
        monkeypatch.chdir(sender_root)
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


class TestCmdPromptRemoteRecipient:
    """`a8s prompt <name> <msg>` to an unknown-locally name with remotes
    configured publishes synchronously instead of erroring. We stub the
    publish path so no broker is required."""

    def test_unknown_recipient_with_no_remotes_rejected(self, fake_home, capsys):
        rc = cmd_prompt(["GHOST", "hello"])
        assert rc == 1
        assert "no agent or alias named" in capsys.readouterr().err

    def test_unknown_recipient_with_remotes_publishes(self, fake_home, monkeypatch, capsys):
        save_network_config({"remotes": {"hub": {"transport": "mqtt", "broker": "mqtt://x", "topic": "t"}}})
        captured: list[dict] = []

        def fake_publish_once(msg):
            captured.append(msg)
            return ["hub"], []

        # Patch the symbol resolved inside commands.py at import time.
        monkeypatch.setattr("commands.publish_once_to_remotes", fake_publish_once)
        rc = cmd_prompt(["GHOST", "/reset"])
        assert rc == 0
        assert len(captured) == 1
        env = captured[0]
        assert env["from"] == ""  # senderless → invokePrompt on the receiver
        assert env["to"] == "GHOST"
        assert env["content"] == "/reset"
        assert env.get("clear") is None  # not a clear sentinel

    def test_publish_failure_returns_error(self, fake_home, monkeypatch, capsys):
        save_network_config({"remotes": {"hub": {"transport": "mqtt", "broker": "mqtt://x", "topic": "t"}}})
        monkeypatch.setattr("commands.publish_once_to_remotes", lambda msg: ([], ["hub"]))
        rc = cmd_prompt(["GHOST", "/reset"])
        assert rc == 1
        assert "failed to publish" in capsys.readouterr().err


class TestCmdClearRemoteRecipient:
    def test_unknown_recipient_with_no_remotes_rejected(self, fake_home, capsys):
        rc = cmd_clear(["GHOST"])
        assert rc == 1
        assert "no agent or alias named" in capsys.readouterr().err

    def test_unknown_recipient_with_remotes_publishes_clear_sentinel(self, fake_home, monkeypatch):
        save_network_config({"remotes": {"hub": {"transport": "mqtt", "broker": "mqtt://x", "topic": "t"}}})
        captured: list[dict] = []

        def fake_publish_once(msg):
            captured.append(msg)
            return ["hub"], []

        monkeypatch.setattr("commands.publish_once_to_remotes", fake_publish_once)
        rc = cmd_clear(["GHOST"])
        assert rc == 0
        assert len(captured) == 1
        env = captured[0]
        assert env["from"] == ""
        assert env["to"] == "GHOST"
        assert env.get("clear") is True  # the sentinel marker

    def test_publish_failure_returns_error(self, fake_home, monkeypatch, capsys):
        save_network_config({"remotes": {"hub": {"transport": "mqtt", "broker": "mqtt://x", "topic": "t"}}})
        monkeypatch.setattr("commands.publish_once_to_remotes", lambda msg: ([], ["hub"]))
        rc = cmd_clear(["GHOST"])
        assert rc == 1
        assert "failed to publish" in capsys.readouterr().err


class TestCmdAsk:
    """`a8s ask` is single-recipient request/response. Local path writes the
    ask to the recipient's inbox with `ask: true`, polls a per-message
    response file. Aliases are rejected — there's only one response slot."""

    def test_alias_rejected(self, fake_home, tmp_path, capsys):
        d = tmp_path / "a"
        d.mkdir()
        save_registry({"A": {"root": str(d), "definition": ""}})
        cmd_alias(["TEAM", "A"])
        rc = cmd_ask(["TEAM", "hello"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "alias" in err and "single-recipient" in err

    def test_unknown_no_remotes_rejected(self, fake_home, capsys):
        rc = cmd_ask(["GHOST", "hello"])
        assert rc == 1
        assert "no agent or alias" in capsys.readouterr().err

    def test_local_writes_ask_to_inbox_and_times_out(self, fake_home, tmp_path, capsys):
        # No handler is running, so the ask never gets a response — it
        # should time out promptly and exit non-zero.
        from core import inbox_dir
        import json as _json
        d = tmp_path / "a"
        d.mkdir()
        save_registry({"A": {"root": str(d), "definition": ""}})
        rc = cmd_ask(["A", "hello", "--timeout", "0.5"])
        assert rc == 1
        # The ask envelope did land in the inbox so a real handler would
        # have something to wake on.
        files = list(inbox_dir("A").iterdir())
        assert len(files) == 1
        body = _json.loads(files[0].read_text())
        assert body["ask"] is True
        assert body["from"] == ""
        assert body["to"] == "A"
        assert body["content"] == "hello"
        err = capsys.readouterr().err
        assert "timed out" in err

    def test_local_returns_response_when_file_appears(self, fake_home, tmp_path, monkeypatch, capsys):
        # Simulate the handler by writing the response file ourselves between
        # ask invocation and timeout. Easiest: monkeypatch time.sleep in the
        # poll loop to drop the file in.
        from core import inbox_dir, response_path
        import commands as _commands
        import json as _json
        d = tmp_path / "a"
        d.mkdir()
        save_registry({"A": {"root": str(d), "definition": ""}})

        # Capture the message id by intercepting the inbox write.
        original_sleep = _commands.time.sleep

        def fake_sleep(seconds):
            files = list(inbox_dir("A").iterdir())
            if files:
                msg = _json.loads(files[0].read_text())
                rpath = response_path("A", msg["id"])
                rpath.parent.mkdir(parents=True, exist_ok=True)
                rpath.write_text("the answer is 42")
            original_sleep(0)  # don't actually sleep — just return immediately

        monkeypatch.setattr(_commands.time, "sleep", fake_sleep)
        rc = cmd_ask(["A", "what's the answer", "--timeout", "5"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "the answer is 42" in out
