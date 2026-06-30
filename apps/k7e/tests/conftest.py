"""Shared fixtures for K7E tests."""

import sys
from pathlib import Path

import pytest

# Add k7e source to path
K7E_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(K7E_DIR))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated K7E store in tmp_path."""
    monkeypatch.setenv("K7E_HOME", str(tmp_path))
    # Disable ollama for deterministic tests
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:99999")
    # Disable LLM CLI resolution so search/recall never shell out to a real
    # provider (e.g. l9m on PATH) and block. @llm tests use their own fixtures.
    monkeypatch.setenv("K7E_LLM", "none")

    import engine
    engine.reset(tmp_path)
    engine.init()
    return tmp_path
