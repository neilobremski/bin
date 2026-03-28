"""Stimulus handling for the PFC organ.

The PFC accepts any stimulus. For each one, it:
1. Sends the stimulus to the LLM with a system prompt describing skills
2. Parses the LLM's JSON response for a reply and optional signals
3. Sends the reply back to the stimulus sender
4. Sends any additional signals to other organs

LLM Response Format:
    {"reply": "text response", "signals": [
        {"to": "hippocampus", "body": {"action": "store", ...}}
    ]}
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from llm import invoke, log

DIR = Path(__file__).resolve().parent.parent
STIMULUS_DIR = DIR / ".stimulus"

SYSTEM_PROMPT = """Respond in JSON: {"reply": "text", "signals": [{"to": "organ", "body": {...}}]}

Known organs: hippocampus (memory store/search/recall), ear (audio transcription), brain (orchestrator).
Express all actions through the signals array. Do not emit shell commands.
Be concise."""


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
        log(f"failed to send to {target}: {e}")


def _parse_llm_response(text):
    """Parse LLM response as JSON. Falls back to plain text reply."""
    text = text.strip()

    # Handle markdown code blocks (with safe fence parsing)
    try:
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            text = text[start:end].strip()
    except ValueError:
        pass  # unclosed fence — try parsing as-is

    try:
        data = json.loads(text)
        signals = data.get("signals", [])
        if not isinstance(signals, list):
            signals = []
        return {
            "reply": data.get("reply", ""),
            "signals": signals,
        }
    except (json.JSONDecodeError, AttributeError):
        return {"reply": text, "signals": []}


def think(stimulus_data, provider=None):
    """Process a single stimulus through the LLM.

    Returns dict with 'reply' and 'signals'.
    """
    prompt = f"Incoming stimulus:\n{json.dumps(stimulus_data, indent=2, default=str)}"

    response_text = invoke(prompt, system=SYSTEM_PROMPT, provider=provider)
    return _parse_llm_response(response_text)


def process_stimuli(stimuli, provider=None):
    """Process all stimuli through the LLM, send responses and signals.

    Returns count of successfully processed stimuli.
    """
    processed = 0
    for stim in stimuli:
        sender = stim.get("from")
        corr_id = stim.get("id")

        try:
            result = think(stim, provider=provider)

            # Send reply to stimulus sender
            if sender and result["reply"]:
                _send_response(sender, {
                    "id": corr_id,
                    "from": "pfc",
                    "reply": result["reply"],
                })

            # Send additional signals
            for signal in result.get("signals", []):
                target = signal.get("to")
                body = signal.get("body", {})
                if target and body:
                    if "from" not in body:
                        body["from"] = "pfc"
                    if "id" not in body:
                        body["id"] = str(uuid.uuid4())[:8]
                    _send_response(target, body)

            processed += 1
        except Exception as e:
            log(f"error processing stimulus: {e}")
            if sender and corr_id:
                _send_response(sender, {
                    "id": corr_id,
                    "from": "pfc",
                    "error": str(e),
                })

    return processed
