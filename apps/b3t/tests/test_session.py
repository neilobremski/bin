"""session.run()/navigate()'s handling of a stuck "leave this page?" prompt.

A browser dialog left standing blocks every later playwright-cli call with
"does not handle the modal state" (`MODAL_STUCK`). `run()` answers a prompt
nobody asked for with a dismiss, by default. `navigate()` is a caller that
*is* asking to leave, so it answers with `dialog-accept` instead, and
confirms the page actually changed.

No real browser here: `_raw_run` (the one function that shells out to
`playwright-cli`) is mocked, and every other call in `session.py` is
exercised for real against it.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import session


def _cp(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def _fake_raw_run(goto_stuck_once):
    """A `_raw_run` stand-in for a single `navigate()` call.

    `goto_stuck_once`: the first `goto` reports the stuck-modal error and a
    retry succeeds; false answers every `goto` cleanly the first time.
    """
    calls = []
    state = {"goto_attempts": 0}

    def fake(cmd, timeout):
        calls.append(list(cmd))
        tail = cmd[-2:]
        if tail == ["eval", "() => true"]:
            return _cp(cmd, 0, '"true"')
        if tail == ["eval", "() => document.visibilityState"]:
            return _cp(cmd, 0, '"visible"')
        if cmd[-1] == "() => { window.onbeforeunload = null; return true; }":
            return _cp(cmd, 0, '"true"')
        if tail == ["eval", "() => window.location.href"]:
            # Still the old page until a `goto` has actually landed.
            landed = state["goto_attempts"] >= (2 if goto_stuck_once else 1)
            url = "https://rmsptsa.org/Home" if landed else "https://rmsptsa.org/PageManager/Edit/25371"
            return _cp(cmd, 0, '"%s"' % url)
        if cmd[-2] == "goto":
            state["goto_attempts"] += 1
            if goto_stuck_once and state["goto_attempts"] == 1:
                return _cp(cmd, 1, "", "Error: does not handle the modal state")
            return _cp(cmd, 0, "")
        if cmd[-1] in ("dialog-accept", "dialog-dismiss"):
            return _cp(cmd, 0, "")
        return _cp(cmd, 0, "")

    return fake, calls


def test_navigate_answers_a_stuck_leave_page_prompt_with_accept(monkeypatch, capsys):
    """The first `goto` hits the stuck-modal error; `navigate` must answer it
    with `dialog-accept` (never the generic dismiss), retry once, and confirm
    the retry actually landed somewhere new before calling it a success."""
    fake, calls = _fake_raw_run(goto_stuck_once=True)
    monkeypatch.setattr(session, "_raw_run", fake)

    rc = session.navigate("https://rmsptsa.org/Home")

    assert rc == 0
    dialog_cmds = [c[-1] for c in calls if c[-1] in ("dialog-accept", "dialog-dismiss")]
    assert dialog_cmds == ["dialog-accept"]
    goto_cmds = [c for c in calls if c[-2] == "goto"]
    assert len(goto_cmds) == 2, "goto must be retried exactly once after the accept"
    assert "accepted a stuck leave-page prompt" in capsys.readouterr().err


def test_navigate_with_no_stuck_prompt_never_touches_a_dialog(monkeypatch, capsys):
    """The ordinary case: nothing stuck, nothing to answer."""
    fake, calls = _fake_raw_run(goto_stuck_once=False)
    monkeypatch.setattr(session, "_raw_run", fake)

    rc = session.navigate("https://rmsptsa.org/Home")

    assert rc == 0
    assert not any(c[-1] in ("dialog-accept", "dialog-dismiss") for c in calls)
    assert "leave-page prompt" not in capsys.readouterr().err


def test_run_default_still_dismisses_an_unrelated_stuck_dialog(monkeypatch):
    """`run()`'s own default (no caller opted into `on_dialog="accept"`) is
    unchanged: a prompt nobody asked for is dismissed, not accepted."""
    calls = []
    attempts = {"n": 0}

    def fake(cmd, timeout):
        calls.append(list(cmd))
        if cmd[-2] == "click":
            attempts["n"] += 1
            if attempts["n"] == 1:
                return _cp(cmd, 1, "", "Error: does not handle the modal state")
            return _cp(cmd, 0, "")
        return _cp(cmd, 0, "")

    monkeypatch.setattr(session, "_raw_run", fake)

    result = session.run("click", "e5")
    assert result.returncode == 0
    dialog_cmds = [c[-1] for c in calls if c[-1] in ("dialog-accept", "dialog-dismiss")]
    assert dialog_cmds == ["dialog-dismiss"]
