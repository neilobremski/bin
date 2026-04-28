"""a8s definitions — invoke* verbs and argv interpolation.

Each agent has a definition JSON (built-in or custom) that encodes argv for
three verbs. `select_verb` picks the verb from the queued message;
`build_command` substitutes `$SENDER` / `$RECIPIENT` / `$MESSAGE` / `$A8S_DIR`
into the chosen argv.

Strict opacity (issues #69, #70): the recipient sees only sender + message
content — no `alias` or `others_count` leak. A direct tell and an
alias-fanned tell produce the same prompt shape, distinguished only by what
`$RECIPIENT` resolves to (the original `to` field, which is the alias name
for fanned messages and the agent name for direct ones — same as a public
mailing list: you know it came via the list, you don't know who else got it).
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
    "clear": "invokeClear",
}


def default_definition_path(kind: str) -> Path:
    return DEFINITIONS_DIR / f"{kind}.json"


def load_definition(name: str) -> dict:
    """Load the JSON definition for `name`. Every agent always has one — if
    the registry lacks an explicit `definition` field, falls back to the
    bundled `apps/a8s/definitions/default.json` (a dummy CLI that prints
    'not configured' and the received prompt).

    Definitions encode argv with `$SENDER`, `$RECIPIENT`, `$MESSAGE`, and
    `$A8S_DIR` placeholders. See apps/a8s/definitions/*.json.
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
    senderless prompt, then plain message. Alias-routed messages take
    `message` like direct ones — strict opacity collapses the dispatch."""
    if msg.get("clear") is True:
        return "clear"
    sender = (msg.get("from") or "").strip()
    if not sender:
        return "prompt"
    return "message"


def _message_body(msg: dict) -> str:
    """Compose the `$MESSAGE` body: content plus any FILE: lines."""
    content = msg.get("content", "")
    lines = _file_lines(msg)
    if not lines:
        return content
    return "\n".join([content, *lines])


def _expand_argv(argv: list[str], sender: str, recipient: str, message: str) -> list[str]:
    """Expand placeholders in argv:
      - `$SENDER`     sender's canonical name (empty for senderless prompts)
      - `$RECIPIENT`  what the sender wrote in `to` (alias for fanned, agent for direct)
      - `$MESSAGE`    content + any FILE: lines
      - `$A8S_DIR`    the apps/a8s/ directory (so default.json can reference
                      bundled scripts like dummy-cli without hardcoding paths)
    """
    a8s_dir = str(SCRIPT_DIR)
    out: list[str] = []
    for a in argv:
        a = a.replace("$SENDER", sender)
        a = a.replace("$RECIPIENT", recipient)
        a = a.replace("$MESSAGE", message)
        a = a.replace("$A8S_DIR", a8s_dir)
        out.append(a)
    return out


def build_command(definition: dict, msg: dict, verb: str) -> list[str]:
    """Pick the `invoke*` argv from `definition` for this verb and expand
    interpolation variables.

    Verbs:
      prompt   invokePrompt   senderless supervisor-direct (raw content)
      message  invokeMessage  routed tell (sender + recipient + content)
      clear    invokeClear    start a fresh conversation (no message)
    """
    key = VERB_KEY.get(verb)
    if key is None:
        raise ValueError(f"unknown verb: {verb!r}")
    argv = definition.get(key)
    if not argv:
        raise ValueError(f"definition missing {key!r}")
    if verb == "clear":
        return _expand_argv(list(argv), "", "", "")
    sender = (msg.get("from") or "").strip()
    recipient = (msg.get("to") or "").strip()
    body = _message_body(msg)
    return _expand_argv(list(argv), sender, recipient, body)


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
