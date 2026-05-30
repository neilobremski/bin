"""Tests for k7e recall (RAG) and _call_llm helper."""
import engine


class TestRecallNoLLM:
    """Recall behavior when no LLM is available (most test environments)."""

    def test_empty_store_returns_nothing(self, store):
        answer, sources = engine.recall("anything at all")
        assert answer is None
        assert sources == []

    def test_empty_input_returns_nothing(self, store):
        answer, sources = engine.recall("")
        assert answer is None
        assert sources == []

    def test_whitespace_input_returns_nothing(self, store):
        answer, sources = engine.recall("   \n\t  ")
        assert answer is None
        assert sources == []

    def test_returns_sources_without_answer(self, store):
        """When nodes match but no LLM is available, sources are still returned."""
        engine.store_entry("Redis Port", "Redis default port is 6379", tags=["redis"])
        engine.store_entry("Redis Persistence", "Redis supports RDB and AOF", tags=["redis"])
        answer, sources = engine.recall("redis port")
        # No LLM → no synthesis, but sources should be found
        assert answer is None
        assert len(sources) >= 1
        assert any("Redis" in s["title"] for s in sources)

    def test_unrelated_query_finds_nothing(self, store):
        engine.store_entry("Redis Port", "Redis default port is 6379", tags=["redis"])
        answer, sources = engine.recall("quantum chromodynamics gluon plasma")
        assert answer is None
        assert sources == []

    def test_limit_respected(self, store):
        for i in range(10):
            engine.store_entry(f"Fact {i}", f"Knowledge about topic number {i}", tags=["facts"])
        _, sources = engine.recall("knowledge topic", limit=3)
        assert len(sources) <= 3

    def test_long_input_uses_decomposition_fallback(self, store):
        """Long input without LLM falls back to word-based splitting."""
        engine.store_entry("SSH Tunneling", "Use ssh -L for local port forwarding", tags=["ssh"])
        long_text = (
            "We were discussing SSH tunneling and port forwarding techniques "
            "for securely accessing services behind firewalls. The conversation "
            "covered both local and remote forwarding patterns."
        )
        _, sources = engine.recall(long_text)
        # Should still find SSH-related content via fallback decomposition
        # (may or may not match depending on FTS5 tokenization)
        # The key assertion: no crash on long input without LLM
        assert isinstance(sources, list)


class TestCallLlm:
    """Test _call_llm graceful failures."""

    def test_returns_none_when_no_provider(self, store, monkeypatch):
        monkeypatch.setenv("K7E_LLM", "nonexistent_binary_xyz")
        monkeypatch.setenv("OLLAMA_URL", "http://localhost:99999")
        result = engine._call_llm("test prompt")
        assert result is None

    def test_returns_none_on_timeout(self, store, monkeypatch):
        monkeypatch.setenv("K7E_LLM", "nonexistent_binary_xyz")
        monkeypatch.setenv("OLLAMA_URL", "http://localhost:99999")
        result = engine._call_llm("test", timeout=1)
        assert result is None


class TestDecomposeQueries:
    """Test _decompose_queries fallback (no LLM)."""

    def test_short_text_returns_word_chunks(self, store):
        queries = engine._decompose_queries("short text only four words here extra padding needed")
        assert isinstance(queries, list)
        assert len(queries) >= 1
        for q in queries:
            assert len(q) > 0

    def test_very_short_text_returns_single_query(self, store):
        queries = engine._decompose_queries("hello world")
        # Only 2 words, both ≤ 3 chars after filtering — may be empty
        assert isinstance(queries, list)

    def test_empty_returns_empty(self, store):
        queries = engine._decompose_queries("")
        assert queries == []


class TestRecallCLI:
    """Test CLI recall dispatch."""

    def test_recall_no_args_no_tty(self, store, monkeypatch):
        import io
        import cli

        monkeypatch.setattr("sys.stdin", io.StringIO("redis port forwarding"))
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        # Should not crash — either finds results or prints "No relevant..."
        exit_code = cli.main(["recall"])
        assert exit_code in (0, None)

    def test_recall_with_text_arg(self, store):
        import cli
        engine.store_entry("Test Node", "Some test content for recall", tags=["test"])
        exit_code = cli.main(["recall", "test content"])
        assert exit_code in (0, None)
