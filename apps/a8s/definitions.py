"""a8s definitions — invoke* verbs, prompt formatting, definition loading.

Each agent has a definition JSON (built-in or custom) that encodes argv for
four verbs and message templates. `select_verb` picks the verb from the
queued message, `build_prompt` formats the body, `build_command` expands
$PROMPT/$A8S_DIR placeholders into the final argv.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import (
    DEFINITIONS_DIR,
    MARKER_FILES,
    SCRIPT_DIR,
)
from registry import load_registry


VERB_KEY = {
    "prompt": "invokePrompt",
    "message": "invokeMessage",
    "messageAlias": "invokeMessageAlias",
    "clear": "invokeClear",
}


def default_definition_path(kind: str) -> Path:
    return DEFINITIONS_DIR / f"{kind}.json"


def load_definition(name: str) -> dict:
    """Load the JSON definition for `name`. Every agent always has one — if
    the registry lacks an explicit `definition` field, falls back to the
    bundled `apps/a8s/definitions/default.json` (a dummy CLI that prints
    'not configured' and the received prompt).

    Definitions encode argv (with `$PROMPT` and `$A8S_DIR` placeholders),
    message templates, and per-tool quirks. See apps/a8s/definitions/*.json.
    """
    reg = load_registry()
    info = reg.get(name) or {}
    custom = info.get("definition") or str(default_definition_path("default"))
    path = Path(custom).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"definition file missing: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.loads(f.read())
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"definition load failed for {path}: {e}") from e


def _file_lines(msg: dict) -> list[str]:
    files = msg.get("files") or []
    if not files:
        return []
    out = [""]
    for entry in files:
        path = entry.get("path") or entry.get("filename")
        if path:
            out.append(f"FILE: {path}")
    return out


def select_verb(msg: dict) -> str:
    """Determine the wake verb from a queued message JSON.

    Order matters: clear first (so the sentinel takes precedence), then
    senderless prompt, then alias, then plain message."""
    if msg.get("clear") is True:
        return "clear"
    sender = (msg.get("from") or "").strip()
    if not sender:
        return "prompt"
    if (msg.get("alias") or "").strip():
        return "messageAlias"
    return "message"


def build_prompt(msg: dict, definition: dict, verb: str) -> str:
    """Format a queued message into the prompt string for the agent CLI.

    `verb` selects how the body is built:
      - `prompt`        — raw `content` delivery (no template wrapping)
      - `message`       — `promptMessage` template
      - `messageAlias`  — `promptMessageAlias` template (alias + others_count)
      - `clear`         — invokeClear takes no prompt; returns empty string
    """
    if verb == "clear":
        return ""
    content = msg.get("content", "")
    if verb == "prompt":
        return "\n".join([content, *_file_lines(msg)])

    sender = (msg.get("from") or "").strip()
    date = msg.get("date", "")
    recipient = (msg.get("to") or "").strip()
    alias = (msg.get("alias") or "").strip()
    others_count = msg.get("others_count", 0)
    if verb == "messageAlias":
        tmpl = definition.get("promptMessageAlias") or (
            "{sender} tells you ({recipient}) and {others_count} others on the {alias} alias: {message}"
        )
    else:  # "message"
        tmpl = definition.get("promptMessage") or "{sender} tells you ({recipient}): {message}"
    header = tmpl.format(
        sender=sender,
        recipient=recipient,
        message=content,
        date=date,
        alias=alias,
        others_count=others_count,
    )
    if date and "{date}" not in tmpl:
        header = f"[{date}] {header}"
    return "\n".join([header, *_file_lines(msg)])


def _expand_argv(argv: list[str], prompt: str) -> list[str]:
    """Expand placeholders in argv:
      - `$PROMPT`   the wake prompt (raw or template-formatted)
      - `$A8S_DIR`  the apps/a8s/ directory (so default.json can reference
                    bundled scripts like dummy-cli without hardcoding paths)
    invokeClear ignores prompt entirely; its argv should not contain $PROMPT."""
    a8s_dir = str(SCRIPT_DIR)
    return [a.replace("$PROMPT", prompt).replace("$A8S_DIR", a8s_dir) for a in argv]


def build_command(definition: dict, prompt: str, verb: str) -> list[str]:
    """Pick the `invoke*` argv from `definition` for this verb and expand $PROMPT.

    Verbs:
      prompt        invokePrompt        senderless supervisor-direct (raw)
      message       invokeMessage       direct tell from one agent to another
      messageAlias  invokeMessageAlias  alias-routed tell (with opacity vars)
      clear         invokeClear         start a fresh conversation (no prompt)
    """
    key = VERB_KEY.get(verb)
    if key is None:
        raise ValueError(f"unknown verb: {verb!r}")
    argv = definition.get(key)
    if not argv:
        raise ValueError(f"definition missing {key!r}")
    return _expand_argv(list(argv), prompt)


def _autodiscover_definition(root: Path) -> tuple[str, str]:
    """Look for marker files (CLAUDE.md/GEMINI.md/CODEX.md) directly in `root`
    and pick the matching built-in definition. Always returns a usable path:
    falls back to `default.json` (the dummy fallback) if no single marker
    matches. Returns (definition_path, note)."""
    found: list[tuple[str, str]] = []
    for marker_name, kind in MARKER_FILES.items():
        if (root / marker_name).is_file():
            found.append((marker_name, kind))
    marker_names = [m for m, _ in found]
    default_fallback = str(default_definition_path("default"))
    if len(found) == 1:
        kind = found[0][1]
        path = default_definition_path(kind)
        if path.is_file():
            return str(path), f"auto-detected via {marker_names[0]}"
        return default_fallback, f"marker {marker_names[0]} found but {path} missing — using default fallback"
    if len(found) > 1:
        return default_fallback, f"multiple markers ({', '.join(marker_names)}) — using default fallback; re-add with explicit definition to pick one"
    return default_fallback, "no marker file — using default fallback (run `a8s define` to wire a real CLI)"
