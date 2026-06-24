"""Conversation archive — append-only jsonl of routed messages for `a8s convo`.

One record per logical message (alias fan-out stores the alias in `to` and
lists local deliverees in `recipients`). Rotates to `convo_max_limit` entries
from `~/.a8s/settings.json` (default 1000).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core import conversations_path
from settings import get_int

__all__ = [
    "DEFAULT_HEADING_IN",
    "DEFAULT_HEADING_OUT",
    "format_conversation",
    "involves_agent",
    "load_entries",
    "record",
]

DEFAULT_HEADING_OUT = "## from {from} to {to} at {timestamp}"
DEFAULT_HEADING_IN = "### from {from} to {to} at {timestamp}"


def _max_limit() -> int:
    return get_int("convo_max_limit")


def _name_key(name: str) -> str:
    return (name or "").strip().lower()


def involves_agent(entry: dict[str, Any], agent: str) -> bool:
    key = _name_key(agent)
    if not key:
        return False
    if _name_key(entry.get("from", "")) == key:
        return True
    if _name_key(entry.get("to", "")) == key:
        return True
    recipients = entry.get("recipients") or []
    return any(_name_key(r) == key for r in recipients)


def _entry_from_message(msg: dict[str, Any], *, recipients: list[str]) -> dict[str, Any]:
    files = msg.get("files") or []
    filenames = [
        (e.get("filename") or "").strip()
        for e in files
        if isinstance(e, dict) and (e.get("filename") or "").strip()
    ]
    return {
        "date": (msg.get("date") or "").strip() or _now_iso(),
        "from": (msg.get("from") or "").strip(),
        "to": (msg.get("to") or "").strip(),
        "content": msg.get("content", ""),
        "files": filenames,
        "id": (msg.get("id") or "").strip(),
        "recipients": list(recipients),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load_entries() -> list[dict[str, Any]]:
    path = conversations_path()
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return []
    return out


def record(msg: dict[str, Any], *, recipients: list[str]) -> None:
    """Append one logical message if at least one local recipient exists."""
    if not recipients:
        return
    entry = _entry_from_message(msg, recipients=recipients)
    msg_id = entry.get("id") or ""
    try:
        rows = load_entries()
        if msg_id and any(r.get("id") == msg_id for r in rows):
            return
        rows.append(entry)
        cap = _max_limit()
        if len(rows) > cap:
            rows = rows[-cap:]
        path = conversations_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _format_heading(template: str, entry: dict[str, Any]) -> str:
    ts = (entry.get("date") or "").strip()
    return template.format(
        **{
            "from": entry.get("from", ""),
            "to": entry.get("to", ""),
            "timestamp": ts,
            "date": ts,
        }
    )


def format_conversation(
    agent: str,
    *,
    limit: int = 10,
    heading_out: str = DEFAULT_HEADING_OUT,
    heading_in: str = DEFAULT_HEADING_IN,
) -> str:
    """Return markdown for the last `limit` messages involving `agent`."""
    if limit < 1:
        return ""
    rows = [e for e in load_entries() if involves_agent(e, agent)]
    rows = rows[-limit:]
    parts: list[str] = []
    agent_key = _name_key(agent)
    for entry in rows:
        sent = _name_key(entry.get("from", "")) == agent_key
        heading = _format_heading(heading_out if sent else heading_in, entry)
        content = entry.get("content", "")
        block = heading
        if content:
            block = f"{heading}\n\n{content}"
        files = entry.get("files") or []
        if files:
            file_lines = "\n".join(f"- attachment: {name}" for name in files)
            block = f"{block}\n\n{file_lines}" if block else file_lines
        parts.append(block)
    return "\n\n".join(parts)

