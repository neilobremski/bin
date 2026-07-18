"""Conversation archive — append-only jsonl of routed messages for `a8s convo`.

One record per logical message (alias fan-out stores the alias in `to` and
lists local deliverees in `recipients`). Rotates to `convo_max_limit` entries
from `~/.a8s/settings.json` (default 1000).
"""
from __future__ import annotations

import json
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
    "HEADING_PLACEHOLDERS",
    "convo_help_epilog",
    "decode_template",
    "entry_from_message",
    "extract_heading_templates",
    "format_conversation",
    "format_entry",
    "follow_conversation",
    "involves_agent",
    "load_entries",
    "open_glow_stdout",
    "print_entries",
    "record",
    "write_block",
]

DEFAULT_HEADING_OUT = "## from {from} to {to} at {timestamp}"
DEFAULT_HEADING_IN = "### from {from} to {to} at {timestamp}"

HEADING_PLACEHOLDERS = ("from", "to", "timestamp", "date")


def decode_template(text: str) -> str:
    return text.replace("\\n", "\n").replace("\\t", "\t")


def _argv_looks_like_option(arg: str) -> bool:
    return arg.startswith("-") and arg != "-"


def extract_heading_templates(argv: list[str]) -> tuple[list[str], str | None, str | None]:
    """Pull --heading-out/in (multi-token) out of argv before argparse."""
    rest: list[str] = []
    heading_out: str | None = None
    heading_in: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--heading-out":
            if i + 1 >= len(argv):
                raise ValueError("--heading-out requires a template")
            heading_out, i = _consume_template(argv, i + 1)
            continue
        if arg == "--heading-in":
            if i + 1 >= len(argv):
                raise ValueError("--heading-in requires a template")
            heading_in, i = _consume_template(argv, i + 1)
            continue
        rest.append(arg)
        i += 1
    return rest, heading_out, heading_in


def _consume_template(argv: list[str], start: int) -> tuple[str, int]:
    parts: list[str] = []
    i = start
    while i < len(argv) and not _argv_looks_like_option(argv[i]):
        parts.append(argv[i])
        i += 1
    if not parts:
        raise ValueError("template requires at least one line")
    return decode_template("\n".join(parts)), i


def convo_help_epilog() -> str:
    return f"""heading templates:
  Outbound (--heading-out) and inbound (--heading-in) use Python str.format placeholders:
    {{from}}       sender name
    {{to}}         recipient or alias
    {{timestamp}}  ISO UTC timestamp from the message
    {{date}}       alias for {{timestamp}}

  Defaults:
    outbound: {DEFAULT_HEADING_OUT}
    inbound:  {DEFAULT_HEADING_IN}

  Multiline headings:
    - Shell quotes preserve embedded newlines in one argument
    - Multiple arguments after the flag join with newlines (one line each)
    - Use \\n and \\t escapes inside a single argument

  Message body and attachment lines are appended after the heading block.

examples:
  a8s convo neil-macbook -f --limit 10 --glow
  a8s convo bob --heading-out '**{{from}}**' '→ {{to}}' --limit 5
  a8s convo bob --heading-in "### {{from}}\\n_{{timestamp}}_"

environment:
  A8S_GLOW=<theme>    default glow theme (auto, dark, light, dracula, …); --glow overrides
"""


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


def entry_from_message(msg: dict[str, Any], *, recipients: list[str] | None = None) -> dict[str, Any]:
    """Normalize a tell/inbox envelope into a conversation archive entry."""
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
        "recipients": list(recipients or []),
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
    entry = entry_from_message(msg, recipients=recipients)
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


def open_glow_stdout(theme: str = "auto"):
    from glow_util import open_glow_stdout as _open

    return _open(theme)


def write_block(block: str, glow_stream: object | None) -> None:
    if not block:
        return
    if glow_stream is not None:
        glow_stream.write(block + "\n\n")
        return
    print(block, flush=True)
    print(flush=True)


def print_entries(
    agent: str,
    entries: list[dict[str, Any]],
    *,
    glow_stream: object | None = None,
    heading_out: str = DEFAULT_HEADING_OUT,
    heading_in: str = DEFAULT_HEADING_IN,
) -> None:
    for entry in entries:
        block = format_entry(agent, entry, heading_out=heading_out, heading_in=heading_in)
        write_block(block, glow_stream)


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
    glow_theme: str | None = None,
) -> None:
    """Print the last `limit` messages, then block printing new archive rows."""
    glow_stream = None
    if glow_theme is not None:
        try:
            glow_stream = open_glow_stdout(glow_theme)
        except FileNotFoundError:
            print("a8s convo: glow not found on PATH", file=sys.stderr)

    seen: set[str] = set()
    try:
        rows = [e for e in load_entries() if involves_agent(e, agent)]
        print_entries(
            agent,
            rows[-limit:],
            glow_stream=glow_stream,
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
                        glow_stream=glow_stream,
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
    finally:
        if glow_stream is not None:
            glow_stream.close()

