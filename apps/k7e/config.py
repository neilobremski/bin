"""k7e configuration — LLM providers, embedding backends, status reporting.

Config file: $K7E_HOME/config.json (created by `k7e init` or `k7e config`)
Falls back to env vars, then sensible defaults.

Config format:
{
  "llm": "gemini",                    # CLI for distillation: gemini|claude|ollama|codex
  "llm_model": null,                  # model override (for ollama)
  "embeddings": "ollama",             # embedding backend: ollama|none
  "embed_model": "nomic-embed-text",  # model for embeddings
  "ollama_url": "http://localhost:11434"
}
"""

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def _k7e_home():
    override = os.environ.get("K7E_HOME")
    return Path(override) if override else Path.home() / ".k7e"


def config_path():
    return _k7e_home() / "config.json"


def load_config():
    path = config_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def get(key, default=None):
    """Read config value with env var override."""
    env_map = {
        "llm": "K7E_LLM",
        "llm_model": "K7E_LLM_MODEL",
        "embeddings": "K7E_EMBEDDINGS",
        "embed_model": "EMBED_MODEL",
        "ollama_url": "OLLAMA_URL",
    }
    env_key = env_map.get(key)
    if env_key and os.environ.get(env_key):
        return os.environ[env_key]
    cfg = load_config()
    return cfg.get(key, default)


# --- Provider detection ---

KNOWN_LLMS = {
    "agy": {"bin": "agy", "invoke": ["agy", "--sandbox", "--dangerously-skip-permissions", "-p", "{prompt}"]},
    "claude": {"bin": "claude", "invoke": ["claude", "-p", "{prompt}"]},
    "codex": {"bin": "codex", "invoke": ["codex", "--full-auto", "{prompt}"]},
    "ollama": {"bin": "ollama", "invoke": None},  # uses HTTP API
}


def detect_providers():
    """Check what's available on this system."""
    results = {}

    # LLM CLIs
    for name, info in KNOWN_LLMS.items():
        binary = shutil.which(info["bin"])
        results[f"llm:{name}"] = {"available": binary is not None, "path": binary}

    # Ollama embeddings
    ollama_url = get("ollama_url", "http://localhost:11434")
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            embed_model = get("embed_model", "nomic-embed-text")
            has_embed = any(embed_model in m for m in models)
            results["embeddings:ollama"] = {
                "available": True,
                "models": models,
                "has_embed_model": has_embed,
                "embed_model": embed_model,
            }
    except (urllib.error.URLError, OSError):
        results["embeddings:ollama"] = {"available": False}

    # FTS5 (always available with Python's sqlite3)
    results["search:fts5"] = {"available": True}

    return results


def status():
    """Human-readable status report."""
    providers = detect_providers()
    cfg = load_config()
    home = _k7e_home()
    lines = ["k7e status:", ""]

    lines.append(f"  Home: {home}")
    if not home.exists():
        lines.append("    → Not initialized. Run any k7e command to create.")
    lines.append("")

    # Configured LLM
    llm = get("llm", "auto")
    lines.append(f"  Distillation LLM: {llm}")
    if llm == "auto":
        for name in KNOWN_LLMS:
            if providers.get(f"llm:{name}", {}).get("available"):
                lines.append(f"    → auto-detected: {name}")
                break
        else:
            lines.append("    → NONE detected (distill will use pattern-only extraction)")

    # Embeddings
    embed_cfg = get("embeddings", "ollama")
    embed_status = providers.get("embeddings:ollama", {})
    if embed_status.get("available"):
        if embed_status.get("has_embed_model"):
            lines.append(f"  Embeddings: ollama ({embed_status['embed_model']}) ✓")
        else:
            model = embed_status.get("embed_model", "nomic-embed-text")
            lines.append(f"  Embeddings: ollama running but model '{model}' not found")
            lines.append(f"    → Install: ollama pull {model}")
    else:
        lines.append("  Embeddings: UNAVAILABLE (ollama not running)")
        lines.append("    → Install: curl -fsSL https://ollama.com/install.sh | sh")
        lines.append(f"    → Then: ollama pull {get('embed_model', 'nomic-embed-text')}")

    # Search
    lines.append("  Search: FTS5 (keyword) ✓")
    if embed_status.get("available") and embed_status.get("has_embed_model"):
        lines.append("  Search: Semantic (embeddings) ✓")
    else:
        lines.append("  Search: Semantic (embeddings) ✗ — FTS5-only mode")

    # Recommendations
    lines.append("")
    missing = []
    if not any(providers.get(f"llm:{n}", {}).get("available") for n in KNOWN_LLMS):
        missing.append("Install an LLM CLI (gemini, claude, or ollama) for distillation")
    if not embed_status.get("has_embed_model"):
        missing.append(f"Run `ollama pull {get('embed_model', 'nomic-embed-text')}` for semantic search")
    if missing:
        lines.append("  Recommendations:")
        for m in missing:
            lines.append(f"    • {m}")
    else:
        lines.append("  All capabilities active.")

    return "\n".join(lines)


def resolve_llm_command(prompt):
    """Return the command list to run for LLM distillation, or None."""
    llm = get("llm", "auto")

    if llm == "auto":
        for name in ["agy", "claude", "codex", "ollama"]:
            if shutil.which(KNOWN_LLMS[name]["bin"]):
                llm = name
                break
        else:
            return None

    if llm == "ollama":
        return None  # handled via HTTP API in distill.py

    info = KNOWN_LLMS.get(llm)
    if not info or not shutil.which(info["bin"]):
        return None

    return [arg.replace("{prompt}", prompt) for arg in info["invoke"]]
