"""Stimulus handling for the ear organ.

Request:  {"action":"transcribe", "audio_path":"/path/file.mp3",
           "id":"corr-1", "from":"brain"}
  Or:     {"action":"transcribe", "audio_hash":"abc123",
           "id":"corr-1", "from":"brain"}
  Optional: language, prompt, provider, model

Response: {"id":"corr-1", "action":"transcribe", "status":"ok",
           "text":"...", "provider":"groq"}
  Error:  {"id":"corr-1", "action":"transcribe", "status":"error",
           "error":"message"}
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure sibling modules importable regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcribe import transcribe, log

DIR = Path(__file__).resolve().parent.parent
STIMULUS_DIR = DIR / ".stimulus"


def consume_stimulus_files():
    """Read .stimulus/*.json in sorted order, delete after parse."""
    if not STIMULUS_DIR.exists():
        return []
    files = sorted(STIMULUS_DIR.glob("*.json"))
    stimuli = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            stimuli.append(data)
        except (json.JSONDecodeError, OSError) as e:
            log(f"bad stimulus {f.name}: {e}")
        f.unlink(missing_ok=True)
    return stimuli


def _send_response(target, response_data):
    """Send JSON response via stimulus send."""
    body = json.dumps(response_data, default=str)
    try:
        subprocess.run(
            ["stimulus", "send", "--to", target, "--body", body],
            capture_output=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"failed to send response to {target}: {e}")


def _resolve_audio_path(stim):
    """Get audio file path from stimulus, resolving circ hash if needed."""
    if "audio_path" in stim:
        return stim["audio_path"]

    if "audio_hash" in stim:
        try:
            result = subprocess.run(
                ["circ", "get", stim["audio_hash"]],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        raise RuntimeError(f"circ get failed for hash: {stim['audio_hash']}")

    raise RuntimeError("stimulus must include audio_path or audio_hash")


def handle_transcribe(stim):
    """Handle a transcribe action."""
    try:
        audio_path = _resolve_audio_path(stim)
        result = transcribe(
            audio_path,
            language=stim.get("language", "en"),
            prompt=stim.get("prompt"),
            model=stim.get("model"),
            provider=stim.get("provider"),
        )
        return {
            "id": stim.get("id"),
            "action": "transcribe",
            "status": "ok",
            "text": result.get("text", ""),
            "provider": result.get("provider", ""),
        }
    except Exception as e:
        return {
            "id": stim.get("id"),
            "action": "transcribe",
            "status": "error",
            "error": str(e),
        }


def process_stimuli(stimuli):
    """Process stimulus list, dispatch handlers, send responses."""
    processed = 0
    for stim in stimuli:
        action = stim.get("action")
        sender = stim.get("from")

        if action != "transcribe":
            log(f"unknown action: {action}")
            if sender:
                _send_response(sender, {
                    "id": stim.get("id"),
                    "action": action,
                    "status": "error",
                    "error": f"unknown action: {action}",
                })
            continue

        response = handle_transcribe(stim)
        if sender:
            _send_response(sender, response)
        processed += 1

    return processed
