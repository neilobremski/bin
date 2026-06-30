"""Live compaction tests — require a running ollama instance.

Run with: pytest -m llm apps/l9m/tests/test_llm_compact.py
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

import pytest

_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR))

import l9m

pytestmark = pytest.mark.llm

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("L9M_MODEL", "qwen3:0.6b")

SAMPLE_LOG = """\
>>> what is my project directory
/Users/neilo/bin
>>> favorite color
blue
"""


def _ollama_available() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2)
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _skip_if_no_ollama():
    if not _ollama_available():
        pytest.skip("ollama not running")


@pytest.fixture
def isolated_context(tmp_path, monkeypatch):
    ctx_dir = tmp_path / "l9m"
    ctx_dir.mkdir()
    monkeypatch.setattr(l9m, "CONTEXT_DIR", ctx_dir)
    monkeypatch.setattr(l9m, "CONTEXT_FILE", ctx_dir / "context.txt")
    monkeypatch.setenv("OLLAMA_URL", OLLAMA_URL)
    monkeypatch.setenv("L9M_MODEL", MODEL)
    return ctx_dir / "context.txt"


class TestLiveCompaction:
    def test_compact_context_rewrites_file(self, isolated_context):
        before = SAMPLE_LOG
        isolated_context.write_text(before)
        limit = max(len(before) * 2, 500)

        assert l9m.compact_context(MODEL, limit)

        after = isolated_context.read_text()
        assert after.startswith("[compacted ")
        assert after != before
        assert len(after.strip()) > len("[compacted 2020-01-01T00:00:00Z]")

    def test_main_compact_flag(self, isolated_context, monkeypatch):
        isolated_context.write_text(SAMPLE_LOG)
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)

        assert l9m.main(["--compact", "-s"]) == 0

        after = isolated_context.read_text()
        assert after.startswith("[compacted ")
        assert ">>> what is my project directory" not in after
