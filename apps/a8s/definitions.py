"""a8s definitions — single `invoke` verb and argv interpolation.

Each agent has a definition JSON (built-in or custom) that encodes one argv
under the `invoke` key. `build_command` substitutes `$SENDER` / `$RECIPIENT`
/ `$MESSAGE` / `$TIMESTAMP` / `$AGE` / `$A8S_DIR` into it.

Strict opacity (issues #69, #70): the recipient sees only sender + message
content — no `alias` or `others_count` leak. A direct tell and an
alias-fanned tell produce the same prompt shape, distinguished only by what
`$RECIPIENT` resolves to (the original `to` field, which is the alias name
for fanned messages and the agent name for direct ones — same as a public
mailing list: you know it came via the list, you don't know who else got it).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core import (
    DEFINITIONS_DIR,
    MARKER_FILES,
    SCRIPT_DIR,
)
from registry import load_registry

ATTACHED_FILE_PREFIX = "ATTACHED FILE: "


def default_definition_path(kind: str) -> Path:
    return DEFINITIONS_DIR / f"{kind}.json"


def is_file_proxy(definition: dict) -> bool:
    return definition.get("proxy") == "file"


def files_ttl_seconds(definition: dict) -> float:
    hours = definition.get("files_ttl_hours", 48)
    try:
        h = float(hours)
    except (TypeError, ValueError):
        h = 48.0
    return max(0.0, h * 3600)


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
    msg_id = (msg.get("id") or "").strip()
    if not msg_id:
        return []
    out = [""]
    for entry in files:
        if (entry.get("path") or "").strip():
            continue
        filename = (entry.get("filename") or "").strip()
        if not filename:
            continue
        out.append(f"{ATTACHED_FILE_PREFIX}./.files/{msg_id}/{filename}")
    return out


def _message_body(msg: dict) -> str:
    """Compose the `$MESSAGE` body: content plus any ATTACHED FILE: lines."""
    content = msg.get("content", "")
    lines = _file_lines(msg)
    if not lines:
        return content
    return "\n".join([content, *lines])


def _parse_iso(date_str: str) -> datetime | None:
    """Parse the ISO timestamp we write into messages (`...Z` UTC). Returns
    None for empty / unparseable input."""
    if not date_str:
        return None
    try:
        if date_str.endswith("Z"):
            return datetime.fromisoformat(date_str[:-1] + "+00:00")
        return datetime.fromisoformat(date_str)
    except ValueError:
        return None


def _format_age(date_str: str, *, now: datetime | None = None) -> str:
    """Convert an ISO timestamp into a human-readable 'N units ago' string.
    Empty for missing/unparseable input. `now` is injectable for tests."""
    ts = _parse_iso(date_str)
    if ts is None:
        return ""
    if now is None:
        now = datetime.now(timezone.utc)
    seconds = max(0, int((now - ts).total_seconds()))
    if seconds < 60:
        n, unit = seconds, "second"
    elif seconds < 3600:
        n, unit = seconds // 60, "minute"
    elif seconds < 86400:
        n, unit = seconds // 3600, "hour"
    elif seconds < 7 * 86400:
        n, unit = seconds // 86400, "day"
    else:
        n, unit = seconds // (7 * 86400), "week"
    plural = "" if n == 1 else "s"
    return f"{n} {unit}{plural} ago"


def _expand_argv(
    argv: list[str],
    sender: str,
    recipient: str,
    message: str,
    timestamp: str = "",
    age: str = "",
) -> list[str]:
    """Expand placeholders in argv:
      - `$SENDER`     sender's canonical name (empty for senderless prompts)
      - `$RECIPIENT`  what the sender wrote in `to` (alias for fanned, agent for direct)
      - `$MESSAGE`    content + any ATTACHED FILE: lines
      - `$TIMESTAMP`  ISO 8601 UTC time the message was queued (e.g.,
                      `2026-04-28T14:30:00.123456Z`); empty for invokeClear
                      and for messages without a `date` field
      - `$AGE`        human-readable age relative to now (e.g.,
                      `5 minutes ago`); same emptiness rules as $TIMESTAMP
      - `$A8S_DIR`    the apps/a8s/ directory (so default.json can reference
                      bundled scripts like dummy-cli without hardcoding paths)
    """
    a8s_dir = str(SCRIPT_DIR)
    out: list[str] = []
    for a in argv:
        a = a.replace("$SENDER", sender)
        a = a.replace("$RECIPIENT", recipient)
        a = a.replace("$MESSAGE", message)
        a = a.replace("$TIMESTAMP", timestamp)
        a = a.replace("$AGE", age)
        a = a.replace("$A8S_DIR", a8s_dir)
        out.append(a)
    return out


def build_command(definition: dict, msg: dict) -> list[str]:
    """Pick the `invoke` argv from `definition` and expand interpolation
    variables. There is one verb — every routed message is a `tell` — so
    no dispatch table is needed.

    `$TIMESTAMP` and `$AGE` come from `msg["date"]`; both fall back to
    empty for messages that somehow lack a date field (defensive — every
    `_write_outbox` stamps one)."""
    argv = definition.get("invoke")
    if not argv:
        raise ValueError("definition missing 'invoke'")
    sender = (msg.get("from") or "").strip()
    recipient = (msg.get("to") or "").strip()
    body = _message_body(msg)
    date_str = (msg.get("date") or "").strip()
    age = _format_age(date_str)
    return _expand_argv(list(argv), sender, recipient, body, date_str, age)


def build_idle_command(definition: dict, agent_name: str) -> list[str] | None:
    """Pick the `idle.invoke` argv from `definition` and expand the same
    interpolation variables `build_command` does. Returns None if the agent
    has no idle config, or if its `invoke` argv is missing/empty.

    Idle invocations have no incoming message, so $SENDER, $MESSAGE,
    $TIMESTAMP, and $AGE expand to empty strings. $RECIPIENT is set to the
    agent's own name so a definition like `["claude", "--continue", "-p",
    "$RECIPIENT idle wake"]` reads naturally."""
    idle = definition.get("idle")
    if not isinstance(idle, dict):
        return None
    argv = idle.get("invoke")
    if not argv:
        return None
    return _expand_argv(list(argv), "", agent_name, "", "", "")


def pause_seconds(definition: dict) -> float:
    raw = definition.get("pause")
    if raw is None:
        return 0.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def has_batch_invoke(definition: dict) -> bool:
    batch = definition.get("batch")
    if not isinstance(batch, dict):
        return False
    return bool(batch.get("invoke"))


def batch_limit(definition: dict) -> int:
    batch = definition.get("batch")
    if not isinstance(batch, dict):
        return 5
    raw = batch.get("limit", 5)
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 5
    return max(1, v)


def build_batch_command(
    definition: dict, agent_name: str, msg_paths: list[Path]
) -> list[str]:
    """Expand `batch.invoke` like idle (no incoming message) and append each
    message file path as a trailing argv element."""
    batch = definition.get("batch")
    if not isinstance(batch, dict):
        raise ValueError("definition missing 'batch'")
    argv = batch.get("invoke")
    if not argv:
        raise ValueError("definition missing 'batch.invoke'")
    cmd = _expand_argv(list(argv), "", agent_name, "", "", "")
    cmd.extend(str(p.resolve()) for p in msg_paths)
    return cmd


def idle_timeout_seconds(definition: dict) -> float | None:
    """Returns `definition.idle.timeout` as a positive float, or None if
    not configured / not a positive number. Loose typing tolerated:
    `"60"` strings parse the same as `60`."""
    idle = definition.get("idle")
    if not isinstance(idle, dict):
        return None
    raw = idle.get("timeout")
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


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
