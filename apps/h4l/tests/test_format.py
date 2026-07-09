from __future__ import annotations

import pytest

from format import (
    DEFAULT_VIEW_LIMIT,
    format_room_view,
    parse_view_args,
    select_messages,
)


def _msg(i: int, *, sender: str = "ALICE", content: str | None = None) -> dict:
    return {
        "id": f"MSG{i:03d}",
        "date": f"2026-01-0{i}T12:00:00.000000Z",
        "from": sender,
        "content": content or f"message {i}",
    }


def _room_messages(n: int) -> list[dict]:
    return [_msg(i, sender="ALICE" if i % 2 else "BOB") for i in range(1, n + 1)]


class TestSelectMessages:
    def test_latest_window(self):
        messages = _room_messages(15)
        window, total, start = select_messages(messages, limit=10)
        assert total == 15
        assert start == 5
        assert len(window) == 10
        assert window[0]["id"] == "MSG006"

    def test_start_window(self):
        messages = _room_messages(15)
        window, total, start = select_messages(messages, limit=4, start_n=5)
        assert total == 15
        assert start == 4
        assert [m["id"] for m in window] == ["MSG005", "MSG006", "MSG007", "MSG008"]


class TestFormatRoomView:
    def test_convo_style_headings(self):
        messages = [
            _msg(1, sender="ALICE", content="hello"),
            _msg(2, sender="BOB", content="hi back"),
        ]
        text = format_room_view("war", messages, "ALICE", node="HALL")
        assert "## from ALICE to #war at" in text
        assert "hello" in text
        assert "### from BOB to #war at" in text
        assert "hi back" in text
        assert "viewed messages 1–2 of 2" in text
        assert "Older:" not in text
        assert "Newer:" not in text
        assert "MSG" not in text

    def test_default_limit_is_ten(self):
        messages = _room_messages(12)
        text = format_room_view("war", messages, "ALICE", node="HALL")
        assert f"(limit {DEFAULT_VIEW_LIMIT})" in text
        assert "viewed messages 3–12 of 12" in text
        assert "message 12" in text
        assert "message 2" not in text
        assert 'Older: tell HALL "/view war --start 1 --limit 10"' in text
        assert "Newer:" not in text

    def test_older_and_window_hints_when_paged(self):
        messages = _room_messages(12)
        text = format_room_view("war", messages, "ALICE", limit=5, node="HALL")
        assert "viewed messages 8–12 of 12" in text
        assert 'Older: tell HALL "/view war --start 3 --limit 5"' in text
        assert 'Window: tell HALL "/view war --start <n> --limit <m>"' in text

    def test_start_pagination_footer(self):
        messages = _room_messages(20)
        text = format_room_view(
            "war",
            messages,
            "ALICE",
            limit=5,
            start_n=6,
            node="HALL",
        )
        assert "viewed messages 6–10 of 20" in text
        assert 'Older: tell HALL "/view war --start 1 --limit 5"' in text
        assert 'Newer: tell HALL "/view war --start 11 --limit 5"' in text
        assert 'Latest: tell HALL "/view war"' in text

    def test_empty_room(self):
        text = format_room_view("war", [], "ALICE", node="HALL")
        assert text == '#war: no messages\n\ntell HALL "#war <message>"'


class TestParseViewArgs:
    def test_room_only(self):
        assert parse_view_args(["war"]) == ("war", DEFAULT_VIEW_LIMIT, None)

    def test_positional_limit(self):
        assert parse_view_args(["war", "5"]) == ("war", 5, None)

    def test_positional_start_and_limit(self):
        assert parse_view_args(["war", "5", "10"]) == ("war", 10, 5)

    def test_named_flags(self):
        assert parse_view_args(["war", "--start", "5", "--limit", "3"]) == (
            "war",
            3,
            5,
        )

    def test_requires_room(self):
        with pytest.raises(ValueError, match="requires <room>"):
            parse_view_args([])

    def test_unknown_token(self):
        with pytest.raises(ValueError, match="unknown"):
            parse_view_args(["war", "--nope", "1"])

    def test_rejects_before_flag(self):
        with pytest.raises(ValueError, match="unknown"):
            parse_view_args(["war", "--before", "MSG010"])
