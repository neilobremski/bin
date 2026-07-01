"""k7e configuration — LLM model, embedding backend, status reporting.

Config file: $K7E_HOME/config.json (created by `k7e init` or `k7e config`)
Falls back to env vars, then sensible defaults.

k7e talks to ollama's HTTP API directly for both generation and embeddings.
It deliberately does NOT shell out to LLM CLIs (l9m, claude, codex, …): those
carry their own rolling context / agent preamble, which would contaminate
k7e's distill, recall, and rerank prompts. Every k7e LLM call is stateless.

Config format:
{
  "llm": "ollama",                    # "ollama" (default) or "none" to disable
  "llm_model": null,                  # pin a model; null = auto-detect installed
  "embeddings": "ollama",             # embedding backend: ollama|none
  "embed_model": "nomic-embed-text",  # model for embeddings
  "ollama_url": "http://localhost:11434"
}
"""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_LLM_MODEL = "qwen3:0.6b"


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
        "decay_offset_days": "K7E_DECAY_OFFSET",
        "decay_scale_days": "K7E_DECAY_SCALE",
        "use_count_weight": "K7E_USE_WEIGHT",
        "rerank": "K7E_RERANK",
    }
    env_key = env_map.get(key)
    if env_key and os.environ.get(env_key):
        return os.environ[env_key]
    cfg = load_config()
    return cfg.get(key, default)


# --- Provider detection ---

def _list_ollama_models():
    """Return the list of model names installed in ollama, or [] if unreachable."""
    ollama_url = get("ollama_url", "http://localhost:11434")
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        return []


def _model_size(name):
    """Best-effort parameter count parsed from a model tag (e.g. '8b' -> 8.0)."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", name.lower())
    return float(m.group(1)) if m else 0.0


def resolve_llm_model(models=None):
    """Resolve the ollama model k7e should use for generation.

    Precedence: pinned `llm_model`/`K7E_LLM_MODEL` > best installed model
    (qwen family preferred, largest wins) > DEFAULT_LLM_MODEL. Pass `models`
    to reuse an already-fetched `/api/tags` listing."""
    pinned = get("llm_model")
    if pinned:
        return pinned
    if models is None:
        models = _list_ollama_models()
    candidates = [m for m in models if "embed" not in m.lower()]
    if not candidates:
        return DEFAULT_LLM_MODEL
    qwen = [m for m in candidates if m.lower().startswith("qwen")]
    pool = qwen or candidates
    pool.sort(key=_model_size, reverse=True)
    return pool[0]


def llm_available():
    """True when k7e can make an LLM call: not explicitly disabled and ollama
    is reachable. Used by the LLM-requiring commands (distill/recall/compile)
    to fail fast with an actionable message instead of degrading silently."""
    if get("llm", "ollama") == "none":
        return False
    return _ollama_reachable()


def detect_providers():
    """Check what's available on this system (single ollama probe)."""
    results = {}

    models = _list_ollama_models()
    ollama_up = bool(models) or _ollama_reachable()

    embed_model = get("embed_model", "nomic-embed-text")
    has_embed = any(embed_model in m for m in models)
    results["embeddings:ollama"] = {
        "available": ollama_up,
        "models": models,
        "has_embed_model": has_embed,
        "embed_model": embed_model,
    }

    llm_enabled = get("llm", "ollama") != "none"
    results["llm:ollama"] = {
        "available": ollama_up and llm_enabled,
        "model": resolve_llm_model(models),
        "pinned": bool(get("llm_model")),
        "enabled": llm_enabled,
    }

    # FTS5 (always available with Python's sqlite3)
    results["search:fts5"] = {"available": True}

    return results


def _ollama_reachable():
    """True if ollama answers on /api/tags even with zero models installed."""
    ollama_url = get("ollama_url", "http://localhost:11434")
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        return False


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

    # LLM (ollama, direct HTTP — no CLI shell-out)
    llm_info = providers.get("llm:ollama", {})
    if not llm_info.get("enabled", True):
        lines.append("  LLM: disabled (llm=none) — distill uses pattern-only extraction")
    else:
        model = llm_info.get("model", DEFAULT_LLM_MODEL)
        source = "configured" if llm_info.get("pinned") else "auto-detected"
        if llm_info.get("available"):
            lines.append(f"  LLM: ollama ({model}, {source}) ✓")
        else:
            lines.append(f"  LLM: ollama ({model}, {source}) ✗ — ollama not running")

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
    if llm_info.get("enabled", True) and not llm_info.get("available"):
        missing.append("Start ollama (and `ollama pull` a model) for LLM distillation/recall")
    if not embed_status.get("has_embed_model"):
        missing.append(f"Run `ollama pull {get('embed_model', 'nomic-embed-text')}` for semantic search")
    if missing:
        lines.append("  Recommendations:")
        for m in missing:
            lines.append(f"    • {m}")
    else:
        lines.append("  All capabilities active.")

    return "\n".join(lines)
