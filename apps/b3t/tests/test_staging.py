"""Upload staging: a cloud-only OneDrive file must not go up as 0 bytes.

On 2026-10-01 an image in the edition folder was a macOS File Provider
"dataless" file. It listed at full size, but `shutil.copy2` (and shell `cat`)
produced 0 bytes, while Python's own `open().read()` hydrated it. Givebacks
took the empty file without complaint. Staging now reads the bytes, writes
them itself, and refuses any read that comes back empty or short.

A real dataless file cannot be made in a test, so the two halves of the
failure are faked: a copy that yields nothing, and a size that disagrees
with what a read returns.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import staging

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40


def _write(tmp_path, name="header.png", data=PNG):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_staged_copy_is_byte_identical_and_renamed(tmp_path):
    src = _write(tmp_path)
    staged, size = staging.stage(src, "20261004-header.png")
    assert os.path.basename(staged) == "20261004-header.png"
    assert size == len(PNG)
    assert Path(staged).read_bytes() == PNG
    assert os.path.dirname(staged) != str(tmp_path)    # never the source dir


def test_staged_copy_keeps_its_own_name_by_default(tmp_path):
    staged, _ = staging.stage(_write(tmp_path, "flyer.jpg"))
    assert os.path.basename(staged) == "flyer.jpg"


def test_staging_does_not_go_through_the_copy_that_returns_nothing(tmp_path, monkeypatch):
    """The fake: every copy helper writes an empty file, as clonefile did on
    the dataless original. Reading the bytes still gets all of them."""
    def empty_copy(src, dst, *a, **k):
        open(dst, "wb").close()
        return dst
    monkeypatch.setattr(shutil, "copy2", empty_copy)
    monkeypatch.setattr(shutil, "copyfile", empty_copy)
    monkeypatch.setattr(shutil, "copy", empty_copy)
    staged, size = staging.stage(_write(tmp_path))
    assert size == len(PNG)
    assert Path(staged).read_bytes() == PNG


def test_a_read_of_zero_bytes_is_refused_by_name(tmp_path, monkeypatch):
    """Size says 51 KB, the read says nothing: the dataless signature."""
    src = _write(tmp_path, data=b"")
    real = os.path.getsize
    monkeypatch.setattr(staging.os.path, "getsize",
                        lambda p: 51234 if p == src else real(p))
    with pytest.raises(staging.StageError) as e:
        staging.stage(src)
    assert "0 bytes" in str(e.value) and "51234" in str(e.value)
    assert "OneDrive" in str(e.value)


def test_a_short_read_is_refused(tmp_path, monkeypatch):
    src = _write(tmp_path)
    real = os.path.getsize
    monkeypatch.setattr(staging.os.path, "getsize",
                        lambda p: len(PNG) * 2 if p == src else real(p))
    with pytest.raises(staging.StageError, match="partial"):
        staging.stage(src)


def test_gb_upload_refuses_before_opening_the_editor(tmp_path, monkeypatch):
    import givebacks
    import session
    src = _write(tmp_path, data=b"")
    real = os.path.getsize
    monkeypatch.setattr(staging.os.path, "getsize",
                        lambda p: 999 if p == src else real(p))
    monkeypatch.setattr(givebacks, "ensure_authenticated", lambda: True)

    def no_browser(*a, **k):
        raise AssertionError("the editor must not be opened for a 0-byte file")
    monkeypatch.setattr(session, "navigate", no_browser)
    monkeypatch.setattr(session, "run", no_browser)
    args = type("A", (), {"id": "x", "image": src, "index": 1,
                          "name": "20261004-header.png", "expect_empty": True})()
    assert givebacks.cmd_upload(args) == 1


def test_gemini_refuses_before_opening_the_file_chooser(tmp_path, monkeypatch):
    import gemini
    import session
    src = _write(tmp_path, data=b"")
    real = os.path.getsize
    monkeypatch.setattr(staging.os.path, "getsize",
                        lambda p: 999 if p == src else real(p))

    def no_browser(*a, **k):
        raise AssertionError("no chooser may be left open for a refused file")
    monkeypatch.setattr(session, "run", no_browser)
    assert gemini._upload_file(src) == 1
