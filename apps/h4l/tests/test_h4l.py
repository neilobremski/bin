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
    sent: list[tuple[str, str, list]] = []

    def capture(agent: str, body: str, attachments=None) -> None:
        sent.append((agent, body, list(attachments or [])))

    return sent, capture


class TestSlug:
    def test_normalizes_case(self):
        assert normalize_slug("War") == "war"

    def test_strips_hash_prefix(self):
        assert normalize_slug("#war") == "war"
        assert normalize_slug("  #War  ") == "war"

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
        alice_msgs = [b for a, b, _ in sent if a == "ALICE"]
        assert not alice_msgs
        assert not any(
            a == "ALICE" and "hello everyone" in b and "posted in" in b
            for a, b, _ in sent
        )

    def test_hash_prefix_room_same_as_plain(self, store, tells):
        sent, tell_fn = tells
        assert (
            dispatch_slash(
                store,
                sender="ALICE",
                node="HALL",
                message="/post war first",
                tell_fn=tell_fn,
            )
            == 0
        )
        assert (
            dispatch_slash(
                store,
                sender="BOB",
                node="HALL",
                message="/post #war second",
                tell_fn=tell_fn,
            )
            == 0
        )
        messages = store.list_messages("war")
        assert len(messages) == 2
        assert messages[0]["content"] == "first"
        assert messages[1]["content"] == "second"

    def test_irc_style_hash_post(self, store, tells):
        sent, tell_fn = tells
        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="CHATROOM",
            message="#everyone hello",
            tell_fn=tell_fn,
        )
        assert rc == 0
        messages = store.list_messages("everyone")
        assert len(messages) == 1
        assert messages[0]["content"] == "hello"
        assert not [b for a, b, _ in sent if a == "ALICE"]

    def test_irc_style_matches_slash_post(self, store, tells):
        sent, tell_fn = tells
        dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="#war irc hello",
            tell_fn=tell_fn,
        )
        dispatch_slash(
            store,
            sender="BOB",
            node="HALL",
            message="/post war slash hello",
            tell_fn=tell_fn,
        )
        messages = store.list_messages("war")
        assert [m["content"] for m in messages] == ["irc hello", "slash hello"]

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
        bob_msgs = [b for a, b, _ in sent if a == "BOB"]
        assert len(bob_msgs) == 1
        assert "ALICE posted in #war" in bob_msgs[0]
        assert "update" in bob_msgs[0]
        assert "Post a message: tell HALL #war" in bob_msgs[0]
        assert "More commands: tell HALL /help" in bob_msgs[0]
        assert "tell HALL /view" not in bob_msgs[0]

    def test_onboard_footer_only_once(self, store, tells):
        sent, tell_fn = tells
        meta = store.ensure_room("war")
        meta["members"] = ["ALICE", "BOB"]
        store.save_meta("war", meta)
        for msg in ("/post war one", "/post war two"):
            dispatch_slash(
                store,
                sender="ALICE",
                node="HALL",
                message=msg,
                tell_fn=tell_fn,
            )
        bob_msgs = [b for a, b, _ in sent if a == "BOB"]
        assert len(bob_msgs) == 2
        assert "Post a message:" in bob_msgs[0]
        assert "Post a message:" not in bob_msgs[1]
        meta = store.load_meta("war")
        assert store.has_seen_help(meta, "BOB")

    def test_at_mention_invites_and_posts_once(self, store, tells):
        sent, tell_fn = tells
        dispatch_slash(
            store,
            sender="ALICE",
            node="CHATROOM",
            message="#everyone @pat I'm testing out chat rooms.",
            tell_fn=tell_fn,
        )
        meta = store.load_meta("everyone")
        assert "pat" in {m.lower() for m in store.member_names(meta)}
        messages = store.list_messages("everyone")
        assert len(messages) == 1
        assert messages[0]["content"] == "@pat I'm testing out chat rooms."
        pat_msgs = [b for a, b, _ in sent if a.lower() == "pat"]
        assert len(pat_msgs) == 1
        assert "@pat I'm testing out chat rooms." in pat_msgs[0]
        assert "posted in #everyone" in pat_msgs[0]
        assert "invited" not in pat_msgs[0]

    def test_multiple_at_mentions(self, store, tells):
        sent, tell_fn = tells
        dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="#war @bob @carol hello team",
            tell_fn=tell_fn,
        )
        meta = store.load_meta("war")
        members = {m.upper() for m in store.member_names(meta)}
        assert members >= {"ALICE", "BOB", "CAROL"}
        assert store.list_messages("war")[0]["content"] == "@bob @carol hello team"
        assert len([b for a, b, _ in sent if a.upper() == "BOB"]) == 1
        assert len([b for a, b, _ in sent if a.upper() == "CAROL"]) == 1

    def test_at_mention_only_at_message_start(self, store, tells):
        sent, tell_fn = tells
        dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="#war ping @bob later",
            tell_fn=tell_fn,
        )
        meta = store.load_meta("war")
        assert "BOB" not in {m.upper() for m in store.member_names(meta)}
        assert store.list_messages("war")[0]["content"] == "ping @bob later"
        assert not [b for a, b, _ in sent if a == "BOB"]


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
        bob_invite = [b for a, b, _ in sent if a == "BOB"][0]
        assert "invited" in bob_invite
        assert "Post a message: tell HALL #war" in bob_invite
        assert any(a == "BOB" and "invited" in b for a, b, _ in sent)
        system = [m for m in store.list_messages("war") if m["kind"] == "system"]
        assert system
        assert "BOB" in system[0]["content"] and "CAROL" in system[0]["content"]


