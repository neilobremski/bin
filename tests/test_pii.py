"""PII regression — same rules as .github/workflows/test.yml pii-check job."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github"))

from pii_check import (  # noqa: E402
    check_branch,
    check_diff,
    load_patterns,
    parse_patterns,
)

SAMPLE_PATTERNS = "knobert\nhetzner\n178\\.105\\.88\\.118\n"


@pytest.fixture(autouse=True)
def _pii_patterns_env(monkeypatch):
    monkeypatch.setenv("PII_PATTERNS", SAMPLE_PATTERNS)


def test_knobert_is_registered_pii_pattern():
    patterns = load_patterns()
    assert "knobert" in patterns


def test_no_pii_patterns_in_diff_vs_main():
    violations = check_branch()
    assert violations == [], _format_violations(violations)


def test_pii_check_catches_knobert_in_added_line():
    diff = "\n".join(
        [
            "diff --git a/example.md b/example.md",
            "+++ b/example.md",
            "+export TELL_DIR=/var/mailboxes/knobert",
        ]
    )
    hits = check_diff(diff, parse_patterns(SAMPLE_PATTERNS))
    assert any(p == "knobert" for p, _ in hits)


def test_load_patterns_requires_env_or_local_file(monkeypatch):
    monkeypatch.delenv("PII_PATTERNS", raising=False)
    local = Path(__file__).resolve().parents[1] / ".github" / "pii-patterns.local.txt"
    if not local.is_file():
        with pytest.raises(FileNotFoundError):
            load_patterns()


def _format_violations(violations: list[tuple[str, str]]) -> str:
    if not violations:
        return ""
    lines = [f"  pattern {p!r}: {ln}" for p, ln in violations[:10]]
    return "PII patterns found in diff vs main:\n" + "\n".join(lines)
