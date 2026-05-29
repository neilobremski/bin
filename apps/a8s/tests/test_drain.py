"""Tests for `a8s drain <name>` — move inbox messages to trash with summary."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from commands import cmd_drain
from core import agent_dir, inbox_dir, trash_dir
from registry import load_registry, save_registry


@pytest.fixture
def registered_agent(fake_home, tmp_path):
    """Register a minimal agent so inbox_dir/trash_dir resolve correctly."""
    name = "alpha"
    root = tmp_path / "agent-root"
    root.mkdir()
    save_registry({name: {"root": str(root)}})
    inbox = inbox_dir(name)
    inbox.mkdir(parents=True)
    return name


def _write_msg(inbox: Path, filename: str, *, sender: str = "bob", content: str = "hello"):
    msg = {"from": sender, "content": content}
    (inbox / filename).write_text(json.dumps(msg))


class TestDrainEmptyInbox:
    def test_returns_zero_and_prints_empty(self, registered_agent, capsys):
        rc = cmd_drain([registered_agent])
        assert rc == 0
        out = capsys.readouterr().out
        assert "inbox empty" in out


class TestDrainMovesToTrash:
    def test_messages_moved_to_trash(self, registered_agent):
        name = registered_agent
        inbox = inbox_dir(name)
        _write_msg(inbox, "msg1.json", sender="bob", content="first")
        _write_msg(inbox, "msg2.json", sender="carol", content="second")

        rc = cmd_drain([name])
        assert rc == 0

        remaining = list(inbox.iterdir())
        assert remaining == []

        trash = trash_dir(name)
        trash_files = sorted(f.name for f in trash.iterdir())
        assert "msg1.json" in trash_files
        assert "msg2.json" in trash_files


class TestDrainSummary:
    def test_sender_and_preview_printed(self, registered_agent, capsys):
        name = registered_agent
        inbox = inbox_dir(name)
        _write_msg(inbox, "msg1.json", sender="bob", content="important task")

        cmd_drain([name])
        out = capsys.readouterr().out
        assert "bob" in out
        assert "important task" in out

    def test_count_in_summary(self, registered_agent, capsys):
        name = registered_agent
        inbox = inbox_dir(name)
        _write_msg(inbox, "a.json", sender="x", content="one")
        _write_msg(inbox, "b.json", sender="y", content="two")

        cmd_drain([name])
        out = capsys.readouterr().out
        assert "drained 2 message(s)" in out


class TestDrainPreviewTruncation:
    def test_content_truncated_at_80_chars(self, registered_agent, capsys):
        name = registered_agent
        inbox = inbox_dir(name)
        long_content = "x" * 200
        _write_msg(inbox, "long.json", sender="bob", content=long_content)

        cmd_drain([name])
        out = capsys.readouterr().out
        # The preview line should contain exactly 80 x's, not all 200
        lines = [l for l in out.splitlines() if "bob" in l]
        assert len(lines) == 1
        assert "x" * 80 in lines[0]
        assert "x" * 81 not in lines[0]


class TestDrainNewlinesReplaced:
    def test_newlines_become_spaces(self, registered_agent, capsys):
        name = registered_agent
        inbox = inbox_dir(name)
        _write_msg(inbox, "nl.json", sender="bob", content="line1\nline2\nline3")

        cmd_drain([name])
        out = capsys.readouterr().out
        lines = [l for l in out.splitlines() if "bob" in l]
        assert len(lines) == 1
        assert "line1 line2 line3" in lines[0]


class TestDrainInvalidJson:
    def test_invalid_json_still_drained(self, registered_agent, capsys):
        name = registered_agent
        inbox = inbox_dir(name)
        (inbox / "bad.json").write_text("{not valid json!!!")

        rc = cmd_drain([name])
        assert rc == 0

        assert not list(inbox.iterdir())
        trash = trash_dir(name)
        assert (trash / "bad.json").exists()

        out = capsys.readouterr().out
        assert "unreadable" in out


class TestDrainNonexistentAgent:
    def test_returns_one_for_unknown_agent(self, fake_home, capsys):
        rc = cmd_drain(["nobody"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "no inbox" in err
