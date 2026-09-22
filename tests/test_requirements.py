"""Requirements-group regression — a pip index directive is global, not per-file."""
from __future__ import annotations

from pathlib import Path

import pytest

REQUIREMENTS_DIR = Path(__file__).resolve().parents[1] / "requirements"


def requirement_files() -> list[Path]:
    return sorted(REQUIREMENTS_DIR.glob("*.txt"))


def test_requirement_files_exist():
    assert requirement_files()


@pytest.mark.parametrize("req_file", requirement_files(), ids=lambda p: p.name)
def test_no_primary_index_override(req_file: Path):
    # venv_util passes several of these to one pip run, where --index-url (or -i)
    # replaces PyPI for every package in the run, not just this file's. Use
    # --extra-index-url and pin the packages that must come from the extra index.
    offenders = [
        line
        for line in req_file.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip().split(" ")[0] in ("--index-url", "-i")
    ]
    assert not offenders, f"{req_file.name}: {offenders}"
