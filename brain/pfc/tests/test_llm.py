"""Tests for llm.py: provider detection, caching, invocation."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import llm as mod


def test_get_provider_from_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = Path(tmpdir) / "llm-provider"
        cache.write_text("claude")
        with patch.object(mod, 'CACHE_FILE', cache):
            assert mod.get_provider() == "claude"


def test_get_provider_invalid_cache_redetects():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = Path(tmpdir) / "llm-provider"
        cache.write_text("invalid-provider")
        with patch.object(mod, 'CACHE_FILE', cache):
            with patch.object(mod, 'detect_provider', return_value="gemini"):
                assert mod.get_provider() == "gemini"


def test_detect_provider_returns_first_working():
    def mock_probe(provider):
        return provider == "gemini"

    with patch.object(mod, '_probe', side_effect=mock_probe):
        assert mod.detect_provider() == "gemini"


def test_detect_provider_none_available():
    with patch.object(mod, '_probe', return_value=False):
        assert mod.detect_provider() is None


def test_get_provider_caches_result():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = Path(tmpdir) / "memory" / "llm-provider"
        with patch.object(mod, 'CACHE_FILE', cache):
            with patch.object(mod, 'detect_provider', return_value="claude"):
                result = mod.get_provider()
                assert result == "claude"
                assert cache.read_text() == "claude"


def test_invoke_success():
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = '{"reply": "hello", "signals": []}'

    with patch("subprocess.run", return_value=mock_result):
        text = mod.invoke("test prompt", provider="claude")
        assert "hello" in text


def test_invoke_clears_cache_on_failure():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = Path(tmpdir) / "llm-provider"
        cache.write_text("claude")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"

        with patch.object(mod, 'CACHE_FILE', cache):
            with patch("subprocess.run", return_value=mock_result):
                try:
                    mod.invoke("test", provider="claude")
                except RuntimeError:
                    pass
                assert not cache.exists()


def test_invoke_no_provider_raises():
    with patch.object(mod, 'get_provider', return_value=None):
        try:
            mod.invoke("test")
            assert False, "should have raised"
        except RuntimeError as e:
            assert "no LLM provider" in str(e)
