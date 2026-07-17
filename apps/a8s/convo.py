"""Conversation archive — append-only jsonl of routed messages for `a8s convo`.

One record per logical message (alias fan-out stores the alias in `to` and
lists local deliverees in `recipients`). Rotates to `convo_max_limit` entries
from `~/.a8s/settings.json` (default 1000).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import conversations_path, inbound_bundle_dir
from settings import get_int

__all__ = [
    "DEFAULT_HEADING_IN",
    "DEFAULT_HEADING_OUT",
    "emit_block",
    "format_conversation",
    "format_entry",
    "follow_conversation",
    "involves_agent",
    "load_entries",
    "print_entries",
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
    """Append one logical message when delivery completes (local inbox, remote
    receive, or outbound remote publish). `recipients` lists local deliverees
    for routed/RECEIVED_REMOTE rows, or the logical `to` name for outbound
    remote-only sends."""
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


def _attachment_lines(agent: str, entry: dict[str, Any]) -> list[str]:
    names = [str(name).strip() for name in (entry.get("files") or []) if str(name).strip()]
    if not names:
        return []
    msg_id = (entry.get("id") or "").strip()
    bundle_root: Path | None = None
    if msg_id:
        from registry import find_participant, participants_from_registry

        participant = find_participant(participants_from_registry(), agent)
        if participant is not None:
            bundle_root = inbound_bundle_dir(participant.files_path(), msg_id)
    lines: list[str] = []
    for name in names:
        if bundle_root is not None:
            path = bundle_root / name
            if path.is_file():
                lines.append(f"- attachment: {path}")
                continue
        lines.append(f"- attachment: {name}")
    return lines


def format_entry(
    agent: str,
    entry: dict[str, Any],
    *,
    heading_out: str = DEFAULT_HEADING_OUT,
    heading_in: str = DEFAULT_HEADING_IN,
) -> str:
    agent_key = _name_key(agent)
    sent = _name_key(entry.get("from", "")) == agent_key
    heading = _format_heading(heading_out if sent else heading_in, entry)
    content = entry.get("content", "")
    block = heading
    if content:
        block = f"{heading}\n\n{content}"
    file_lines = _attachment_lines(agent, entry)
    if file_lines:
        joined = "\n".join(file_lines)
        block = f"{block}\n\n{joined}" if block else joined
    return block


def emit_block(block: str, *, glow: bool = False) -> None:
    """Print one message block; with glow, pipe this block alone to `glow -`."""
    if not block:
        return
    if glow:
        glow_bin = shutil.which("glow")
        if glow_bin:
            proc = subprocess.run(
                [glow_bin, "-"],
                input=block + "\n",
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                sys.stdout.write(proc.stdout)
                if not proc.stdout.endswith("\n"):
                    sys.stdout.write("\n")
                sys.stdout.flush()
                return
    print(block, flush=True)


def print_entries(
    agent: str,
    entries: list[dict[str, Any]],
    *,
    glow: bool = False,
    heading_out: str = DEFAULT_HEADING_OUT,
    heading_in: str = DEFAULT_HEADING_IN,
) -> None:
    for entry in entries:
        block = format_entry(agent, entry, heading_out=heading_out, heading_in=heading_in)
        if not block:
            continue
        emit_block(block, glow=glow)
        print(flush=True)


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
    parts = [
        format_entry(agent, entry, heading_out=heading_out, heading_in=heading_in)
        for entry in rows
    ]
    return "\n\n".join(parts)


def _remember_entry_id(seen: set[str], entry: dict[str, Any]) -> None:
    msg_id = (entry.get("id") or "").strip()
    if msg_id:
        seen.add(msg_id)


def _parse_convo_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def follow_conversation(
    agent: str,
    *,
    limit: int = 10,
    heading_out: str = DEFAULT_HEADING_OUT,
    heading_in: str = DEFAULT_HEADING_IN,
    poll_interval: float = 1.0,
    glow: bool = False,
) -> None:
    """Print the last `limit` messages, then block printing new archive rows."""
    seen: set[str] = set()
    rows = [e for e in load_entries() if involves_agent(e, agent)]
    print_entries(
        agent,
        rows[-limit:],
        glow=glow,
        heading_out=heading_out,
        heading_in=heading_in,
    )
    for entry in rows[-limit:]:
        _remember_entry_id(seen, entry)

    path = conversations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    with path.open("r", encoding="utf-8") as handle:
        handle.seek(0, 2)
        while True:
            line = handle.readline()
            if line:
                entry = _parse_convo_line(line)
                if entry is None or not involves_agent(entry, agent):
                    continue
                msg_id = (entry.get("id") or "").strip()
                if msg_id and msg_id in seen:
                    continue
                print_entries(
                    agent,
                    [entry],
                    glow=glow,
                    heading_out=heading_out,
                    heading_in=heading_in,
                )
                _remember_entry_id(seen, entry)
                continue

            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if handle.tell() > size:
                handle.seek(0)
            time.sleep(poll_interval)

