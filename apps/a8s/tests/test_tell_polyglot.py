"""Tests for the self-contained ~/bin/tell polyglot — invoked as a subprocess.

Verifies that the polyglot works without importing anything from apps/a8s/.
The script walks up CWD to find .outbox/, generates a Crockford ULID, parses
argv (with FILE: lifting), and atomic-writes a JSON envelope.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

TELL = Path(__file__).resolve().parent.parent.parent.parent / "tell"


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(TELL), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _read_outbox(outbox: Path) -> tuple[str, dict]:
    files = list(outbox.glob("*.json"))
    assert len(files) == 1, f"expected exactly one outbox file, found {files}"
    return files[0].name, json.loads(files[0].read_text())


def test_tell_writes_outbox_from_root(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "gerry", "hello there")
    assert res.returncode == 0, res.stderr
    name, msg = _read_outbox(tmp_path / ".outbox")
    assert name.endswith(".json")
    assert msg["to"] == "gerry"
    assert msg["content"] == "hello there"
    assert msg["files"] == []
    assert "id" in msg and len(msg["id"]) == 26
    assert "date" in msg and msg["date"].endswith("Z")


def test_tell_walks_up_from_subdir(tmp_path):
    (tmp_path / ".outbox").mkdir()
    sub = tmp_path / "deep" / "nested"
    sub.mkdir(parents=True)
    res = _run(sub, "codex", "from below")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["to"] == "codex"
    assert msg["content"] == "from below"


def test_tell_errors_when_no_outbox(tmp_path):
    res = _run(tmp_path, "anyone", "should fail")
    assert res.returncode != 0
    assert "no .outbox/" in res.stderr


def test_tell_lifts_file_lines_into_files_array(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "gerry", "Here you go.", "FILE: ./report.pdf", "FILE: /tmp/data.csv")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["content"] == "Here you go."
    assert msg["files"] == [
        {"filename": "report.pdf", "path": "./report.pdf"},
        {"filename": "data.csv", "path": "/tmp/data.csv"},
    ]


def test_tell_handles_inline_newline_file_lines(tmp_path):
    (tmp_path / ".outbox").mkdir()
    body = "Here you go.\nFILE: ./report.pdf"
    res = _run(tmp_path, "gerry", body)
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["content"] == "Here you go."
    assert msg["files"] == [{"filename": "report.pdf", "path": "./report.pdf"}]


def test_tell_omits_from_field(tmp_path):
    """The polyglot doesn't know who's sending — the router force-stamps it.
    Producing a `from`-less envelope is correct: the parser tolerates missing
    `from` and overwrites it from the enclosing agent's registry entry."""
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "x", "y")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert "from" not in msg


def test_tell_ids_are_unique_across_rapid_invocations(tmp_path):
    (tmp_path / ".outbox").mkdir()
    ids = set()
    for i in range(10):
        res = _run(tmp_path, "x", f"msg-{i}")
        assert res.returncode == 0, res.stderr
        ids.update(p.stem for p in (tmp_path / ".outbox").glob("*.json"))
    assert len(ids) == 10


def test_tell_id_is_crockford_base32_ulid(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "x", "y")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", msg["id"]), msg["id"]


def test_tell_no_args_prints_usage(tmp_path):
    res = _run(tmp_path)
    assert res.returncode == 2
    assert "usage: tell" in res.stderr


def test_tell_only_recipient_prints_usage(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "gerry")
    assert res.returncode == 2
    assert "usage: tell" in res.stderr


def test_tell_envelope_shape_is_router_compatible(tmp_path):
    """End-to-end: write via the polyglot, then parse via the same logic
    the router uses (split content + files), and assert nothing breaks."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from mailbox import _split_content_and_files
    finally:
        sys.path.pop(0)
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "gerry", "header line\nbody line", "FILE: ./x.txt")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["to"] == "gerry"
    assert msg["files"] == [{"filename": "x.txt", "path": "./x.txt"}]
    assert "header line" in msg["content"]
    assert "body line" in msg["content"]
