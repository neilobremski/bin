"""`outlook draft` recipient verification.

`cmd_draft` refuses to leave a draft behind unless the To box ends up holding
exactly the addresses that were asked for. That check used to trust visible
text in the To box: a resolved contact chip that Outlook renders by display
name only (no address in the text at all) slipped past it, and so did a
right address sitting next to an unrelated name-only chip. Every recipient
chip must now resolve to a real address before the draft is accepted; see
`apps/b3t/outlook.py` around `cmd_draft`.
"""
from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import outlook


def draft_file(tmp_path, body="Body text here, long enough to count."):
    p = tmp_path / "draft.md"
    p.write_text(f"**Subject:** Test subject\n\n---\n\n{body}\n")
    return str(p)


def make_args(file, to=None, subject=None):
    return types.SimpleNamespace(file=file, to=list(to or []), subject=subject)


def composer_result(to_text, to_chips, cc="", saved="draft saved at 1:00 PM", length=200):
    """A `session.run` result mimicking the composer-fill eval's stdout.

    `cmd_draft` does `json.loads(json.loads(stdout))`, matching how
    `JSON.stringify` output is wrapped when it comes back through the CLI.
    """
    info = {"saved": saved, "subj": "Test subject", "len": length,
             "to": to_text, "toChips": to_chips, "cc": cc}
    stdout = json.dumps(json.dumps(info))
    return subprocess.CompletedProcess([], 0, stdout, "")


NEW_MAIL_CLICKED = subprocess.CompletedProcess([], 0, "CLICKED", "")


@pytest.fixture(autouse=True)
def stub_outlook(monkeypatch):
    """Skip the real Outlook navigation/auth check; only `cmd_draft`'s own
    recipient logic is under test."""
    monkeypatch.setattr(outlook, "_ensure_outlook", lambda: True)


def run_draft(monkeypatch, tmp_path, composer_stdout_info, to=None):
    """Call `cmd_draft` with `session.run` stubbed for the two calls it makes
    before the recipient check: the New Mail click, then the composer fill.
    Neither test path reaches the close/reopen calls, since a failed
    recipient check returns before them.
    """
    calls = [NEW_MAIL_CLICKED, composer_stdout_info]

    def fake_run(*args, **kwargs):
        return calls.pop(0)

    monkeypatch.setattr(outlook.session, "run", fake_run)
    args = make_args(draft_file(tmp_path), to=to)
    return outlook.cmd_draft(args)


def test_name_only_wrong_recipient_is_refused(monkeypatch, tmp_path, capsys):
    """`--to alice@example.invalid` but the only chip that rendered is an
    unrelated name-only "Bob Jones" that never resolved to an address."""
    result = composer_result("Bob Jones", [None])
    rc = run_draft(monkeypatch, tmp_path, result, to=["alice@example.invalid"])
    assert rc == 1
    assert "recipients are not what was asked for" in capsys.readouterr().err


def test_mixed_address_and_extra_name_is_refused(monkeypatch, tmp_path, capsys):
    """The right address is there, but so is an unresolved extra chip."""
    result = composer_result(
        "alice@example.invalid; Bob Jones",
        ["alice@example.invalid", None])
    rc = run_draft(monkeypatch, tmp_path, result, to=["alice@example.invalid"])
    assert rc == 1
    assert "recipients are not what was asked for" in capsys.readouterr().err


def test_no_to_requested_but_a_name_appeared_is_refused(monkeypatch, tmp_path, capsys):
    """No `--to` at all: the strict empty-To check must still catch a stray
    chip, name-only or not."""
    result = composer_result("Bob Jones", [None])
    rc = run_draft(monkeypatch, tmp_path, result, to=[])
    assert rc == 1
    assert "recipients are not what was asked for" in capsys.readouterr().err


def test_no_to_requested_and_nothing_rendered_is_accepted(monkeypatch, tmp_path):
    """The unaddressed case this check must not break: nothing asked for,
    nothing in the To box, and the composer fill otherwise succeeded."""
    calls = [
        NEW_MAIL_CLICKED,
        composer_result("", []),
        subprocess.CompletedProcess([], 0, "CLOSED", ""),
        subprocess.CompletedProcess(
            [], 0, json.dumps(json.dumps({"found": True, "len": 200})), ""),
    ]

    def fake_run(*args, **kwargs):
        return calls.pop(0)

    monkeypatch.setattr(outlook.session, "run", fake_run)
    monkeypatch.setattr(outlook, "_click_folder", lambda *a, **k: None)
    args = make_args(draft_file(tmp_path), to=[])
    rc = outlook.cmd_draft(args)
    assert rc == 0


def test_a_resolved_matching_address_is_accepted(monkeypatch, tmp_path):
    """The happy path: one chip, resolved, matching what was asked for."""
    calls = [
        NEW_MAIL_CLICKED,
        composer_result("Alice Smith", ["alice@example.invalid"]),
        subprocess.CompletedProcess([], 0, "CLOSED", ""),
        subprocess.CompletedProcess(
            [], 0, json.dumps(json.dumps({"found": True, "len": 200})), ""),
    ]

    def fake_run(*args, **kwargs):
        return calls.pop(0)

    monkeypatch.setattr(outlook.session, "run", fake_run)
    monkeypatch.setattr(outlook, "_click_folder", lambda *a, **k: None)
    args = make_args(draft_file(tmp_path), to=["alice@example.invalid"])
    rc = outlook.cmd_draft(args)
    assert rc == 0
