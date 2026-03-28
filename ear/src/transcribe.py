"""Groq Whisper transcription via curl. No Python dependencies."""
import json
import os
import subprocess
import sys

DEFAULT_MODEL = "whisper-large-v3"
API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def get_api_key():
    """Get Groq API key from GROQ_API_KEY env var."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY not set (get one at console.groq.com)")
    return key


def transcribe(audio_path, language="en", prompt="Knobert", model=None):
    """Transcribe an audio file via Groq Whisper API.

    Returns dict with 'text' key on success.
    Raises RuntimeError on failure.
    """
    if not os.path.isfile(audio_path):
        raise RuntimeError(f"file not found: {audio_path}")

    # 25 MB limit
    size = os.path.getsize(audio_path)
    if size > 25 * 1024 * 1024:
        raise RuntimeError(f"file too large: {size} bytes (max 25 MB)")

    key = get_api_key()
    model = model or DEFAULT_MODEL

    cmd = [
        "curl", "-s", "-X", "POST", API_URL,
        "--connect-timeout", "10", "--max-time", "120",
        "-H", f"Authorization: Bearer {key}",
        "-H", "Content-Type: multipart/form-data",
        "-F", f"file=@{audio_path}",
        "-F", f"model={model}",
        "-F", f"language={language}",
        "-F", "response_format=json",
    ]
    if prompt:
        cmd.extend(["-F", f"prompt={prompt}"])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"bad response: {result.stdout[:200]}")

    if "error" in data:
        msg = data["error"]
        if isinstance(msg, dict):
            msg = msg.get("message", str(msg))
        raise RuntimeError(f"API error: {msg}")

    return data
