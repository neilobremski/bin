"""Tests for transcribe.py: provider detection, input validation, API calls."""
import json
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import transcribe as mod


# --- Provider detection ---

def test_detect_provider_groq():
    with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
        with patch.object(mod, '_find_whisper_cpp', return_value=None):
            assert mod.detect_provider() == "groq"


def test_detect_provider_whisper_cpp_first():
    with patch.object(mod, '_find_whisper_cpp', return_value="/usr/bin/whisper"):
        assert mod.detect_provider() == "whisper.cpp"


def test_detect_provider_openai():
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
        with patch.object(mod, '_find_whisper_cpp', return_value=None):
            # Clear GROQ key
            os.environ.pop("GROQ_API_KEY", None)
            assert mod.detect_provider() == "openai"


def test_detect_provider_none():
    with patch.dict(os.environ, {}, clear=True):
        with patch.object(mod, '_find_whisper_cpp', return_value=None):
            assert mod.detect_provider() is None


def test_get_provider_caches():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = Path(tmpdir) / "whisper-provider"
        cache.write_text("groq")
        with patch.object(mod, 'CACHE_FILE', cache):
            with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
                assert mod.get_provider() == "groq"


def test_get_provider_stale_cache_redetects():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = Path(tmpdir) / "whisper-provider"
        cache.write_text("whisper.cpp")
        with patch.object(mod, 'CACHE_FILE', cache):
            with patch.object(mod, '_find_whisper_cpp', return_value=None):
                with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
                    assert mod.get_provider() == "groq"


# --- Input validation ---

def test_transcribe_file_not_found():
    with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
        with pytest.raises(RuntimeError, match="file not found"):
            mod.transcribe("/nonexistent/audio.mp3", provider="groq")


def test_transcribe_file_too_large():
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"x" * (26 * 1024 * 1024))
        f.flush()
        try:
            with pytest.raises(RuntimeError, match="too large"):
                mod.transcribe(f.name, provider="groq")
        finally:
            os.unlink(f.name)


def test_transcribe_no_provider():
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"audio")
        f.flush()
        try:
            with patch.object(mod, 'get_provider', return_value=None):
                with pytest.raises(RuntimeError, match="no whisper provider"):
                    mod.transcribe(f.name)
        finally:
            os.unlink(f.name)


# --- API transcription (groq/openai) ---

def test_transcribe_groq_success():
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"fake audio data for testing")
        f.flush()
        try:
            mock_response = json.dumps({"text": "hello world"}).encode()
            mock_resp = MagicMock()
            mock_resp.read.return_value = mock_response
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)

            with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    result = mod.transcribe(f.name, provider="groq")
                    assert result["text"] == "hello world"
                    assert result["provider"] == "groq"
        finally:
            os.unlink(f.name)


def test_transcribe_openai_uses_base_url():
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"fake audio")
        f.flush()
        try:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"text": "hi"}).encode()

            with patch.dict(os.environ, {
                "OPENAI_API_KEY": "sk-test",
                "OPENAI_BASE_URL": "https://proxy.example.com/v1",
            }):
                with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
                    result = mod.transcribe(f.name, provider="openai")
                    assert result["text"] == "hi"
                    assert result["provider"] == "openai"
                    # Verify it used the custom base URL
                    call_req = mock_open.call_args[0][0]
                    assert "proxy.example.com" in call_req.full_url
        finally:
            os.unlink(f.name)


# --- whisper.cpp ---

def test_transcribe_whisper_cpp():
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"fake audio")
        f.flush()
        try:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "hello from whisper cpp"
            mock_result.stderr = ""

            with patch.object(mod, '_find_whisper_cpp', return_value="/usr/bin/whisper"):
                with patch("subprocess.run", return_value=mock_result):
                    result = mod.transcribe(f.name, provider="whisper.cpp")
                    assert result["text"] == "hello from whisper cpp"
                    assert result["provider"] == "whisper.cpp"
        finally:
            os.unlink(f.name)


# --- Multipart HTTP ---

def test_multipart_post_builds_correct_request():
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"text": "ok"}).encode()

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        result = mod._multipart_post(
            "https://api.test.com/transcribe",
            "test-key",
            {"model": "whisper-1", "language": "en"},
            {"file": ("test.mp3", b"audio", "audio/mpeg")},
        )
        assert result["text"] == "ok"
        req = mock_open.call_args[0][0]
        assert "Bearer test-key" in req.get_header("Authorization")
        assert "multipart/form-data" in req.get_header("Content-type")
