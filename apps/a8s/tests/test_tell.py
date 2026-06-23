"""Tests for `tell` — invoked as a subprocess via the ~/bin/tell shim.

The shim delegates to `a8s tell`, which writes JSON envelopes into the
nearest `.outbox/` without requiring registry access.
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
A8S_TELL = [
    sys.executable,
    str(Path(__file__).resolve().parent.parent / "a8s.py"),
    "tell",
]


def _run(
    cwd: Path,
    *args: str,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    kw: dict = {
        "cwd": str(cwd),
        "capture_output": True,
        "text": True,
    }
    if stdin is not None:
        kw["input"] = stdin
    if env is not None:
        kw["env"] = {**os.environ, **env}
    return subprocess.run([str(TELL), *args], **kw)


def _run_a8s(cwd: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    kw: dict = {
        "cwd": str(cwd),
        "capture_output": True,
        "text": True,
    }
    if stdin is not None:
        kw["input"] = stdin
    return subprocess.run([*A8S_TELL, *args], **kw)


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


def test_a8s_tell_writes_outbox_without_registry(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run_a8s(tmp_path, "gerry", "via a8s tell")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["to"] == "gerry"
    assert msg["content"] == "via a8s tell"


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
    assert "cannot send from this directory" in res.stderr


def test_tell_help_is_opaque(tmp_path):
    res = _run(tmp_path, "--help")
    assert res.returncode == 0
    assert ".outbox" not in res.stderr
    assert ".temp" not in res.stderr


def test_tell_uses_tell_default_dir(tmp_path):
    agent = tmp_path / "agent"
    (agent / ".outbox").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    res = _run(
        elsewhere,
        "gerry",
        "via default dir",
        env={"TELL_DEFAULT_DIR": str(agent)},
    )
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(agent / ".outbox")
    assert msg["content"] == "via default dir"


def test_tell_cwd_outbox_wins_over_tell_default_dir(tmp_path):
    cwd_agent = tmp_path / "here"
    other_agent = tmp_path / "there"
    (cwd_agent / ".outbox").mkdir(parents=True)
    (other_agent / ".outbox").mkdir(parents=True)
    res = _run(
        cwd_agent,
        "gerry",
        "from cwd",
        env={"TELL_DEFAULT_DIR": str(other_agent)},
    )
    assert res.returncode == 0, res.stderr
    assert list((other_agent / ".outbox").glob("*.json")) == []
    _name, msg = _read_outbox(cwd_agent / ".outbox")
    assert msg["content"] == "from cwd"


def test_tell_default_dir_walks_up(tmp_path):
    agent = tmp_path / "agent"
    (agent / ".outbox").mkdir(parents=True)
    sub = agent / "src" / "pkg"
    sub.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    res = _run(
        elsewhere,
        "gerry",
        "default subdir",
        env={"TELL_DEFAULT_DIR": str(sub)},
    )
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(agent / ".outbox")
    assert msg["content"] == "default subdir"


def test_tell_dir_locks_mailbox(tmp_path):
    locked = tmp_path / "mailbox"
    cwd_agent = tmp_path / "cwd-agent"
    (locked / ".outbox").mkdir(parents=True)
    (cwd_agent / ".outbox").mkdir(parents=True)
    res = _run(
        cwd_agent,
        "gerry",
        "locked send",
        env={"TELL_DIR": str(locked)},
    )
    assert res.returncode == 0, res.stderr
    assert list((cwd_agent / ".outbox").glob("*.json")) == []
    _name, msg = _read_outbox(locked / ".outbox")
    assert msg["content"] == "locked send"


def test_tell_dir_missing_outbox_fails(tmp_path):
    mailbox = tmp_path / "mailbox"
    mailbox.mkdir()
    res = _run(
        tmp_path,
        "gerry",
        "nope",
        env={"TELL_DIR": str(mailbox)},
    )
    assert res.returncode != 0
    assert "cannot send from this directory" in res.stderr


def test_tell_dir_overrides_tell_default_dir(tmp_path):
    locked = tmp_path / "locked"
    other = tmp_path / "other"
    (locked / ".outbox").mkdir(parents=True)
    (other / ".outbox").mkdir(parents=True)
    res = _run(
        tmp_path,
        "gerry",
        "dir wins",
        env={"TELL_DIR": str(locked), "TELL_DEFAULT_DIR": str(other)},
    )
    assert res.returncode == 0, res.stderr
    assert list((other / ".outbox").glob("*.json")) == []
    _name, msg = _read_outbox(locked / ".outbox")
    assert msg["content"] == "dir wins"


def test_tell_lifts_file_lines_into_files_array(tmp_path):
    (tmp_path / ".outbox").mkdir()
    (tmp_path / "report.pdf").write_text("r")
    (tmp_path / "data.csv").write_text("d")
    res = _run(tmp_path, "gerry", "Here you go.", "FILE: ./report.pdf", f"FILE: {tmp_path / 'data.csv'}")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["content"] == "Here you go."
    assert msg["files"] == [
        {"filename": "report.pdf", "path": str((tmp_path / "report.pdf").resolve())},
        {"filename": "data.csv", "path": str((tmp_path / "data.csv").resolve())},
    ]


def test_tell_handles_inline_newline_file_lines(tmp_path):
    (tmp_path / ".outbox").mkdir()
    (tmp_path / "report.pdf").write_text("r")
    body = "Here you go.\nFILE: ./report.pdf"
    res = _run(tmp_path, "gerry", body)
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["content"] == "Here you go."
    assert msg["files"] == [{"filename": "report.pdf", "path": str((tmp_path / "report.pdf").resolve())}]


def test_tell_omits_from_field_without_registry(tmp_path):
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
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from mailbox import _split_content_and_files
    finally:
        sys.path.pop(0)
    (tmp_path / ".outbox").mkdir()
    (tmp_path / "x.txt").write_text("x")
    res = _run(tmp_path, "gerry", "header line\nbody line", "FILE: ./x.txt")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["to"] == "gerry"
    assert msg["files"] == [{"filename": "x.txt", "path": str((tmp_path / "x.txt").resolve())}]
    assert "header line" in msg["content"]
    assert "body line" in msg["content"]


def test_tell_attach_flag(tmp_path):
    (tmp_path / ".outbox").mkdir()
    (tmp_path / "report.pdf").write_text("r")
    res = _run(tmp_path, "gerry", "--attach", "./report.pdf", "see attached")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["content"] == "see attached"
    assert msg["files"] == [{"filename": "report.pdf", "path": str((tmp_path / "report.pdf").resolve())}]


def test_tell_file_flag_is_alias_for_attach(tmp_path):
    (tmp_path / ".outbox").mkdir()
    (tmp_path / "data.csv").write_text("d")
    res = _run(tmp_path, "gerry", "--file", "./data.csv", "csv inside")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["files"] == [{"filename": "data.csv", "path": str((tmp_path / "data.csv").resolve())}]


def test_tell_attach_before_recipient(tmp_path):
    (tmp_path / ".outbox").mkdir()
    (tmp_path / "a.txt").write_text("a")
    res = _run(tmp_path, "--attach", "./a.txt", "bob", "hello")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["to"] == "bob"
    assert msg["files"] == [{"filename": "a.txt", "path": str((tmp_path / "a.txt").resolve())}]


def test_tell_multiple_attachments(tmp_path):
    (tmp_path / ".outbox").mkdir()
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    res = _run(
        tmp_path,
        "gerry",
        "--attach",
        "./a.txt",
        "--file",
        "./b.txt",
        "two files",
    )
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["files"] == [
        {"filename": "a.txt", "path": str((tmp_path / "a.txt").resolve())},
        {"filename": "b.txt", "path": str((tmp_path / "b.txt").resolve())},
    ]


def test_tell_absolutizes_attach_relative_to_cwd_not_outbox_root(tmp_path):
    agent_root = tmp_path / "agent"
    work = agent_root / "project"
    work.mkdir(parents=True)
    (agent_root / ".outbox").mkdir()
    payload = work / "report.pdf"
    payload.write_text("payload")
    res = _run(work, "bob", "--attach", "report.pdf", "see attached")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(agent_root / ".outbox")
    assert msg["files"] == [{"filename": "report.pdf", "path": str(payload.resolve())}]


def test_tell_absolutized_attach_delivers_after_routing(fake_home, tmp_path):
    from core import Participant, files_dir, inbox_dir
    from mailbox import ensure_mailboxes, route_outboxes
    from registry import save_registry

    sender_root = tmp_path / "sender"
    recipient_root = tmp_path / "recipient"
    work = sender_root / "project"
    work.mkdir(parents=True)
    (sender_root / ".outbox").mkdir()
    recipient_root.mkdir()
    payload = work / "data.txt"
    payload.write_text("hello file")
    save_registry(
        {"SENDER": {"root": str(sender_root)}, "BOB": {"root": str(recipient_root)}}
    )
    res = _run_a8s(work, "BOB", "--attach", "data.txt", "see attached")
    assert res.returncode == 0, res.stderr
    sender = Participant("SENDER", sender_root)
    bob = Participant("BOB", recipient_root)
    ensure_mailboxes(sender)
    ensure_mailboxes(bob)
    route_outboxes([sender, bob], all_agents=[sender, bob])
    copied = list(files_dir(bob.root).iterdir())
    assert len(copied) == 1
    assert copied[0].read_text() == "hello file"
    delivered = json.loads(next(inbox_dir("BOB").iterdir()).read_text())
    assert delivered["files"] == [{"filename": "data.txt", "path": "./.files/data.txt"}]


def test_tell_rejects_attachment_outside_cwd_and_mailbox(tmp_path):
    agent = tmp_path / "agent"
    (agent / ".outbox").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    res = _run(agent, "bob", "--attach", str(outside), "hi")
    assert res.returncode == 1
    assert "outside allowed paths" in res.stderr
    assert list((agent / ".outbox").glob("*.json")) == []


def test_tell_rejects_missing_attachment(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "bob", "--attach", "./missing.txt", "hi")
    assert res.returncode == 1
    assert "not found" in res.stderr


def test_tell_allows_attach_from_cwd_when_outbox_via_tell_dir(tmp_path):
    mailbox = tmp_path / "mailbox"
    (mailbox / ".outbox").mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    note = workspace / "note.txt"
    note.write_text("x")
    res = _run(
        workspace,
        "bob",
        "--attach",
        "./note.txt",
        "hi",
        env={"TELL_DIR": str(mailbox)},
    )
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(mailbox / ".outbox")
    assert msg["files"] == [{"filename": "note.txt", "path": str(note.resolve())}]


def test_tell_stdin_dash(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "gerry", "-", stdin="payload from stdin\n")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["content"] == "payload from stdin"


def test_tell_stdin_auto_detect(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "gerry", stdin="auto-detected body")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(tmp_path / ".outbox")
    assert msg["content"] == "auto-detected body"


def test_tell_stamps_from_when_registered(fake_home, tmp_path, monkeypatch):
    from registry import save_registry

    agent_root = tmp_path / "agent"
    agent_root.mkdir()
    (agent_root / ".outbox").mkdir()
    save_registry({"SENDER": {"root": str(agent_root)}, "bob": {"root": str(tmp_path / "bob")}})
    (tmp_path / "bob").mkdir()
    monkeypatch.chdir(agent_root)

    res = _run_a8s(agent_root, "bob", "registered send")
    assert res.returncode == 0, res.stderr
    _name, msg = _read_outbox(agent_root / ".outbox")
    assert msg["from"] == "SENDER"
    assert msg["to"] == "bob"


def test_tell_attach_requires_path(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "gerry", "--attach")
    assert res.returncode == 2
    assert "--attach requires a path" in res.stderr


def test_tell_check_ok_without_recipient(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "--check")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines()[0] == "tell: ok"
    assert f"send-from: {tmp_path.resolve()}" in res.stdout
    assert "via: cwd" in res.stdout
    assert list((tmp_path / ".outbox").glob("*.json")) == []


def test_tell_check_validates_recipient(fake_home, tmp_path, monkeypatch):
    from registry import save_registry

    agent_root = tmp_path / "agent"
    agent_root.mkdir()
    (agent_root / ".outbox").mkdir()
    bob_root = tmp_path / "bob"
    bob_root.mkdir()
    save_registry({"SENDER": {"root": str(agent_root)}, "bob": {"root": str(bob_root)}})
    monkeypatch.chdir(agent_root)

    res = _run_a8s(agent_root, "--check", "bob")
    assert res.returncode == 0, res.stderr
    assert "recipient 'bob': ok" in res.stdout
    assert "sender: SENDER" in res.stdout
    assert list((agent_root / ".outbox").glob("*.json")) == []


def test_tell_check_unknown_recipient_fails(fake_home, tmp_path, monkeypatch):
    from registry import save_registry

    agent_root = tmp_path / "agent"
    agent_root.mkdir()
    (agent_root / ".outbox").mkdir()
    save_registry({"SENDER": {"root": str(agent_root)}})
    monkeypatch.chdir(agent_root)

    res = _run_a8s(agent_root, "--check", "ghost")
    assert res.returncode == 1
    assert "no agent or alias named 'ghost'" in res.stderr
    assert list((agent_root / ".outbox").glob("*.json")) == []


def test_tell_check_fails_without_outbox(tmp_path):
    res = _run(tmp_path, "--check")
    assert res.returncode == 1
    assert "cannot send from this directory" in res.stderr


def test_tell_check_reports_tell_dir(tmp_path):
    locked = tmp_path / "mailbox"
    (locked / ".outbox").mkdir(parents=True)
    res = _run(
        tmp_path,
        "--check",
        env={"TELL_DIR": str(locked)},
    )
    assert res.returncode == 0, res.stderr
    assert "via: TELL_DIR" in res.stdout
    assert f"send-from: {locked.resolve()}" in res.stdout


def test_tell_check_rejects_message_body(tmp_path):
    (tmp_path / ".outbox").mkdir()
    res = _run(tmp_path, "--check", "bob", "hello")
    assert res.returncode == 2
    assert "does not accept a message" in res.stderr


def test_tell_help_omits_check(tmp_path):
    res = _run(tmp_path, "--help")
    assert res.returncode == 0
    assert "--check" not in res.stderr
