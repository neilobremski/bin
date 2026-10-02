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
