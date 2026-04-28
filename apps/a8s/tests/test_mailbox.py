"""Tests for mailbox.py — routing fan-out, queue helpers, content/file split."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import (
    BACKOFF_SCHEDULE,
    MAX_ATTEMPTS,
    MAX_FILE_BYTES,
    Participant,
    files_dir,
    inbox_dir,
    inbox_tmp_dir,
    outbox_dir,
    pending_dir,
    retry_sidecar_path,
    trash_dir,
)
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

    def test_filename_is_ulid_and_matches_id(self, fake_home, tmp_path):
        from ulid import is_ulid
        path = _write_outbox("A", tmp_path, "B", "hi", [])
        # Filename = "<ulid>.json" — sortable, opaque, no sender leak in name.
        stem = path.stem
        assert is_ulid(stem)
        # The message's `id` field equals the filename stem so receivers can
        # dedupe by ID without re-parsing the filename.
        msg = json.loads(path.read_text())
        assert msg["id"] == stem


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
        # The CLEAR sentinel is identified by its body (`clear: true`), not
        # by the filename — filenames are now opaque ULIDs.
        body = json.loads(files[0].read_text())
        assert body.get("clear") is True
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
        # Strict opacity (#69, #70): no `alias` / `others_count` fields, and
        # `to` preserves the alias name (mailing-list semantics).
        assert "alias" not in b_msg
        assert "others_count" not in b_msg
        assert b_msg["to"] == "devs"
        assert b_msg["from"] == "A"

    def test_alias_fanout_preserves_to_for_all_recipients(self, three_agents):
        # Both fanout recipients see `to: devs`; the message shape is
        # identical for them (no individual "you got this" leak).
        a, b, c = three_agents
        save_aliases({"devs": ["B", "C"]})
        _write_outbox("A", a.root, "devs", "msg", [])
        route_outboxes([a, b, c], all_agents=[a, b, c])
        for n in ("B", "C"):
            m = json.loads(next(inbox_dir(n).iterdir()).read_text())
            assert m["to"] == "devs"
            assert "alias" not in m
            assert "others_count" not in m

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

    def test_unknown_recipient_with_no_remotes_is_trashed(self, two_agents):
        # With the two-phase ingest + process design, an unknown recipient
        # has no path forward when no remotes are configured: local has no
        # match, and there's nothing to publish to. Trash immediately so the
        # outbox dir doesn't accumulate undeliverable messages.
        a, b = two_agents
        outbox = outbox_dir(a.root)
        bad = outbox / "20260101T000000_A.json"
        bad.write_text(json.dumps({
            "from": "A", "to": "BOGUS", "content": "x", "files": [],
        }))
        n = route_outboxes([a, b], all_agents=[a, b])
        assert n == 0
        # Original outbox file is gone (ingest moved it out).
        assert not bad.is_file()
        # The message landed in A's trash — terminal failure.
        trashed = list(trash_dir("A").iterdir())
        assert any("BOGUS" in f.read_text() for f in trashed)

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


class TestAtomicFanout:
    """Issue #67 — `route_outboxes` stages routed copies under each recipient's
    `inbox.tmp/<source-name>` and only renames them into `inbox/` after every
    recipient has staged. A crash mid-fan-out should not produce duplicates
    on retry: recipients whose final `inbox/<source-name>` already exists are
    skipped."""

    def test_uses_source_filename_in_inbox(self, three_agents):
        a, b, c = three_agents
        out_path = _write_outbox("A", a.root, "devs", "team msg", [])
        save_aliases({"devs": ["A", "B", "C"]})
        route_outboxes([a, b, c], all_agents=[a, b, c])
        # Recipients receive a file named exactly like the source outbox file.
        b_files = list(inbox_dir("B").iterdir())
        assert len(b_files) == 1
        assert b_files[0].name == out_path.name
        c_files = list(inbox_dir("C").iterdir())
        assert c_files[0].name == out_path.name

    def test_inbox_tmp_is_empty_after_clean_run(self, three_agents):
        a, b, c = three_agents
        save_aliases({"devs": ["A", "B", "C"]})
        _write_outbox("A", a.root, "devs", "team msg", [])
        route_outboxes([a, b, c], all_agents=[a, b, c])
        for p in (a, b, c):
            assert list(inbox_tmp_dir(p.name).iterdir()) == []

    def test_retry_skips_already_delivered_recipient(self, three_agents):
        # Simulate "process died after delivering to B but before unlinking
        # A's outbox." Pre-populate B's inbox with the source filename and
        # leave A's outbox file in place. Re-routing should NOT re-deliver
        # to B, only fill in C; the outbox file is then unlinked.
        a, b, c = three_agents
        save_aliases({"devs": ["A", "B", "C"]})
        out_path = _write_outbox("A", a.root, "devs", "team msg", [])
        # Pre-populate B's inbox with a copy of the message under the same
        # filename — represents a successful prior staging that promoted to
        # inbox/ before the process died.
        with out_path.open("r", encoding="utf-8") as f:
            base_msg = json.load(f)
        base_msg["from"] = "A"  # routing force-overwrites this anyway
        # `to` stays at "devs" — strict opacity preserves the original target.
        b_pre = inbox_dir("B") / out_path.name
        with b_pre.open("w", encoding="utf-8") as f:
            json.dump(base_msg, f)

        route_outboxes([a, b, c], all_agents=[a, b, c])

        # B still has exactly one copy (no duplicate via .1 suffix).
        b_files = list(inbox_dir("B").iterdir())
        assert len(b_files) == 1
        assert b_files[0].name == out_path.name
        # C now has the message.
        c_files = list(inbox_dir("C").iterdir())
        assert len(c_files) == 1
        # Source outbox unlinked after the routing pass committed.
        assert not out_path.is_file()


class TestFileTransfer:
    """Issue #62 — `FILE:` payloads are copied into each recipient's `.files/`
    at routing time. The routed message's `files[i].path` is rewritten to the
    recipient-local copy so the recipient's wake prompt emits a path it can
    actually open under its own sandbox."""

    @pytest.fixture
    def file_agents(self, fake_home, tmp_path):
        a_root = tmp_path / "a"; a_root.mkdir()
        b_root = tmp_path / "b"; b_root.mkdir()
        save_registry({"A": {"root": str(a_root)}, "B": {"root": str(b_root)}})
        a = Participant("A", a_root)
        b = Participant("B", b_root)
        ensure_mailboxes(a)
        ensure_mailboxes(b)
        return a, b

    def test_copies_file_to_recipient_files_dir(self, file_agents):
        a, b = file_agents
        # Sender prepares a payload under its OWN root.
        payload = a.root / "report.txt"
        payload.write_text("hello payload")
        _write_outbox("A", a.root, "B", "see attached", [
            {"filename": "report.txt", "path": str(payload)},
        ])
        route_outboxes([a, b], all_agents=[a, b])
        # File arrived in B's .files/ and content matches.
        b_files = list(files_dir(b.root).iterdir())
        assert len(b_files) == 1
        assert b_files[0].name == "report.txt"
        assert b_files[0].read_text() == "hello payload"
        # Routed message's path points at B's local copy.
        delivered = json.loads(next(inbox_dir("B").iterdir()).read_text())
        assert len(delivered["files"]) == 1
        assert delivered["files"][0]["path"] == str(b_files[0])
        assert delivered["files"][0]["filename"] == "report.txt"

    def test_alias_fanout_copies_to_each_recipient(self, fake_home, tmp_path):
        # A sends to alias devs=[B, C] with a FILE; each recipient gets its
        # own copy under its own .files/.
        agents = {}
        for n in ("A", "B", "C"):
            d = tmp_path / n; d.mkdir()
            agents[n] = Participant(n, d)
        save_registry({n: {"root": str(p.root)} for n, p in agents.items()})
        save_aliases({"devs": ["B", "C"]})
        for p in agents.values():
            ensure_mailboxes(p)
        a = agents["A"]
        payload = a.root / "data.csv"
        payload.write_text("col1,col2\n1,2\n")
        _write_outbox("A", a.root, "devs", "team data", [
            {"filename": "data.csv", "path": str(payload)},
        ])
        route_outboxes(list(agents.values()), all_agents=list(agents.values()))
        for n in ("B", "C"):
            recipient_files = list(files_dir(agents[n].root).iterdir())
            assert len(recipient_files) == 1
            assert recipient_files[0].read_text() == "col1,col2\n1,2\n"

    def test_path_outside_sender_root_is_rejected(self, fake_home, tmp_path, file_agents):
        a, b = file_agents
        # Attacker writes a FILE: pointing OUTSIDE A's root (e.g., system
        # secrets). Routing must drop the file rather than copy it.
        outside = tmp_path / "secrets.txt"
        outside.write_text("PASSWORD=hunter2")
        _write_outbox("A", a.root, "B", "leaking", [
            {"filename": "secrets.txt", "path": str(outside)},
        ])
        route_outboxes([a, b], all_agents=[a, b])
        # File NOT copied into B's .files/.
        assert list(files_dir(b.root).iterdir()) == []
        # Message still delivered (with the file dropped).
        delivered = json.loads(next(inbox_dir("B").iterdir()).read_text())
        assert delivered["files"] == []
        assert delivered["content"] == "leaking"

    def test_missing_source_is_dropped(self, file_agents):
        a, b = file_agents
        # FILE: points at a path that doesn't exist (sender promised something
        # they didn't write). Routing drops the file silently into the log.
        _write_outbox("A", a.root, "B", "ghost", [
            {"filename": "ghost.txt", "path": str(a.root / "ghost.txt")},
        ])
        route_outboxes([a, b], all_agents=[a, b])
        assert list(files_dir(b.root).iterdir()) == []
        delivered = json.loads(next(inbox_dir("B").iterdir()).read_text())
        assert delivered["files"] == []

    def test_oversized_source_is_dropped(self, file_agents):
        a, b = file_agents
        big = a.root / "big.bin"
        # 1 byte over the cap.
        big.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        _write_outbox("A", a.root, "B", "huge", [
            {"filename": "big.bin", "path": str(big)},
        ])
        route_outboxes([a, b], all_agents=[a, b])
        assert list(files_dir(b.root).iterdir()) == []

    def test_collision_uniquifies(self, file_agents):
        a, b = file_agents
        # Two messages, each carrying a FILE with the same basename.
        for i in range(2):
            payload = a.root / f"p{i}.txt"
            payload.write_text(f"contents {i}")
            # Force same destination filename via shared "doc.txt" name.
            entry_path = a.root / "doc.txt"
            entry_path.write_text(f"v{i}")
            _write_outbox("A", a.root, "B", f"msg {i}", [
                {"filename": "doc.txt", "path": str(entry_path)},
            ])
            route_outboxes([a, b], all_agents=[a, b])
        # Two distinct copies in B's .files/ thanks to unique_path uniquify.
        names = {p.name for p in files_dir(b.root).iterdir()}
        assert names == {"doc.txt", "doc.1.txt"}


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


class TestIngestPhase:
    """Phase 1 of `route_outboxes` (issue #63): a8s never reads a file in
    `<root>/.outbox/`; on every pass it atomically moves new outbox files
    out to `~/.a8s/agents/<sender>/pending/` before any further processing.
    Retry sidecars and trash all live under ~/.a8s/."""

    def test_outbox_emptied_after_pass(self, two_agents):
        a, b = two_agents
        out_path = _write_outbox("A", a.root, "B", "hi", [])
        # Pre-pass: file is in A's outbox.
        assert out_path.is_file()
        route_outboxes([a, b], all_agents=[a, b])
        # Post-pass: outbox dir is empty for both senders.
        assert list(outbox_dir(a.root).iterdir()) == []
        assert list(outbox_dir(b.root).iterdir()) == []

    def test_pending_dir_holds_messages_during_routing(self, fake_home, tmp_path):
        # Solo sender with no recipients in the registry — ingest still happens
        # but processing trashes the message (no path). Verifying the ingest
        # rename in isolation requires watching the filesystem, but we can
        # observe via the trash that the file flowed through pending/.
        a_root = tmp_path / "solo"; a_root.mkdir()
        save_registry({"SOLO": {"root": str(a_root)}})
        a = Participant("SOLO", a_root)
        ensure_mailboxes(a)
        _write_outbox("SOLO", a.root, "GHOST", "lost", [])
        route_outboxes([a], all_agents=[a])
        # Outbox empty.
        assert list(outbox_dir(a.root).iterdir()) == []
        # Pending also empty (no path forward → trashed in phase 2).
        assert list(pending_dir("SOLO").iterdir()) == []
        # Trashed.
        assert any("lost" in f.read_text() for f in trash_dir("SOLO").iterdir())


class TestRetrySidecar:
    """Per-message retry sidecar. With no remotes configured, the happy path
    never creates a sidecar — local delivery succeeds in one pass. The
    sidecar machinery only kicks in when something can't be delivered."""

    def test_no_sidecar_left_after_happy_path(self, two_agents):
        a, b = two_agents
        _write_outbox("A", a.root, "B", "hi", [])
        route_outboxes([a, b], all_agents=[a, b])
        # No sidecars left over for either sender.
        for s in (a, b):
            for f in pending_dir(s.name).iterdir():
                assert not f.name.endswith(".retry")
        # Pending dirs empty for both senders.
        assert list(pending_dir(a.name).iterdir()) == []

    def test_unknown_recipient_with_no_remotes_trashes_immediately(self, two_agents):
        # Defensive: with no remotes there's no point retrying — terminal
        # failure happens on the first pass. No sidecar is left behind.
        a, b = two_agents
        _write_outbox("A", a.root, "BOGUS", "lost", [])
        route_outboxes([a, b], all_agents=[a, b])
        assert list(pending_dir("A").iterdir()) == []
        trashed = list(trash_dir("A").iterdir())
        assert any("lost" in f.read_text() for f in trashed)


class TestRemotePublishHook:
    """Chunk 4 leaves the publish_remotes hook unwired. Stub it here to
    confirm the contract: when at least one configured remote hasn't yet
    accepted, the sidecar persists with bumped attempts; once every remote
    is in `succeeded_remotes`, the message finalizes (unlinks)."""

    def test_remote_failure_creates_sidecar_with_attempts(self, two_agents):
        a, b = two_agents

        def stub_publish(msg, sender_name, succeeded_so_far, attempt_count):
            # Always fail — return the input unchanged (no remote IDs added).
            return list(succeeded_so_far)

        _write_outbox("A", a.root, "B", "hi", [])
        route_outboxes(
            [a, b],
            all_agents=[a, b],
            publish_remotes=stub_publish,
            configured_remote_ids=["hub"],
        )
        # Local delivery succeeded — B has the message.
        assert len(list(inbox_dir("B").iterdir())) == 1
        # But the sidecar persists in A's pending/, with attempts=1 and a
        # next_attempt scheduled per BACKOFF_SCHEDULE[0].
        pending_files = [f for f in pending_dir("A").iterdir()
                         if f.name.endswith(".json") and not f.name.endswith(".retry")]
        assert len(pending_files) == 1
        sidecar_path = retry_sidecar_path(pending_files[0])
        assert sidecar_path.is_file()
        side = json.loads(sidecar_path.read_text())
        assert side["attempts"] == 1
        assert side["local_delivered"] is True
        assert side["succeeded_remotes"] == []
        assert side["next_attempt"]  # ISO timestamp set

    def test_remote_success_finalizes(self, two_agents):
        a, b = two_agents

        def stub_publish(msg, sender_name, succeeded_so_far, attempt_count):
            # Mark hub as succeeded.
            return list(succeeded_so_far) + ["hub"]

        _write_outbox("A", a.root, "B", "hi", [])
        route_outboxes(
            [a, b],
            all_agents=[a, b],
            publish_remotes=stub_publish,
            configured_remote_ids=["hub"],
        )
        # Local delivery + remote publish both succeeded → no sidecar, no
        # pending file.
        remaining = list(pending_dir("A").iterdir())
        assert remaining == []

    def test_remote_only_delivery_unknown_local(self, two_agents):
        # `to: GHOST` is unknown locally but remotes are configured. The
        # message should publish and finalize even without a local match.
        a, b = two_agents

        published = []

        def stub_publish(msg, sender_name, succeeded_so_far, attempt_count):
            published.append(msg.get("to"))
            return list(succeeded_so_far) + ["hub"]

        _write_outbox("A", a.root, "GHOST", "remote-only", [])
        route_outboxes(
            [a, b],
            all_agents=[a, b],
            publish_remotes=stub_publish,
            configured_remote_ids=["hub"],
        )
        # The publish hook saw the envelope.
        assert published == ["GHOST"]
        # Pending is clean — remote-only delivery counts as success.
        assert list(pending_dir("A").iterdir()) == []
        # Nothing in trash either.
        assert list(trash_dir("A").iterdir()) == []

    def test_backoff_exhaustion_trashes(self, two_agents):
        a, b = two_agents

        def always_fails(msg, sender_name, succeeded_so_far, attempt_count):
            return list(succeeded_so_far)

        _write_outbox("A", a.root, "B", "stubborn", [])
        # Run MAX_ATTEMPTS + 1 passes, manually overriding the sidecar's
        # next_attempt each time so the backoff gate doesn't skip us.
        for _ in range(MAX_ATTEMPTS + 1):
            route_outboxes(
                [a, b],
                all_agents=[a, b],
                publish_remotes=always_fails,
                configured_remote_ids=["hub"],
            )
            # Force the sidecar to allow another pass right away.
            for f in pending_dir("A").iterdir():
                if f.name.endswith(".retry"):
                    side = json.loads(f.read_text())
                    side["next_attempt"] = ""
                    f.write_text(json.dumps(side))
        # After exhaustion the message is in trash, sidecar is gone.
        assert list(pending_dir("A").iterdir()) == []
        trashed = list(trash_dir("A").iterdir())
        assert any("stubborn" in f.read_text() for f in trashed)

    def test_file_payloads_skip_remote_publish(self, two_agents):
        # v1 limitation: messages with FILE: payloads do NOT cross the mesh.
        # The publish hook must not be called; the sidecar should treat all
        # configured remotes as already-succeeded so the message finalizes
        # on local delivery alone.
        a, b = two_agents
        payload = a.root / "doc.txt"
        payload.write_text("payload")
        called = []

        def stub_publish(msg, sender_name, succeeded_so_far, attempt_count):
            called.append(msg)
            return list(succeeded_so_far) + ["hub"]

        _write_outbox("A", a.root, "B", "see attached", [
            {"filename": "doc.txt", "path": str(payload)},
        ])
        route_outboxes(
            [a, b],
            all_agents=[a, b],
            publish_remotes=stub_publish,
            configured_remote_ids=["hub"],
        )
        assert called == []  # publish hook not invoked
        assert list(pending_dir("A").iterdir()) == []  # finalized
        assert len(list(inbox_dir("B").iterdir())) == 1  # local delivery happened
