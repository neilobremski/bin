"""Tests for txlog.py — transaction log append-only TSV."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from txlog import HEADER, _sanitize, _ts, _txlog_path, log


class TestTxlogPath:
    def test_respects_a8s_home(self, fake_home, monkeypatch, tmp_path):
        custom = tmp_path / "custom-a8s"
        monkeypatch.setenv("A8S_HOME", str(custom))
        assert _txlog_path() == custom / "transactions.tsv"

    def test_defaults_under_home(self, fake_home):
        assert _txlog_path() == fake_home / ".a8s" / "transactions.tsv"


class TestSanitize:
    def test_strips_tabs(self):
        assert _sanitize("hello\tworld") == "hello world"

    def test_strips_newlines(self):
        assert _sanitize("line1\nline2") == "line1 line2"

    def test_strips_carriage_returns(self):
        assert _sanitize("a\rb") == "ab"

    def test_combined(self):
        assert _sanitize("a\t\n\r\tb") == "a   b"

    def test_clean_string_unchanged(self):
        assert _sanitize("nothing special") == "nothing special"


class TestTimestamp:
    def test_iso8601_format(self):
        ts = _ts()
        # e.g. 2026-05-28T14:30:01.123Z
        pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
        assert re.match(pattern, ts), f"Timestamp {ts!r} doesn't match ISO-8601 ms"

    def test_ends_with_z(self):
        assert _ts().endswith("Z")

    def test_milliseconds_are_three_digits(self):
        ts = _ts()
        ms_part = ts.split(".")[1].rstrip("Z")
        assert len(ms_part) == 3


class TestLogCreatesFile:
    def test_creates_file_with_header(self, fake_home):
        log("ROUTED", msg_id="01ABC", sender="A", recipient="B")
        path = _txlog_path()
        assert path.exists()
        lines = path.read_text().splitlines()
        assert lines[0] == HEADER

    def test_appends_without_duplicating_header(self, fake_home):
        log("ROUTED", msg_id="01ABC", sender="A", recipient="B")
        log("ROUTED", msg_id="02DEF", sender="C", recipient="D")
        lines = _txlog_path().read_text().splitlines()
        header_count = sum(1 for ln in lines if ln == HEADER)
        assert header_count == 1

    def test_creates_parent_dirs(self, fake_home, monkeypatch, tmp_path):
        deep = tmp_path / "deep" / "nested" / "a8s"
        monkeypatch.setenv("A8S_HOME", str(deep))
        log("ROUTED", msg_id="X", sender="A", recipient="B")
        assert (deep / "transactions.tsv").exists()


class TestLogFieldFormat:
    def test_tab_separated_eight_columns(self, fake_home):
        log("ROUTED", msg_id="01ABC", sender="A", recipient="B")
        lines = _txlog_path().read_text().splitlines()
        data_line = lines[1]
        fields = data_line.split("\t")
        assert len(fields) == 8

    def test_detail_truncated_at_200(self, fake_home):
        long_detail = "x" * 500
        log("ROUTED", msg_id="01ABC", sender="A", recipient="B", detail=long_detail)
        lines = _txlog_path().read_text().splitlines()
        data_line = lines[1]
        detail_field = data_line.split("\t")[7]
        assert len(detail_field) == 200

    def test_tabs_in_sender_dont_break_tsv(self, fake_home):
        log("ROUTED", msg_id="01ABC", sender="A\tX", recipient="B\tY")
        lines = _txlog_path().read_text().splitlines()
        data_line = lines[1]
        fields = data_line.split("\t")
        assert len(fields) == 8
        assert fields[3] == "A X"
        assert fields[4] == "B Y"

    def test_files_none_produces_empty_field(self, fake_home):
        log("ROUTED", msg_id="01ABC", sender="A", recipient="B", files=None)
        lines = _txlog_path().read_text().splitlines()
        data_line = lines[1]
        files_field = data_line.split("\t")[5]
        assert files_field == ""

    def test_files_list_produces_comma_joined(self, fake_home):
        log("ROUTED", msg_id="01ABC", sender="A", recipient="B",
            files=["one.txt", "two.log", "three.py"])
        lines = _txlog_path().read_text().splitlines()
        data_line = lines[1]
        files_field = data_line.split("\t")[5]
        assert files_field == "one.txt,two.log,three.py"

    def test_event_field_matches_input(self, fake_home):
        log("DROPPED", msg_id="01ABC", sender="A", recipient="B",
            detail="bad envelope")
        lines = _txlog_path().read_text().splitlines()
        data_line = lines[1]
        assert data_line.split("\t")[1] == "DROPPED"


class TestLogOSError:
    def test_unwritable_path_does_not_raise(self, fake_home, monkeypatch, tmp_path):
        # Point to a path inside a file (impossible to mkdir)
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file, not a directory")
        monkeypatch.setenv("A8S_HOME", str(blocker / "subdir"))
        # Should not raise
        log("ROUTED", msg_id="X", sender="A", recipient="B")


class TestLogIntegrationWithRoute:
    """Verify that route_outboxes produces a ROUTED line in the txlog."""

    def test_route_produces_routed_line(self, fake_home, tmp_path):
        import json

        from core import Participant, inbox_dir, outbox_dir
        from mailbox import _write_outbox, ensure_mailboxes, route_outboxes
        from registry import save_registry

        a_root = tmp_path / "a"; a_root.mkdir()
        b_root = tmp_path / "b"; b_root.mkdir()
        save_registry({"A": {"root": str(a_root)}, "B": {"root": str(b_root)}})
        a = Participant("A", a_root)
        b = Participant("B", b_root)
        ensure_mailboxes(a)
        ensure_mailboxes(b)

        _write_outbox("A", a.root, "B", "hello txlog", [])
        route_outboxes([a, b], all_agents=[a, b])

        path = _txlog_path()
        assert path.exists()
        lines = path.read_text().splitlines()
        routed_lines = [ln for ln in lines if "\tROUTED\t" in ln]
        assert len(routed_lines) >= 1
        # The ROUTED line should reference sender A and recipient B.
        assert "\tA\t" in routed_lines[0]
        assert "\tB\t" in routed_lines[0]
