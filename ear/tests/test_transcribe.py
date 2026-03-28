"""Tests for transcribe.py: input validation and API call structure."""
import json
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

import transcribe as mod


def test_get_api_key_from_env():
    with patch.dict(os.environ, {"GROQ_API_KEY": "test-key-123"}):
        assert mod.get_api_key() == "test-key-123"


def test_get_api_key_missing_raises():
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("GROQ_API_KEY", None)
        with pytest.raises(RuntimeError, match="GROQ_API_KEY not set"):
            mod.get_api_key()


def test_transcribe_file_not_found():
    with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
        with pytest.raises(RuntimeError, match="file not found"):
            mod.transcribe("/nonexistent/audio.mp3")


def test_transcribe_file_too_large():
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"x" * (26 * 1024 * 1024))
        f.flush()
        try:
            with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
                with pytest.raises(RuntimeError, match="too large"):
                    mod.transcribe(f.name)
        finally:
            os.unlink(f.name)


def test_transcribe_success():
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"fake audio data for testing")
        f.flush()
        try:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = json.dumps({"text": "hello world"})
            mock_result.stderr = ""

            with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
                with patch("subprocess.run", return_value=mock_result) as mock_run:
                    result = mod.transcribe(f.name, language="en", prompt="Knobert")
                    assert result["text"] == "hello world"
                    # Verify curl was called with right args
                    call_args = mock_run.call_args[0][0]
                    assert "curl" in call_args[0]
                    assert any("Bearer test-key" in a for a in call_args)
        finally:
            os.unlink(f.name)


def test_transcribe_api_error():
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"fake audio")
        f.flush()
        try:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = json.dumps({"error": {"message": "rate limit"}})

            with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
                with patch("subprocess.run", return_value=mock_result):
                    with pytest.raises(RuntimeError, match="rate limit"):
                        mod.transcribe(f.name)
        finally:
            os.unlink(f.name)
