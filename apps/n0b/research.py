"""OpenAI o4-mini-deep-research (stdlib only)."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

from paths import BIN_ROOT


def _get_hash(prompt: str) -> str:
    clean = "".join(prompt.split())
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def _call_openai(api_key: str, prompt: str) -> dict:
    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "o4-mini-deep-research",
        "input": prompt,
        "tools": [{"type": "web_search_preview"}],
        "background": True,
    }
    req = urllib.request.Request(
        url, data=json.dumps(data).encode("utf-8"), headers=headers
    )
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(json.dumps({"error": f"HTTP Error {e.code}: {e.read().decode('utf-8')}"}))
        return {"error": "http"}
    except OSError as e:
        print(json.dumps({"error": str(e)}))
        return {"error": "os"}


def _check_status(api_key: str, response_id: str) -> dict:
    url = f"https://api.openai.com/v1/responses/{response_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except OSError as e:
        return {"error": str(e)}


def _resolve_api_key() -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        return api_key
    key_file = BIN_ROOT / ".temp" / "openai.env"
    if key_file.is_file():
        for line in key_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                return line[len("OPENAI_API_KEY="):]
    return None


def run_research(prompt_parts: list[str]) -> int:
    api_key = _resolve_api_key()
    if not api_key:
        print(json.dumps({"error": "OPENAI_API_KEY not set and not found in .temp/openai.env"}))
        return 1
    if not prompt_parts:
        print(json.dumps({"error": "No prompt provided"}))
        return 1

    prompt = " ".join(prompt_parts)
    prompt_hash = _get_hash(prompt)
    cache_dir = BIN_ROOT / ".files" / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{prompt_hash}.json"

    if cache_file.is_file():
        response_data = json.loads(cache_file.read_text())
    else:
        response_data = _call_openai(api_key, prompt)
        if response_data.get("error"):
            return 1
        cache_file.write_text(json.dumps(response_data))

    response_id = response_data.get("id")
    if not response_id:
        print(json.dumps(response_data))
        return 1

    while True:
        status_data = _check_status(api_key, response_id)
        if status_data.get("status") == "completed":
            print(json.dumps(status_data))
            return 0
        if status_data.get("status") == "failed":
            print(json.dumps(status_data))
            return 1
        sys.stderr.write(
            f"Status: {status_data.get('status', 'unknown')} — polling again in 30s\n"
        )
        time.sleep(30)
