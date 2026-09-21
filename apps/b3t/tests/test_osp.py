"""`osp._save()`'s recovery from playwright-cli's own dialog interruption.

`#SaveButton` submits the CMS form, which is dirty, so OSP's own "Leave this
page?" prompt fires on the way out. A page-level `page.once('dialog', ...)`
handler accepts it, but playwright-cli tracks dialogs independently and, per
a live reproduction against the installed CLI (PR #361's review), ends the
`run-code` response the moment the dialog appears -- before the script's
`return` -- leaving `result.stdout` with only the echoed script and a
trailing "### Modal state" block, no "### Result". The page has still
actually navigated by then. `_save()` must recognize that and recover the
real outcome by reading the URL directly, not report a failed save that
actually went through.

No real browser here: `session.run` and `session.current_url` are mocked.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import osp
import session


EDIT_URL = "https://rmsptsa.org/PageManager/Edit/25371"
HOME_URL = "https://rmsptsa.org/Home"


def _cp(returncode=0, stdout="", stderr=""):
    import subprocess
    return subprocess.CompletedProcess(["playwright-cli"], returncode, stdout, stderr)


def test_save_recovers_a_result_lost_to_the_clis_own_modal_interruption(monkeypatch):
    """The exact sequence reproduced live: `run-code` comes back with only a
    "### Modal state" block (no `### Result`), but the page already moved.
    `_save()` must not report failure just because the answer was lost."""
    urls = iter([EDIT_URL,  # before the click
                 HOME_URL])  # after recovery

    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:1] == ("run-code",):
            return _cp(0, "await (async function main(page) {...})(page);\n"
                          "### Modal state\n"
                          '- ["beforeunload" dialog with message ""]: can be '
                          "handled by dialog-accept or dialog-dismiss\n")
        if args[:1] == ("eval",) and args[1:2] == ("() => true",):
            return _cp(0, '"true"')
        raise AssertionError("unexpected session.run call: %r %r" % (args, kwargs))

    monkeypatch.setattr(osp.session, "run", fake_run)
    monkeypatch.setattr(osp.session, "current_url", lambda: next(urls))
    monkeypatch.setattr(osp.time, "sleep", lambda s: None)

    ok, url, status, dialog = osp._save()

    assert ok is True
    assert url == HOME_URL
    assert status == "recovered"
    assert "interrupted" in dialog
    # recovery reads the URL back through the ordinary stuck-modal path, and
    # never clicks #SaveButton a second time
    run_code_calls = [a for a, _ in calls if a[:1] == ("run-code",)]
    assert len(run_code_calls) == 1


def test_save_still_reports_failure_when_nothing_actually_navigated(monkeypatch):
    """A lost result is not automatically a win: if the URL never changed,
    this is still a no-save and must be reported as one."""
    def fake_run(*args, **kwargs):
        if args[:1] == ("run-code",):
            return _cp(0, "### Modal state\n- stray dialog, unrelated to Save\n")
        if args[:1] == ("eval",):
            return _cp(0, '"true"')
        raise AssertionError("unexpected session.run call: %r %r" % (args, kwargs))

    monkeypatch.setattr(osp.session, "run", fake_run)
    monkeypatch.setattr(osp.session, "current_url", lambda: EDIT_URL)
    monkeypatch.setattr(osp.time, "sleep", lambda s: None)

    ok, url, status, dialog = osp._save()

    assert ok is False
    assert url == ""
    assert "no answer" in status


def test_save_reports_the_normal_result_when_the_response_is_not_interrupted(monkeypatch):
    """The ordinary path (the `### Result` line comes back intact) is
    unchanged: no recovery attempted, no extra calls."""
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        assert args[:1] == ("run-code",)
        return _cp(0, HOME_URL + " 200 | none")

    monkeypatch.setattr(osp.session, "run", fake_run)
    monkeypatch.setattr(osp.session, "current_url", lambda: EDIT_URL)

    ok, url, status, dialog = osp._save()

    assert ok is True
    assert url == HOME_URL
    assert status == "200"
    assert dialog == "none"
    assert len(calls) == 1, "no recovery call should happen when the result parses cleanly"