class TestRemove:
    def test_remove_drops_member(self, store, tells):
        sent, tell_fn = tells
        meta = store.ensure_room("war")
        meta["members"] = ["ALICE", "BOB", "CAROL"]
        store.save_meta("war", meta)
        dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/remove war BOB",
            tell_fn=tell_fn,
        )
        meta = store.load_meta("war")
        members = {m.upper() for m in store.member_names(meta)}
        assert members == {"ALICE", "CAROL"}
        bob_msgs = [b for a, b, _ in sent if a.upper() == "BOB"]
        assert len(bob_msgs) == 1
        assert "removed you from #war" in bob_msgs[0]
        system = [m for m in store.list_messages("war") if m["kind"] == "system"]
        assert system
        assert "removed BOB" in system[0]["content"]

    def test_remove_requires_membership(self, store, tells):
        sent, tell_fn = tells
        meta = store.ensure_room("war")
        meta["members"] = ["BOB"]
        store.save_meta("war", meta)
        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/remove war BOB",
            tell_fn=tell_fn,
        )
        assert rc == 1
        assert "not a member" in sent[0][1]

    def test_remove_self_hints_leave(self, store, tells):
        sent, tell_fn = tells
        meta = store.ensure_room("war")
        meta["members"] = ["ALICE", "BOB"]
        store.save_meta("war", meta)
        dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/remove war ALICE",
            tell_fn=tell_fn,
        )
        meta = store.load_meta("war")
        assert "ALICE" in store.member_names(meta)
        ack = [b for a, b, _ in sent if a == "ALICE"][-1]
        assert "use /leave" in ack

    def test_kick_alias(self, store, tells):
        sent, tell_fn = tells
        meta = store.ensure_room("war")
        meta["members"] = ["ALICE", "BOB"]
        store.save_meta("war", meta)
        dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/kick war BOB",
            tell_fn=tell_fn,
        )
        meta = store.load_meta("war")
        assert "BOB" not in {m.upper() for m in store.member_names(meta)}


