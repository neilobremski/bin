from __future__ import annotations

import pytest

from dispatch import dispatch_slash
from rooms import RoomStore, normalize_slug
from h4l import main as h4l_main


@pytest.fixture
def store(tmp_path):
    return RoomStore(tmp_path)


@pytest.fixture
def tells():
    sent: list[tuple[str, str]] = []

    def capture(agent: str, body: str) -> None:
        sent.append((agent, body))

    return sent, capture


class TestSlug:
    def test_normalizes_case(self):
        assert normalize_slug("War") == "war"

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            normalize_slug("")

    def test_rejects_bad_chars(self):
        with pytest.raises(ValueError):
            normalize_slug("war room")


class TestPost:
    def test_auto_create_join_notify_and_ack(self, store, tells):
        sent, tell_fn = tells
        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/post war hello everyone",
            tell_fn=tell_fn,
        )
        assert rc == 0
        meta = store.load_meta("war")
        assert "ALICE" in store.member_names(meta)
        messages = store.list_messages("war")
        assert len(messages) == 1
        assert messages[0]["content"] == "hello everyone"
        acks = [b for a, b in sent if a == "ALICE"]
        assert any("posted to #war" in b for b in acks)
        assert not any(a == "ALICE" and "hello everyone" in b and "posted in" in b for a, b in sent)

    def test_notifies_other_members(self, store, tells):
        sent, tell_fn = tells
        meta = store.ensure_room("war")
        meta["members"] = ["ALICE", "BOB"]
        store.save_meta("war", meta)
        dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/post war update",
            tell_fn=tell_fn,
        )
        bob_msgs = [b for a, b in sent if a == "BOB"]
        assert len(bob_msgs) == 1
        assert "ALICE posted in #war" in bob_msgs[0]
        assert "update" in bob_msgs[0]
        assert "tell HALL /view war" in bob_msgs[0]


class TestInvite:
    def test_invite_adds_and_notifies(self, store, tells):
        sent, tell_fn = tells
        store.ensure_room("war")
        dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/invite war BOB CAROL",
            tell_fn=tell_fn,
        )
        meta = store.load_meta("war")
        members = {m.upper() for m in store.member_names(meta)}
        assert members == {"BOB", "CAROL"}
        assert any(a == "BOB" and "invited" in b for a, b in sent)
        system = [m for m in store.list_messages("war") if m["kind"] == "system"]
        assert system
        assert "BOB" in system[0]["content"] and "CAROL" in system[0]["content"]


class TestList:
    def test_lists_all_rooms_and_members(self, store, tells):
        sent, tell_fn = tells
        m1 = store.ensure_room("war")
        m1["members"] = ["ALICE", "BOB"]
        store.save_meta("war", m1)
        m2 = store.ensure_room("peace")
        m2["members"] = ["CAROL"]
        store.save_meta("peace", m2)
        dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/list",
            tell_fn=tell_fn,
        )
        ack = [b for a, b in sent if a == "ALICE"][-1]
        assert "#war: ALICE, BOB" in ack
        assert "#peace: CAROL" in ack


class TestErrors:
    def test_missing_slash_tells_error(self, store, tells):
        sent, tell_fn = tells
        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="post war hi",
            tell_fn=tell_fn,
        )
        assert rc == 1
        assert sent == [("ALICE", "h4l error: commands must start with / (e.g. /post war hello)")]


class TestSimulateTell:
    def test_simulate_prints_tell_to_stderr(self, tmp_path, capsys):
        rc = h4l_main([
            "dispatch",
            "--root",
            str(tmp_path),
            "--from",
            "ALICE",
            "--node",
            "HALL",
            "--simulate-tell",
            "--message",
            "/post war hello",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        assert "posted to #war" in captured.out
        assert "h4l> tell ALICE:" in captured.err
        assert "posted to #war" in captured.err

    def test_simulate_env_enables_mode(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("H4L_SIMULATE_TELL", "1")
        rc = h4l_main([
            "dispatch",
            "--root",
            str(tmp_path),
            "--from",
            "ALICE",
            "--node",
            "HALL",
            "--message",
            "/list",
        ])
        assert rc == 0
        assert "h4l> tell ALICE:" in capsys.readouterr().err


class TestClear:
    def test_clear_older_than(self, store, tmp_path):
        store.ensure_room("old")
        store.ensure_room("new")
        import json
        from datetime import datetime, timezone, timedelta

        old_meta = store.load_meta("old")
        old_meta["last_activity"] = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat().replace("+00:00", "Z")
        store.save_meta("old", old_meta)
        removed = store.clear_older_than(3600)
        assert "old" in removed
        assert "new" not in removed

    def test_cli_clear_all(self, store, tmp_path, capsys):
        store.ensure_room("a")
        store.ensure_room("b")
        rc = h4l_main(["clear", "--root", str(tmp_path), "--all"])
        assert rc == 0
        assert "cleared 2 room(s)" in capsys.readouterr().out
        assert store.list_rooms() == []
