"""Audio transcription with multi-provider support.

Providers (checked in order):
  1. whisper.cpp — local binary, no network, no API key
  2. groq — Groq Whisper API (GROQ_API_KEY)
  3. openai — OpenAI Whisper API (OPENAI_API_KEY + OPENAI_BASE_URL)

Uses Python stdlib only (urllib). No curl, no third-party packages.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = DIR / ".memory" / "whisper-provider"
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

PROVIDERS = ["whisper.cpp", "groq", "openai"]

PROVIDER_CONFIG = {
    "whisper.cpp": {
        "env_key": None,
        "binary": "whisper-cpp",
        "aliases": ["whisper", "whisper-cpp", "main"],
    },
    "groq": {
        "env_key": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3",
    },
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "url_env": "OPENAI_BASE_URL",
        "url_default": "https://api.openai.com/v1",
        "model": "whisper-1",
    },
}


def log(msg):
    print(f"ear: {msg}", file=sys.stderr)


# --- Provider detection ---

def _find_whisper_cpp():
    """Find whisper.cpp binary on PATH."""
    for name in PROVIDER_CONFIG["whisper.cpp"]["aliases"]:
        path = shutil.which(name)
        if path:
            return path
    return None


def _probe_provider(provider):
    """Check if a provider is available (has binary or API key)."""
    if provider == "whisper.cpp":
        return _find_whisper_cpp() is not None
    cfg = PROVIDER_CONFIG[provider]
    key = os.environ.get(cfg["env_key"], "").strip()
    return len(key) > 0


def detect_provider():
    """Auto-detect the first available provider."""
    for p in PROVIDERS:
        if _probe_provider(p):
            return p
    return None


def get_provider():
    """Get cached provider, or detect and cache one."""
    if CACHE_FILE.exists():
        cached = CACHE_FILE.read_text().strip()
        if cached in PROVIDERS and _probe_provider(cached):
            return cached

    provider = detect_provider()
    if provider:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(provider)
        log(f"detected whisper provider: {provider}")
    return provider


# --- Multipart HTTP (stdlib) ---

def _multipart_post(url, api_key, fields, files, timeout=120):
    """POST multipart/form-data using urllib. No curl, no requests.

    fields: dict of name -> value
    files: dict of name -> (filename, data, content_type)
    Returns parsed JSON response.
    """
    boundary = "----EarOrganBoundary9876543210"
    body = io.BytesIO()

    for name, value in fields.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.write(f"{value}\r\n".encode())

    for name, (filename, data, content_type) in files.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\n'.encode()
        )
        body.write(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.write(data)
        body.write(b"\r\n")

    body.write(f"--{boundary}--\r\n".encode())
    payload = body.getvalue()

    req = urllib.request.Request(url, data=payload)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", "ear-organ/1.0")

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()[:300]
        try:
            err = json.loads(error_body)
            msg = err.get("error", {})
            if isinstance(msg, dict):
                msg = msg.get("message", str(msg))
            raise RuntimeError(f"API error ({e.code}): {msg}")
        except (json.JSONDecodeError, RuntimeError):
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"HTTP {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"connection error: {e.reason}")


# --- Transcription by provider ---

def _transcribe_whisper_cpp(audio_path, language="en", model=None):
    """Transcribe using local whisper.cpp binary."""
    binary = _find_whisper_cpp()
    if not binary:
        raise RuntimeError("whisper.cpp not found on PATH")

    cmd = [binary, "-f", audio_path, "-l", language, "--no-timestamps"]
    if model:
        cmd.extend(["-m", model])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"whisper.cpp failed: {result.stderr[:200]}")

    return {"text": result.stdout.strip(), "provider": "whisper.cpp"}


def _transcribe_api(audio_path, language="en", prompt="Knobert",
                    model=None, provider="groq"):
    """Transcribe via Groq or OpenAI-compatible API."""
    cfg = PROVIDER_CONFIG[provider]
    api_key = os.environ.get(cfg["env_key"], "").strip()
    if not api_key:
        raise RuntimeError(f"{cfg['env_key']} not set")

    if provider == "openai":
        base = os.environ.get(cfg["url_env"], cfg["url_default"]).rstrip("/")
        url = f"{base}/audio/transcriptions"
    else:
        url = cfg["url"]

    model = model or cfg["model"]

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    filename = os.path.basename(audio_path)
    ext = os.path.splitext(filename)[1].lower()
    mime_types = {
        ".mp3": "audio/mpeg", ".m4a": "audio/x-m4a", ".wav": "audio/wav",
        ".flac": "audio/flac", ".ogg": "audio/ogg", ".webm": "audio/webm",
    }
    content_type = mime_types.get(ext, "application/octet-stream")

    fields = {"model": model, "language": language, "response_format": "json"}
    if prompt:
        fields["prompt"] = prompt

    result = _multipart_post(
        url, api_key, fields,
        {"file": (filename, audio_data, content_type)},
    )
    result["provider"] = provider
    return result


# --- Public API ---

def transcribe(audio_path, language="en", prompt="Knobert",
               model=None, provider=None):
    """Transcribe an audio file.

    Auto-detects provider if not specified.
    Returns dict with 'text' and 'provider' keys.
    Raises RuntimeError on failure.
    """
    if not os.path.isfile(audio_path):
        raise RuntimeError(f"file not found: {audio_path}")

    size = os.path.getsize(audio_path)
    if size > MAX_FILE_SIZE:
        raise RuntimeError(f"file too large: {size} bytes (max 25 MB)")

    provider = provider or get_provider()
    if not provider:
        raise RuntimeError(
            "no whisper provider available. Set GROQ_API_KEY, "
            "OPENAI_API_KEY, or install whisper.cpp"
        )

    if provider == "whisper.cpp":
        return _transcribe_whisper_cpp(audio_path, language, model)
    else:
        return _transcribe_api(audio_path, language, prompt, model, provider)