class TestView:
    def test_view_convo_markdown(self, store, tells):
        sent, tell_fn = tells
        store.ensure_room("war")
        store.append_message("war", sender="ALICE", content="hello")
        store.append_message("war", sender="BOB", content="hi back")
        dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/view war",
            tell_fn=tell_fn,
        )
        ack = [b for a, b, _ in sent if a == "ALICE"][-1]
        assert "## from ALICE to #war at" in ack
        assert "hello" in ack
        assert "### from BOB to #war at" in ack
        assert "hi back" in ack
        assert "viewed messages 1–2 of 2" in ack


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
        ack = [b for a, b, _ in sent if a == "ALICE"][-1]
        assert "#war: ALICE, BOB" in ack
        assert "#peace: CAROL" in ack


class TestErrors:
    def test_missing_slash_tells_error(self, store, tells):
        sent, tell_fn = tells
        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="CHATROOM",
            message="How do I use you?",
            tell_fn=tell_fn,
        )
        assert rc == 1
        assert len(sent) == 1
        agent, body, atts = sent[0]
        assert agent == "ALICE"
        assert atts == []
        assert body.startswith("Error: send #<room> <message> or a /command")
        assert "h4l" not in body.lower()
        assert 'tell CHATROOM "#<room> <message>"' in body
        assert 'tell CHATROOM "/list"' in body

    def test_help_outputs_usage(self, store, tells):
        sent, tell_fn = tells
        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="CHATROOM",
            message="/help",
            tell_fn=tell_fn,
        )
        assert rc == 0
        ack = [b for a, b, _ in sent if a == "ALICE"][-1]
        assert "Post (IRC style):" in ack
        assert 'tell CHATROOM "#<room> <message>"' in ack
        assert 'tell CHATROOM "/help"' in ack
        assert "Error:" not in ack

    def test_unknown_command_includes_usage(self, store, tells):
        sent, tell_fn = tells
        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/frobnicate",
            tell_fn=tell_fn,
        )
        assert rc == 1
        body = sent[0][1]
        assert "Error: unknown command /frobnicate" in body
        assert 'tell HALL "/join <room>"' in body

    def test_part_alias_for_leave(self, store, tells):
        sent, tell_fn = tells
        meta = store.ensure_room("war")
        meta, _ = store.add_member(meta, "ALICE")
        store.save_meta("war", meta)
        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message="/part war",
            tell_fn=tell_fn,
        )
        assert rc == 0
        assert any("left #war" in b for a, b, _ in sent if a == "ALICE")


