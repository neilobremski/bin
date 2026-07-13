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


def test_handle_command_attach_and_detach(roster, human):
    attached = handle_command(roster, NODE, human, "/attach phil")
    assert attached.attach == "phil" and attached.detach is False
    assert "attached to phil" in attached.lines[0]
    miss = handle_command(roster, NODE, human, "/attach nobody")
    assert miss.attach is None and "no AI member" in miss.lines[0]
    human_miss = handle_command(roster, NODE, human, "/attach neil")
    assert human_miss.attach is None  # the human is not a dispatchable member
    detached = handle_command(roster, NODE, human, "/detach")
    assert detached.detach is True


def test_help_lists_attach_and_detach(session, capsys):
    session.handle_line("/help")
    out = capsys.readouterr().out
    assert "/attach" in out and "/detach" in out


def test_member_log_event_filters_by_member():
    from chat import member_log_event

    line = 'r4t: QUEUED gerry -> vela thread=T hop=0 "hi" (depth 1)'
    assert member_log_event(line, "vela") == line
    assert member_log_event(line, "cass") is None
    assert member_log_event("### Prompt", "vela") is None


def test_member_watch_streams_recv_and_output(r4t_home):
    from chat import MemberWatch

    watch = MemberWatch(NODE, "phil")
    watch.poll()  # first poll syncs the log offset to the end (skips history)
    state.append_log(NODE, 'r4t: QUEUED gerry -> phil thread=T hop=0 "do it" (depth 1)')
    state.reset_live_log(NODE, "phil")
    with state.live_log_path(NODE, "phil").open("a", encoding="utf-8") as f:
        f.write("thinking out loud\n")
    events = watch.poll()
    kinds = [k for k, _ in events]
    assert "recv" in kinds and "out" in kinds
    recv = next(text for kind, text in events if kind == "recv")
    assert "phil" in recv and "do it" in recv
    out = next(text for kind, text in events if kind == "out")
    assert "thinking out loud" in out


def test_member_statuses_reports_active_resting_and_queue(ctx, roster, r4t_home):
    from chat import member_statuses
    from rig import load_rig_config

    config = load_rig_config(ctx.config_path)
    state.enqueue(NODE, "phil", {"from": "acme:gerry", "body": "x"})
    rows = {r.name: r for r in member_statuses(NODE, roster, config)}
    assert rows["Phil"].queue == 1
    assert "Neil" not in rows and "Broken" not in rows  # human + errored excluded
    lock = state.AgentLock(NODE, "gerry")
    assert lock.acquire("leader")
    try:
        active = {r.name: r for r in member_statuses(NODE, roster, config)}
        assert active["Gerry"].state == "active"
    finally:
        lock.release()
