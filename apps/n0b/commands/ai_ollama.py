"""Minimal Ollama helpers for n0b ai (vision chat)."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_VISION_MODEL = "qwen3.6"
SUGGESTED_VISION_PULL = "qwen3.6"


class OllamaError(Exception):
    pass


def resolve_vision_model(cli_model: str | None = None) -> str:
    if cli_model and cli_model.strip():
        return cli_model.strip()
    env = os.environ.get("N0B_TRANSCRIBE_VISION_MODEL", "").strip()
    if env:
        return env
    return DEFAULT_VISION_MODEL


def _request(method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip()
        raise OllamaError(f"HTTP {exc.code} from Ollama {path}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Ollama not reachable at {OLLAMA_URL} ({exc.reason}); "
            "start it with: ollama serve"
        ) from exc
    if not raw:
        return {}
    return json.loads(raw)


def ensure_ollama_running() -> None:
    _request("GET", "/api/tags", timeout=5)


def model_capabilities(model: str) -> list[str]:
    data = _request("POST", "/api/show", {"name": model}, timeout=30)
    caps = data.get("capabilities") or []
    return [str(c) for c in caps]


def ensure_vision_model(model: str) -> None:
    ensure_ollama_running()
    try:
        caps = model_capabilities(model)
    except OllamaError as exc:
        msg = str(exc)
        if "HTTP 404" in msg or "not found" in msg.lower():
            raise OllamaError(
                f"model {model!r} is not installed; try: ollama pull {SUGGESTED_VISION_PULL}"
            ) from exc
        raise
    if "vision" not in caps:
        raise OllamaError(
            f"model {model!r} has no vision support; try: ollama pull {SUGGESTED_VISION_PULL}"
        )


def _b64_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def chat_with_images(
    model: str,
    prompt: str,
    image_paths: list[Path],
    *,
    system: str | None = None,
    timeout: float = 600,
) -> str:
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append(
        {
            "role": "user",
            "content": prompt,
            "images": [_b64_image(p) for p in image_paths],
        }
    )
    data = _request(
        "POST",
        "/api/chat",
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
        },
        timeout=timeout,
    )
    message = data.get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        raise OllamaError("empty response from vision model")
    return content
