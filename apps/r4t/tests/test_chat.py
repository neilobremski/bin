"""r4t chat + seat tests — mailbox helpers, rendering, session commands."""
from __future__ import annotations

import json

import pytest

import state
from chat import ChatSession, filter_log_line, render_envelope, run_chat
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
    assert "unknown command" in capsys.readouterr().out
    assert session.handle_line("") is True


def test_plain_line_queues_send(session, capsys):
    session.handle_line("hello team")
    assert session.sends.get_nowait() == (NODE, "hello team")
    assert "you -> acme: hello team" in capsys.readouterr().out


def test_poll_inbox_consumes_and_renders(session, r4t_home):
    state.park_seat_message(NODE, "Neil", "acme:gerry", "[r4t task=01KX0000000000000000000000 hop=1 auto] hi")
    session.poll_inbox()
    assert drain_events(session) == [("in", "acme:gerry: hi")]
    session.poll_inbox()
    assert drain_events(session) == []
    assert len(state.list_seat_messages(NODE, "neil", read=True)) == 1


def test_run_chat_requires_human(ctx, repo, r4t_home):
    (repo / "ROSTER.md").write_text(
        "# Roster\n\n### Gerry\n- **Status:** AI\n- **Rig:** leader\n"
        "- **Leader:** yes\n",
        encoding="utf-8",
    )
    assert run_chat(ctx) == 2
