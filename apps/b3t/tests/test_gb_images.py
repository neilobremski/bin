"""`gb images` carries on past a slot it cannot fill.

On 2026-10-01 slot 1 (the header) was not on disk yet, the run stopped with
"image slot 1 points at editions/2026-10-04/header.jpg, which is missing",
and the band flyer in slot 2 never went up. Every slot is now tried; skips
and failures are named in a summary, and any of them still fails the run.

No browser: the per-slot upload is a fake.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import givebacks


def _edition(tmp_path, *files):
    for name in files:
        (tmp_path / name).write_bytes(b"img")
    return str(tmp_path)


class FakeUpload:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, args):
        self.calls.append((args.index, args.name))
        return 1 if args.index in self.fail else 0


PENDING = [(1, "header.jpg", "20261004-header.jpg"),
           (2, "band.png", "20261004-band.png"),
           (3, "math.png", "20261004-math.png")]


def test_missing_slot_is_skipped_and_the_rest_still_upload(tmp_path, capsys):
    ed = _edition(tmp_path, "band.png", "math.png")
    up = FakeUpload()
    rc = givebacks._upload_pending(PENDING, [], ed, "draft", upload=up)
    assert [c[0] for c in up.calls] == [2, 3]
    assert rc == 1                                   # a skip is not success
    err = capsys.readouterr().err
    assert "2 image(s) uploaded, 0 already in place, 1 skipped, 0 failed" in err
    assert "SKIPPED [1] 20261004-header.jpg" in err and "missing" in err


def test_a_failed_upload_does_not_stop_the_next_slot(tmp_path, capsys):
    ed = _edition(tmp_path, "header.jpg", "band.png", "math.png")
    up = FakeUpload(fail={2})
    rc = givebacks._upload_pending(PENDING, [], ed, "draft", upload=up)
    assert [c[0] for c in up.calls] == [1, 2, 3]
    assert rc == 1
    assert "FAILED  [2] 20261004-band.png" in capsys.readouterr().err


def test_a_mismatched_live_slot_is_skipped_not_overwritten(tmp_path, capsys):
    ed = _edition(tmp_path, "header.jpg", "band.png", "math.png")
    live = ["logo", "https://s3/1700000000000-20260920-header.jpg", "", ""]
    up = FakeUpload()
    rc = givebacks._upload_pending(PENDING, live, ed, "draft", upload=up)
    assert [c[0] for c in up.calls] == [2, 3]
    assert rc == 1
    assert "live draft holds 1700000000000-20260920-header.jpg" in capsys.readouterr().err


def test_all_done_or_already_in_place_exits_zero(tmp_path, capsys):
    ed = _edition(tmp_path, "header.jpg", "band.png", "math.png")
    live = ["logo", "https://s3/1759999999999-20261004-header.jpg", "", ""]
    up = FakeUpload()
    rc = givebacks._upload_pending(PENDING, live, ed, "draft", upload=up)
    assert rc == 0
    assert [c[0] for c in up.calls] == [2, 3]
    assert "2 image(s) uploaded, 1 already in place, 0 skipped, 0 failed" in capsys.readouterr().err
