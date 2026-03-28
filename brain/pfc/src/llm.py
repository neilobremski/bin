"""LLM provider auto-detection and caching.

Checks .memory/llm-provider for cached choice. If not cached, probes
claude, gemini, codex in order. Caches the first one that works.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = DIR / ".memory" / "llm-provider"

PROVIDERS = ["claude", "gemini", "codex"]

# How to invoke each provider with a prompt
PROVIDER_COMMANDS = {
    "claude": lambda prompt, system: [
        "claude", "-p", prompt,
        *(["-s", system] if system else []),
        "--output-format", "text",
    ],
    "gemini": lambda prompt, system: [
        "gemini", "-p", f"{system}\n\n{prompt}" if system else prompt,
    ],
    "codex": lambda prompt, system: [
        "codex", "-p", f"{system}\n\n{prompt}" if system else prompt,
    ],
}


def log(msg):
    print(f"pfc: {msg}", file=sys.stderr)


def _probe(provider):
    """Test if a provider CLI is available and responds."""
    if not shutil.which(provider):
        return False
    try:
        cmd_fn = PROVIDER_COMMANDS.get(provider)
        if not cmd_fn:
            return False
        cmd = cmd_fn("Say OK", None)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0 and len(result.stdout.strip()) > 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def detect_provider():
    """Auto-detect the first working LLM provider."""
    for provider in PROVIDERS:
        if _probe(provider):
            return provider
    return None


def get_provider():
    """Get the cached provider, or detect and cache one.

    Returns provider name string, or None if nothing works.
    """
    # Check cache
    if CACHE_FILE.exists():
        cached = CACHE_FILE.read_text().strip()
        if cached in PROVIDERS:
            return cached

    # Detect
    provider = detect_provider()
    if provider:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(provider)
        log(f"detected and cached LLM provider: {provider}")
    else:
        log("no LLM provider available")

    return provider


def invoke(prompt, system=None, provider=None):
    """Send a prompt to the LLM and return the response text.

    Uses cached/detected provider if not specified.
    Raises RuntimeError if no provider or invocation fails.
    """
    provider = provider or get_provider()
    if not provider:
        raise RuntimeError("no LLM provider available")

    cmd_fn = PROVIDER_COMMANDS.get(provider)
    if not cmd_fn:
        raise RuntimeError(f"unknown provider: {provider}")

    cmd = cmd_fn(prompt, system)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{provider} timed out")
    except FileNotFoundError:
        # Provider disappeared — clear cache and fail
        CACHE_FILE.unlink(missing_ok=True)
        raise RuntimeError(f"{provider} not found on PATH")

    if result.returncode != 0:
        # Provider failed — clear cache so next call re-detects
        CACHE_FILE.unlink(missing_ok=True)
        raise RuntimeError(f"{provider} failed: {result.stderr[:200]}")

    return result.stdout.strip()