class TestAttachmentProxy:
    def test_strips_attached_file_lines_from_stored_content(self, store, tells, tmp_path):
        sent, tell_fn = tells
        payload = tmp_path / "report.pdf"
        payload.write_bytes(b"%PDF")
        meta = store.ensure_room("war")
        meta, _ = store.add_member(meta, "ALICE")
        meta, _ = store.add_member(meta, "BOB")
        store.save_meta("war", meta)

        message = f"#war see this\nATTACHED FILE: {payload.resolve()}"
        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message=message,
            tell_fn=tell_fn,
        )
        assert rc == 0
        messages = store.list_messages("war")
        assert len(messages) == 1
        assert messages[0]["content"] == "see this"
        assert messages[0]["files"] == [{"filename": "report.pdf"}]
        assert "ATTACHED FILE:" not in messages[0]["content"]

    def test_fanout_reattaches_to_other_members(self, store, tells, tmp_path):
        sent, tell_fn = tells
        payload = tmp_path / "notes.txt"
        payload.write_text("secret", encoding="utf-8")
        meta = store.ensure_room("war")
        meta, _ = store.add_member(meta, "ALICE")
        meta, _ = store.add_member(meta, "BOB")
        meta, _ = store.add_member(meta, "CAROL")
        store.save_meta("war", meta)

        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message=f"/post war shared\nATTACHED FILE: {payload.resolve()}",
            tell_fn=tell_fn,
        )
        assert rc == 0
        bob = [(a, b, atts) for a, b, atts in sent if a == "BOB"]
        carol = [(a, b, atts) for a, b, atts in sent if a == "CAROL"]
        assert len(bob) == 1
        assert len(carol) == 1
        assert bob[0][2] == [payload.resolve()]
        assert carol[0][2] == [payload.resolve()]
        assert "shared" in bob[0][1]
        assert not any(a == "ALICE" for a, _, _ in sent)

    def test_multiple_attachments(self, store, tells, tmp_path):
        sent, tell_fn = tells
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        meta = store.ensure_room("war")
        meta, _ = store.add_member(meta, "ALICE")
        meta, _ = store.add_member(meta, "BOB")
        store.save_meta("war", meta)

        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message=(
                f"#war two files\n"
                f"ATTACHED FILE: {a.resolve()}\n"
                f"ATTACHED FILE: {b.resolve()}"
            ),
            tell_fn=tell_fn,
        )
        assert rc == 0
        bob_atts = [atts for agent, _, atts in sent if agent == "BOB"][0]
        assert bob_atts == [a.resolve(), b.resolve()]
        assert store.list_messages("war")[0]["files"] == [
            {"filename": "a.bin"},
            {"filename": "b.bin"},
        ]

    def test_attachment_only_post_allowed(self, store, tells, tmp_path):
        sent, tell_fn = tells
        payload = tmp_path / "solo.png"
        payload.write_bytes(b"PNG")
        meta = store.ensure_room("war")
        meta, _ = store.add_member(meta, "ALICE")
        meta, _ = store.add_member(meta, "BOB")
        store.save_meta("war", meta)

        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message=f"#war\nATTACHED FILE: {payload.resolve()}",
            tell_fn=tell_fn,
        )
        assert rc == 0
        msg = store.list_messages("war")[0]
        assert msg["content"] == ""
        assert msg["files"] == [{"filename": "solo.png"}]
        bob_atts = [atts for agent, _, atts in sent if agent == "BOB"][0]
        assert bob_atts == [payload.resolve()]

    def test_missing_attachment_skipped(self, store, tells, tmp_path, capsys):
        sent, tell_fn = tells
        missing = tmp_path / "gone.txt"
        meta = store.ensure_room("war")
        meta, _ = store.add_member(meta, "ALICE")
        meta, _ = store.add_member(meta, "BOB")
        store.save_meta("war", meta)

        rc = dispatch_slash(
            store,
            sender="ALICE",
            node="HALL",
            message=f"#war still here\nATTACHED FILE: {missing.resolve()}",
            tell_fn=tell_fn,
        )
        assert rc == 0
        msg = store.list_messages("war")[0]
        assert msg["content"] == "still here"
        assert "files" not in msg
        bob = [(b, atts) for a, b, atts in sent if a == "BOB"]
        assert len(bob) == 1
        assert bob[0][1] == []
        assert "skipping missing attachment" in capsys.readouterr().err

    def test_default_tell_passes_attach_flags(self, tmp_path, monkeypatch):
        from notify import default_tell

        calls: list[list[str]] = []

        def fake_run(cmd, check=False):
            calls.append(list(cmd))

            class R:
                returncode = 0

            return R()

        monkeypatch.setattr("notify.subprocess.run", fake_run)
        payload = tmp_path / "x.txt"
        payload.write_text("x", encoding="utf-8")
        default_tell("BOB", "hello", [payload])
        assert calls == [["tell", "--attach", str(payload), "BOB", "hello"]]

    def test_simulate_shows_attach(self, tmp_path, capsys):
        from notify import simulate_tell

        payload = tmp_path / "y.txt"
        payload.write_text("y", encoding="utf-8")
        simulate_tell("BOB", "hi\nthere", [payload])
        err = capsys.readouterr().err
        assert f"h4l> tell --attach {payload} BOB:" in err
        assert "h4l>   hi" in err


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
        assert captured.out == ""
        assert "h4l> tell ALICE:" not in captured.err

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
