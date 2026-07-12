"""r4t chat + seat tests — mailbox helpers, rendering, session commands."""
from __future__ import annotations

import json

import pytest

import state
import tasks as taskmod
from chat import (
    ChatSession,
    filter_log_line,
    handle_command,
    render_envelope,
    run_chat,
    sender_label,
)
from roster import load_roster

NODE = "acme"


@pytest.fixture
def roster(repo):
    return load_roster(repo / "ROSTER.md")


@pytest.fixture
def human(roster):
    return next(m for m in roster.members if m.is_human)


@pytest.fixture
def session(ctx, roster, human, r4t_home):
    return ChatSession(ctx, roster, human)


def drain_events(session):
    events = []
    while not session.events.empty():
        events.append(session.events.get())
    return events


def test_seat_mailbox_roundtrip(r4t_home):
    state.park_seat_message(NODE, "Neil", "acme:gerry", "hello")
    unread = state.list_seat_messages(NODE, "neil")
    assert len(unread) == 1
    moved = state.mark_seat_read(NODE, "neil", unread[0])
    assert moved.is_file()
    assert state.list_seat_messages(NODE, "neil") == []
    assert len(state.list_seat_messages(NODE, "neil", read=True)) == 1


def test_seat_presence(r4t_home):
    assert not state.seat_attached(NODE, "neil")
    state.touch_seat_presence(NODE, "neil")
    assert state.seat_attached(NODE, "neil")
    state.clear_seat_presence(NODE, "neil")
    assert not state.seat_attached(NODE, "neil")


def test_render_envelope_strips_header_and_indents():
    text = render_envelope(
        {
            "from": "acme:gerry",
            "content": "[r4t task=01KX0000000000000000000000 hop=2 auto] line one\nline two",
        }
    )
    assert text == "acme:gerry: line one\n    line two"


def test_filter_log_line_shapes():
    assert filter_log_line("r4t: PARKED cadence 12s") == "r4t: PARKED cadence 12s"
    assert (
        filter_log_line("## 2026-07-11T15:23:35Z dispatch neil -> Ada (task X hop 0)")
        == "turn: neil -> Ada (task X hop 0)"
    )
    assert filter_log_line("### Output (Ada, exit 0 in 13.7s)") == "done: Ada, exit 0 in 13.7s"
    assert filter_log_line("### Prompt") is None
    assert filter_log_line("You are Ada, a member of the crew team") is None


def test_to_command_targets(session, capsys):
    assert session.target == NODE
    session.handle_line("/to phil")
    assert session.target == "acme:phil"
    session.handle_line("/to nobody")
    assert session.target == "acme:phil"
    session.handle_line("/to neil")
    assert session.target == "acme:phil"
    assert "no AI member" in capsys.readouterr().out
    session.handle_line("/to acme")
    assert session.target == NODE


def test_quit_and_unknown_command(session, capsys):
    assert session.handle_line("/quit") is False
    assert session.handle_line("/bogus") is True
    assert "unknown command: /bogus (try /help)" in capsys.readouterr().out
    assert session.handle_line("") is True


def test_help_lists_commands(session, capsys):
    assert session.handle_line("/help") is True
    out = capsys.readouterr().out
    for cmd in ("/to", "/who", "/threads", "/help", "/quit"):
        assert cmd in out
    assert "/tasks" not in out


def test_threads_lists_only_open_threads(session, capsys, r4t_home):
    taskmod.ensure_task(NODE, "01KX000000000000000000AAAA", "acme:gerry")
    closed = taskmod.ensure_task(NODE, "01KX000000000000000000BBBB", "acme:phil")
    taskmod.close_task(NODE, closed["id"])
    session.handle_line("/threads")
    out = capsys.readouterr().out
    assert "creator=acme:gerry" in out
    assert "0000AAAA" in out  # short id tail (last 8 chars)
    assert "acme:phil" not in out  # closed thread is hidden


def test_threads_empty_message(session, capsys, r4t_home):
    session.handle_line("/threads")
    assert "(no open threads)" in capsys.readouterr().out


def test_sender_label_adds_rig_for_members(roster):
    assert sender_label(roster, "acme:gerry") == "acme:gerry (leader)"
    assert sender_label(roster, "acme:phil") == "acme:phil (junior-dev)"
    # external agents and the human seat carry no rig slug
    assert sender_label(roster, "external:bot") == "external:bot"
    assert sender_label(roster, "acme:neil") == "acme:neil"


def test_render_envelope_carries_rig_slug(roster):
    text = render_envelope({"from": "acme:gerry", "content": "hi"}, roster)
    assert text == "acme:gerry (leader): hi"
    plain = render_envelope({"from": "acme:gerry", "content": "hi"})
    assert plain == "acme:gerry: hi"


def test_handle_command_reports_target_and_quit(roster, human):
    result = handle_command(roster, NODE, human, "/to phil")
    assert result.target == "acme:phil"
    assert result.lines == ["target: acme:phil"]
    assert handle_command(roster, NODE, human, "/quit").quit is True
    miss = handle_command(roster, NODE, human, "/to nobody")
    assert miss.target is None and "no AI member" in miss.lines[0]


def test_plain_line_queues_send(session, capsys):
    session.handle_line("hello team")
    assert session.sends.get_nowait() == (NODE, "hello team")
    assert "you -> acme: hello team" in capsys.readouterr().out


def test_poll_inbox_consumes_and_renders(session, r4t_home):
    state.park_seat_message(NODE, "Neil", "acme:gerry", "[r4t task=01KX0000000000000000000000 hop=1 auto] hi")
    events = session.feed.poll_inbox()
    assert [(kind, render_envelope(e)) for kind, e in events] == [
        ("in", "acme:gerry: hi")
    ]
    assert session.feed.poll_inbox() == []
    assert len(state.list_seat_messages(NODE, "neil", read=True)) == 1


def test_run_chat_requires_human(ctx, repo, r4t_home):
    (repo / "ROSTER.md").write_text(
        "# Roster\n\n### Gerry\n- **Status:** AI\n- **Rig:** leader\n"
        "- **Leader:** yes\n",
        encoding="utf-8",
    )
    assert run_chat(ctx) == 2
