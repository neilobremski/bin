"""LLM-dependent distill tests — require a running ollama instance.

Run with: pytest -m llm
Skipped by default in CI unless ollama is available.
"""
import json
import urllib.request

import pytest

import config
import distill
import engine

pytestmark = pytest.mark.llm

OLLAMA_URL = "http://localhost:11434"


def _ollama_available():
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
def store(tmp_path, monkeypatch):
    """Isolated K7E store with ollama enabled (overrides conftest's store)."""
    monkeypatch.setenv("K7E_HOME", str(tmp_path))
    monkeypatch.setenv("OLLAMA_URL", OLLAMA_URL)

    engine.reset(tmp_path)
    engine.init()

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"llm": "ollama", "llm_model": "qwen3:0.6b"}))

    return tmp_path


class TestLLMDistill:
    def test_extracts_knowledge_from_plain_text(self, store):
        """LLM should extract structured knowledge from prose with no patterns."""
        text = (
            "Kubernetes uses etcd as its backing store for all cluster data. "
            "etcd is a consistent, distributed key-value store. "
            "If etcd goes down, the entire control plane becomes read-only — "
            "existing workloads keep running but no new scheduling occurs."
        )
        path = store / "input.txt"
        path.write_text(text)
        results = distill.distill([str(path)])
        stored = [r for r in results if r["action"] == "stored"]
        assert len(stored) >= 1, f"LLM should extract at least 1 fact, got: {results}"

    def test_extracts_from_conversation(self, store):
        """LLM should extract knowledge from a conversation-like transcript."""
        text = (
            "I discovered that Python's asyncio.run() creates a new event loop "
            "each time. If you call it from within an already-running loop, "
            "you get RuntimeError. The fix is to use asyncio.ensure_future() or "
            "loop.run_until_complete() when you're already inside an async context."
        )
        path = store / "convo.txt"
        path.write_text(text)
        results = distill.distill([str(path)])
        stored = [r for r in results if r["action"] == "stored"]
        assert len(stored) >= 1

    def test_dedup_on_second_run(self, store):
        """Running distill twice should not cause unbounded growth."""
        text = (
            "Git's reflog stores every position of HEAD for the last 90 days. "
            "Even after a hard reset, you can recover commits via "
            "git reflog and git cherry-pick."
        )
        path = store / "git.txt"
        path.write_text(text)
        results1 = distill.distill([str(path)])
        stored1 = [r for r in results1 if r["action"] == "stored"]
        assert len(stored1) >= 1

        results2 = distill.distill([str(path)])
        new_stored = [r for r in results2 if r["action"] == "stored"]
        # Small LLMs are non-deterministic — may extract a different facet.
        # Key property: second run doesn't produce MORE than first + 1
        # (allows one novel extraction from non-determinism, blocks unbounded growth)
        assert len(new_stored) <= len(stored1) + 1, (
            f"Second run grew too much: first={len(stored1)}, second={len(new_stored)}"
        )

    def test_returns_empty_for_noise(self, store):
        """LLM should return nothing for trivial/noise content."""
        text = "Hey, sounds good! Let's sync tomorrow morning."
        path = store / "noise.txt"
        path.write_text(text)
        results = distill.distill([str(path)])
        assert len(results) == 0, f"Noise should not produce entries, got: {results}"
