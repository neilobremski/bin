"""Tests for mailbox.py — routing fan-out, queue helpers, content/file split."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import Participant, inbox_dir, outbox_dir, trash_dir
from mailbox import (
    _queue_clear_sentinel,
    _queue_prompt,
    _split_content_and_files,
    _write_outbox,
    ensure_mailboxes,
    next_inbox_message,
    route_outboxes,
)
from registry import save_aliases, save_registry


# ---------- _split_content_and_files ----------

class TestSplitContentAndFiles:
    def test_no_files(self):
        body, files = _split_content_and_files("hello world")
        assert body == "hello world"
        assert files == []

    def test_single_file(self):
        raw = "see attached\nFILE: /tmp/build.log"
        body, files = _split_content_and_files(raw)
        assert body == "see attached"
        assert files == [{"filename": "build.log", "path": "/tmp/build.log"}]

    def test_multiple_files_preserve_order(self):
        raw = "two attachments\nFILE: /a/b.log\nFILE: /c/d.log"
        body, files = _split_content_and_files(raw)
        assert body == "two attachments"
        assert files == [
            {"filename": "b.log", "path": "/a/b.log"},
            {"filename": "d.log", "path": "/c/d.log"},
        ]

    def test_files_must_be_at_end(self):
        # FILE: lines in the middle are NOT extracted (only trailing ones).
        raw = "FILE: /not-extracted\nbody"
        body, files = _split_content_and_files(raw)
        assert body == "FILE: /not-extracted\nbody"
        assert files == []

    def test_empty_input(self):
        body, files = _split_content_and_files("")
        assert body == ""
        assert files == []


# ---------- ensure_mailboxes ----------

class TestEnsureMailboxes:
    def test_creates_inbox_trash_outbox(self, fake_home, tmp_path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        p = Participant("X", agent_root)
        ensure_mailboxes(p)
        assert inbox_dir("X").is_dir()
        assert trash_dir("X").is_dir()
        assert outbox_dir(agent_root).is_dir()


# ---------- _write_outbox / _queue_prompt / _queue_clear_sentinel ----------

class TestWriteOutbox:
    def test_writes_message_json(self, fake_home, tmp_path):
        path = _write_outbox(
            sender_name="A",
            sender_root=tmp_path,
            to="B",
            content="hi",
            files=[],
        )
        assert path.is_file()
        msg = json.loads(path.read_text())
        assert msg["from"] == "A"
        assert msg["to"] == "B"
        assert msg["content"] == "hi"
        assert msg["files"] == []
        assert "date" in msg


class TestQueuePrompt:
    def test_writes_senderless_to_inbox(self, fake_home, tmp_path):
        agent_root = tmp_path / "x"
        agent_root.mkdir()
        p = Participant("X", agent_root)
        path = _queue_prompt(p, "do the thing")
        assert path.parent == inbox_dir("X")
        msg = json.loads(path.read_text())
        assert msg["from"] == ""
        assert msg["to"] == "X"
        assert msg["content"] == "do the thing"


class TestQueueClearSentinel:
    def test_writes_clear_sentinel(self, fake_home, tmp_path):
        agent_root = tmp_path / "x"
        agent_root.mkdir()
        p = Participant("X", agent_root)
        path = _queue_clear_sentinel(p)
        msg = json.loads(path.read_text())
        assert msg["clear"] is True
        assert msg["from"] == ""

    def test_write_time_wipe_trashes_existing(self, fake_home, tmp_path):
        agent_root = tmp_path / "x"
        agent_root.mkdir()
        p = Participant("X", agent_root)
        # Pre-existing inbox messages.
        _queue_prompt(p, "msg1")
        _queue_prompt(p, "msg2")
        _queue_prompt(p, "msg3")
        assert len(list(inbox_dir("X").iterdir())) == 3

        _queue_clear_sentinel(p)
        # Inbox should now contain ONLY the CLEAR sentinel.
        files = list(inbox_dir("X").iterdir())
        assert len(files) == 1
        assert "_CLEAR" in files[0].name
        # The 3 prior messages are in trash.
        assert len(list(trash_dir("X").iterdir())) == 3


# ---------- route_outboxes ----------

@pytest.fixture
def two_agents(fake_home, tmp_path):
    """Set up two agents, return their Participants."""
    a_root = tmp_path / "a"; a_root.mkdir()
    b_root = tmp_path / "b"; b_root.mkdir()
    save_registry({"A": {"root": str(a_root)}, "B": {"root": str(b_root)}})
    a = Participant("A", a_root)
    b = Participant("B", b_root)
    ensure_mailboxes(a)
    ensure_mailboxes(b)
    return a, b


@pytest.fixture
def three_agents(fake_home, tmp_path):
    """Three agents A, B, C with an alias `devs` -> [A, B, C]."""
    parts = []
    for n in ("A", "B", "C"):
        root = tmp_path / n
        root.mkdir()
        parts.append(Participant(n, root))
    save_registry({p.name: {"root": str(p.root)} for p in parts})
    save_aliases({"devs": ["A", "B", "C"]})
    for p in parts:
        ensure_mailboxes(p)
    return parts


class TestRouteOutboxes:
    def test_single_agent_delivery(self, two_agents):
        a, b = two_agents
        # A writes to B
        _write_outbox("A", a.root, "B", "hi", [])
        n = route_outboxes([a, b], all_agents=[a, b])
        assert n == 1
        # B's inbox has one message
        files = list(inbox_dir("B").iterdir())
        assert len(files) == 1
        msg = json.loads(files[0].read_text())
        assert msg["from"] == "A"
        assert msg["to"] == "B"
        assert msg["content"] == "hi"
        # A's outbox is empty
        assert list(outbox_dir(a.root).iterdir()) == []

    def test_alias_fanout_excludes_sender(self, three_agents):
        a, b, c = three_agents
        # A writes to alias devs (which contains [A, B, C]).
        _write_outbox("A", a.root, "devs", "team meeting", [])
        n = route_outboxes([a, b, c], all_agents=[a, b, c])
        # Sender excluded → 2 recipients (B, C).
        assert n == 2
        assert list(inbox_dir("A").iterdir()) == []
        b_msg = json.loads(next(inbox_dir("B").iterdir()).read_text())
        assert b_msg["alias"] == "devs"
        assert b_msg["others_count"] == 1  # B sees 1 OTHER (C)
        assert b_msg["to"] == "B"
        assert b_msg["from"] == "A"

    def test_alias_fanout_others_count_when_outsider_sends(self, three_agents):
        a, b, c = three_agents
        # An outsider (also A here, but pretending) sends to alias of all 3.
        # If sender is NOT on the alias, all 3 get it. But our setup has
        # A in the alias, so let's test by removing A from the alias.
        save_aliases({"devs": ["B", "C"]})
        _write_outbox("A", a.root, "devs", "msg", [])
        n = route_outboxes([a, b, c], all_agents=[a, b, c])
        # All members (B and C) get it.
        assert n == 2
        b_msg = json.loads(next(inbox_dir("B").iterdir()).read_text())
        assert b_msg["alias"] == "devs"
        assert b_msg["others_count"] == 1  # B sees 1 OTHER (C)

    def test_empty_to_is_rejected_to_trash(self, two_agents):
        a, b = two_agents
        # Write a malformed outbox file with empty `to`.
        outbox = outbox_dir(a.root)
        bad = outbox / "20260101T000000_A.json"
        bad.write_text(json.dumps({
            "from": "A", "to": "", "content": "rogue", "files": [],
        }))
        n = route_outboxes([a, b], all_agents=[a, b])
        assert n == 0
        # Outbox file moved to A's trash.
        assert not bad.is_file()
        assert any("rogue" in f.read_text() for f in trash_dir("A").iterdir())

    def test_unknown_recipient_left_in_outbox(self, two_agents):
        a, b = two_agents
        outbox = outbox_dir(a.root)
        bad = outbox / "20260101T000000_A.json"
        bad.write_text(json.dumps({
            "from": "A", "to": "BOGUS", "content": "x", "files": [],
        }))
        n = route_outboxes([a, b], all_agents=[a, b])
        assert n == 0
        # Unknown-recipient messages are LEFT in the outbox (not trashed) so
        # they can be picked up if the recipient is added later.
        assert bad.is_file()

    def test_from_is_force_overwritten(self, two_agents):
        a, b = two_agents
        # Hand-write an outbox JSON with a SPOOFED from.
        outbox = outbox_dir(a.root)
        f = outbox / "20260101T000000_A.json"
        f.write_text(json.dumps({
            "from": "VICTIM",  # spoofed; should be overwritten with sender
            "to": "B",
            "content": "spoof attempt",
            "files": [],
        }))
        route_outboxes([a, b], all_agents=[a, b])
        delivered = json.loads(next(inbox_dir("B").iterdir()).read_text())
        # Routing forces from = sender's actual name, regardless of the JSON.
        assert delivered["from"] == "A"


class TestNextInboxMessage:
    def test_returns_oldest(self, fake_home, tmp_path):
        agent_root = tmp_path / "x"
        agent_root.mkdir()
        p = Participant("X", agent_root)
        ensure_mailboxes(p)
        first = _queue_prompt(p, "first")
        second = _queue_prompt(p, "second")
        result = next_inbox_message(p)
        # Both filenames sort lexicographically; first written has earlier ts.
        assert result.name in {first.name, second.name}
        # The function returns the SORTED-FIRST file (lex order, which matches
        # creation order due to timestamp prefixes).
        assert result.name == sorted([first.name, second.name])[0]

    def test_returns_none_when_empty(self, fake_home, tmp_path):
        agent_root = tmp_path / "x"
        agent_root.mkdir()
        p = Participant("X", agent_root)
        ensure_mailboxes(p)
        assert next_inbox_message(p) is None
