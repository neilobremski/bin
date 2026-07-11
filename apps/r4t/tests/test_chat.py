"""r4t chat tests — seat resolution, rendering, log filtering, commands."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat import (
    ChatSession,
    Seat,
    filter_log_line,
    handler_pid,
    render_envelope,
    resolve_seat,
)
from roster import load_roster


@pytest.fixture
def roster(repo):
    return load_roster(repo / "ROSTER.md")


def _registry(tmp_path: Path, *, proxy: bool = True) -> dict:
    definition = tmp_path / "human.json"
    definition.write_text(
        json.dumps({"proxy": "file"} if proxy else {"invoke": ["echo"]})
    )
    root = tmp_path / "seat-root"
    root.mkdir(exist_ok=True)
    return {
        "agents": {
            "neil": {"definition": str(definition), "root": str(root)},
            "crew-node": {"definition": str(definition), "root": str(root)},
        },
        "aliases": {},
        "namespaces": {"crew": "crew-node"},
    }


def test_resolve_seat_happy_path(tmp_path, roster):
    seat, problems = resolve_seat(_registry(tmp_path), "crew", roster)
    assert problems == []
    assert seat.node_agent == "crew-node"
    assert seat.human.name == "Neil"
    assert seat.human_agent == "neil"
    assert seat.inbox_dir == tmp_path / "seat-root" / ".inbox"
    assert seat.outbox_dir == tmp_path / "seat-root" / ".outbox"


def test_resolve_seat_missing_namespace(tmp_path, roster):
    registry = _registry(tmp_path)
    registry["namespaces"] = {}
    seat, problems = resolve_seat(registry, "crew", roster)
    assert seat is None
    assert any("a8s namespace crew" in p for p in problems)


def test_resolve_seat_unregistered_human(tmp_path, roster):
    registry = _registry(tmp_path)
    del registry["agents"]["neil"]
    seat, problems = resolve_seat(registry, "crew", roster)
    assert seat is None
    assert any("'neil' is not a registered" in p for p in problems)


def test_resolve_seat_rejects_waking_definition(tmp_path, roster):
    seat, problems = resolve_seat(_registry(tmp_path, proxy=False), "crew", roster)
    assert seat is None
    assert any("file-proxy" in p for p in problems)


def test_render_envelope_multiline_and_files():
    text = render_envelope(
        {
            "from": "crew:gerry",
            "content": "line one\nline two",
            "files": [{"filename": "report.md"}, {"path": "x"}],
        }
    )
    assert text == "crew:gerry: line one\n    line two\n    [files: report.md]"


def test_filter_log_line_shapes():
    assert filter_log_line("r4t: PARKED cadence 12s") == "r4t: PARKED cadence 12s"
    assert (
        filter_log_line("## 2026-07-11T15:23:35Z dispatch neil -> Ada (task X hop 0, rig o)")
        == "turn: neil -> Ada (task X hop 0, rig o)"
    )
    assert filter_log_line("### Output (Ada, exit 0 in 13.7s)") == "done: Ada, exit 0 in 13.7s"
    assert filter_log_line("### Prompt") is None
    assert filter_log_line("You are Ada, a member of the crew team") is None


def test_handler_pid_dead_and_missing(tmp_path):
    assert handler_pid(tmp_path, "ghost") is None
    pid_file = tmp_path / "agents" / "ghost" / "pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("999999")
    assert handler_pid(tmp_path, "ghost") is None
    import os

    pid_file.write_text(str(os.getpid()))
    assert handler_pid(tmp_path, "ghost") == os.getpid()


def _session(tmp_path, roster) -> ChatSession:
    seat, problems = resolve_seat(_registry(tmp_path), "crew", roster)
    assert problems == []
    return ChatSession(seat, roster, home=tmp_path)


def test_to_command_targets(tmp_path, roster, capsys):
    session = _session(tmp_path, roster)
    assert session.target == "crew"
    session.handle_line("/to phil")
    assert session.target == "crew:phil"
    session.handle_line("/to nobody")
    assert session.target == "crew:phil"
    session.handle_line("/to neil")
    assert session.target == "crew:phil"
    assert "no AI member" in capsys.readouterr().out
    session.handle_line("/to crew")
    assert session.target == "crew"


def test_quit_and_unknown_command(tmp_path, roster, capsys):
    session = _session(tmp_path, roster)
    assert session.handle_line("/quit") is False
    assert session.handle_line("/bogus") is True
    assert "unknown command" in capsys.readouterr().out
    assert session.handle_line("") is True


def test_send_uses_tell_with_seat_outbox(tmp_path, roster, monkeypatch):
    session = _session(tmp_path, roster)
    calls = {}

    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["outbox"] = kwargs["env"]["TELL_OUTBOX_DIR"]

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr("chat.subprocess.run", fake_run)
    session.handle_line("hello team")
    assert calls["argv"] == ["tell", "crew", "hello team"]
    assert calls["outbox"] == str(session.seat.outbox_dir)


def test_poll_inbox_announces_new_once(tmp_path, roster):
    session = _session(tmp_path, roster)
    inbox = session.seat.inbox_dir
    inbox.mkdir(parents=True)
    (inbox / "01A.json").write_text(json.dumps({"from": "crew:gerry", "content": "hi"}))
    session.poll_inbox()
    session.poll_inbox()
    events = []
    while not session.events.empty():
        events.append(session.events.get())
    assert events == [("in", "crew:gerry: hi")]
