"""`ol check` reads the whole folder, not just the rendered rows.

Outlook virtualises the message list: on 2026-10-01 a snapshot of the Inbox
held 6 of its 19 rows. `collect_messages` scrolls and merges screens, keyed
by the row's own text (a snapshot ref does not survive a re-render), and says
how it stopped: the end of the folder, the `--limit`, or a partial read.

No browser: snapshots and the scroll are fakes.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import outlook


def row(n, ref=None, unread=False):
    state = "Unread " if unread else ""
    return (f'      - option "{state}Sender {n} Subject number {n} 9/{n:02d}/2026 '
            f'preview text {n}" [ref={ref or f"e{n}"}]')


class FakeList:
    """A 19-row folder shown 6 rows at a time, 3 rows per scroll step."""

    def __init__(self, total=19, window=6, step=3, measurable=True):
        self.total, self.window, self.step = total, window, step
        self.top, self.measurable = 0, measurable
        self.scrolls = 0
        self.renders = 0

    def snapshot(self):
        self.renders += 1
        lines = ['    - listbox "Message list" [ref=lb]:']
        for n in range(self.top + 1, min(self.top + self.window, self.total) + 1):
            # Refs change between renders, as a re-rendered row's would.
            lines.append(row(n, ref=f"e{n}r{self.renders}", unread=(n % 4 == 0)))
        return "\n".join(lines)

    def scroll(self):
        self.scrolls += 1
        self.top = min(self.top + self.step, max(0, self.total - self.window))
        if not self.measurable:
            return None
        return {"top": self.top * 10, "h": self.total * 10, "c": self.window * 10}


def test_reads_every_row_of_a_virtualised_folder():
    fake = FakeList()
    msgs, why = outlook.collect_messages(limit=0, snapshot=fake.snapshot,
                                         scroll=fake.scroll)
    assert why == "end"
    assert len(msgs) == 19
    assert [re.search(r"Sender (\d+)", m["text"]).group(1) for m in msgs] == \
        [str(n) for n in range(1, 20)]


def test_limit_stops_early_and_the_last_ref_is_from_the_current_render():
    fake = FakeList()
    msgs, why = outlook.collect_messages(limit=10, snapshot=fake.snapshot,
                                         scroll=fake.scroll)
    assert why == "limit" and len(msgs) == 10
    # Row 10 completed the limit on this render, so its ref is clickable now.
    assert msgs[-1]["ref"] == f"e10r{fake.renders}"


def test_a_folder_that_fits_on_one_screen_ends_at_once():
    fake = FakeList(total=4)
    msgs, why = outlook.collect_messages(limit=50, snapshot=fake.snapshot,
                                         scroll=fake.scroll)
    assert why == "end" and len(msgs) == 4
    assert fake.scrolls == 1


def test_unmeasurable_list_is_reported_as_unconfirmed():
    fake = FakeList(measurable=False)
    msgs, why = outlook.collect_messages(limit=0, snapshot=fake.snapshot,
                                         scroll=fake.scroll)
    assert len(msgs) == 19
    assert why == "stalled"


def test_scroll_budget_running_out_is_partial():
    fake = FakeList(total=200)
    msgs, why = outlook.collect_messages(limit=0, max_steps=5,
                                         snapshot=fake.snapshot, scroll=fake.scroll)
    assert why == "steps"
    assert len(msgs) == 6 + 5 * 3


def test_new_rows_are_handed_over_while_they_are_on_screen():
    fake = FakeList()
    batches = []
    outlook.collect_messages(limit=0, snapshot=fake.snapshot, scroll=fake.scroll,
                             on_new=lambda new: batches.append(len(new)))
    assert batches[0] == 6 and sum(batches) == 19


def test_status_words_do_not_split_one_row_in_two():
    """A row read as "Unread ..." on one screen and plain on the next is one row."""
    shots = iter([
        "\n".join([row(1, unread=True), row(2)]),
        "\n".join([row(1), row(2), row(3)]),
        "\n".join([row(2), row(3)]),
    ])
    geos = iter([{"top": 0, "h": 30, "c": 20}, {"top": 10, "h": 30, "c": 20}])
    msgs, why = outlook.collect_messages(limit=0, snapshot=lambda: next(shots),
                                         scroll=lambda: next(geos))
    assert len(msgs) == 3 and why == "end"


def _senders(msgs):
    return [int(re.search(r"Sender (\d+)", m["text"]).group(1)) for m in msgs]


def _failing_at(fake, top, times=None):
    """fake.snapshot, but None while the list sits at `top` (`times` times, or always)."""
    left = [times]

    def snap():
        if fake.top == top and (left[0] is None or left[0] > 0):
            if left[0] is not None:
                left[0] -= 1
            return None
        return fake.snapshot()
    return snap


def _with_top(fake):
    """fake.scroll that also takes `to_top`, as _scroll_message_list does."""
    def scroll(to_top=False):
        if to_top:
            fake.top = 0
            return {"top": 0, "h": fake.total * 10, "c": fake.window * 10}
        return fake.scroll()
    return scroll


def test_a_failed_snapshot_is_read_again_without_scrolling():
    fake = FakeList()
    msgs, why = outlook.collect_messages(limit=0, snapshot=_failing_at(fake, 0, times=1),
                                         scroll=fake.scroll)
    assert why == "end" and _senders(msgs) == list(range(1, 20))


@pytest.mark.parametrize("top,read", [(0, 0), (6, 9), (13, 18)])
def test_a_screen_that_cannot_be_read_stops_the_walk(top, read):
    """First, middle and last screens: no gap is skipped, nothing claims the end."""
    fake = FakeList()
    msgs, why = outlook.collect_messages(limit=0, snapshot=_failing_at(fake, top),
                                         scroll=fake.scroll)
    assert why == "error"
    assert _senders(msgs) == list(range(1, read + 1))
    assert fake.top == top                     # did not scroll past it


def test_an_empty_folder_ends_cleanly():
    fake = FakeList(total=0)
    msgs, why = outlook.collect_messages(limit=0, snapshot=fake.snapshot,
                                         scroll=fake.scroll)
    assert msgs == [] and why == "end"


def test_pick_row_counts_from_the_top_after_check_left_the_list_scrolled(monkeypatch):
    fake = FakeList()
    outlook.collect_messages(limit=0, snapshot=fake.snapshot, scroll=fake.scroll)
    assert fake.top == 13                      # where `ol check` leaves it
    monkeypatch.setattr(outlook.session, "snapshot", fake.snapshot)
    target = outlook._pick_row(1, "Inbox", scroll=_with_top(fake))
    assert _senders([target]) == [1]


def test_pick_row_refuses_when_the_top_cannot_be_confirmed(monkeypatch, capsys):
    fake = FakeList()
    monkeypatch.setattr(outlook.session, "snapshot", fake.snapshot)
    assert outlook._pick_row(1, "Inbox", scroll=lambda to_top=False: None) is None
    assert "top" in capsys.readouterr().err


def test_pick_row_will_not_number_across_an_unread_screen(monkeypatch, capsys):
    fake = FakeList()
    monkeypatch.setattr(outlook.session, "snapshot", _failing_at(fake, 6))
    assert outlook._pick_row(12, "Inbox", scroll=_with_top(fake)) is None
    assert "could not read" in capsys.readouterr().err


class _StopAtClick(Exception):
    pass


def _commands(monkeypatch, fake, snapshot=None):
    """Patch the browser seams so cmd_check / cmd_reply run on the fake list."""
    clicked = []

    def run(*args, **kwargs):
        if args[0] == "click":
            clicked.append(args[1])
            raise _StopAtClick
        raise AssertionError(f"unexpected browser call {args}")

    monkeypatch.setattr(outlook, "_ensure_outlook", lambda: True)
    monkeypatch.setattr(outlook, "_click_folder", lambda folder: True)
    monkeypatch.setattr(outlook, "_scroll_message_list", _with_top(fake))
    monkeypatch.setattr(outlook.session, "snapshot", snapshot or fake.snapshot)
    monkeypatch.setattr(outlook.session, "run", run)
    return clicked


def test_check_then_numbered_reply_opens_the_numbered_row(monkeypatch, tmp_path):
    """The folder click keeps the viewport `ol check` left; reply --number 1 must
    still open row 1, not whatever row is on screen."""
    fake = FakeList()
    clicked = _commands(monkeypatch, fake)
    assert outlook.cmd_check(SimpleNamespace(folder="Inbox", addresses=False, limit=0)) == 0
    assert fake.top == 13
    body = tmp_path / "reply.md"
    body.write_text("Subject: Reply\n\nSynthetic reply.\n")
    with pytest.raises(_StopAtClick):
        outlook.cmd_reply(SimpleNamespace(file=str(body), folder="Inbox", number=1, match=None))
    assert re.match(r"e1r\d+$", clicked[0])


def test_check_retries_a_failed_first_snapshot(monkeypatch, capsys):
    fake = FakeList()
    _commands(monkeypatch, fake, snapshot=_failing_at(fake, 0, times=1))
    assert outlook.cmd_check(SimpleNamespace(folder="Inbox", addresses=False, limit=0)) == 0
    out = capsys.readouterr()
    assert "19 messages (whole folder)" in out.err and "Sender 1 " in out.out


def test_check_reports_an_unreadable_screen_as_partial(monkeypatch, capsys):
    fake = FakeList()
    _commands(monkeypatch, fake, snapshot=_failing_at(fake, 0))
    assert outlook.cmd_check(SimpleNamespace(folder="Inbox", addresses=False, limit=0)) == 1
    err = capsys.readouterr().err
    assert "PARTIAL" in err and "whole folder" not in err
