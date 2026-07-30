from __future__ import annotations

import re
from typing import NamedTuple

DEFAULT_VIEW_LIMIT = 10

HEADING_OUT = "## from {from} to {to} at {timestamp}"
HEADING_IN = "### from {from} to {to} at {timestamp}"

# Crockford base32 ULID (26 chars); h4l message ids are ULIDs.
_MSG_ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$", re.IGNORECASE)


class ViewArgs(NamedTuple):
    slug: str
    limit: int = DEFAULT_VIEW_LIMIT
    start_n: int | None = None
    msg_id: str | None = None


def is_message_id(token: str) -> bool:
    return bool(_MSG_ID_RE.match(token.strip()))


def _attachment_lines(entry: dict) -> list[str]:
    files = entry.get("files") or []
    if not isinstance(files, list):
        return []
    lines: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        name = (item.get("filename") or "").strip()
        if name:
            lines.append(f"- attachment: {name}")
    return lines


def _format_heading(template: str, entry: dict, room: str) -> str:
    ts = (entry.get("date") or "").strip()
    return template.format(
        **{
            "from": entry.get("from", ""),
            "to": f"#{room}",
            "timestamp": ts,
            "date": ts,
        }
    )


def _format_entry(entry: dict, room: str, viewer_key: str) -> str:
    sent = (entry.get("from") or "").strip().lower() == viewer_key
    heading = _format_heading(
        HEADING_OUT if sent else HEADING_IN,
        entry,
        room,
    )
    content = entry.get("content", "")
    file_lines = _attachment_lines(entry)
    body_parts: list[str] = []
    if content:
        body_parts.append(content)
    body_parts.extend(file_lines)
    if body_parts:
        return f"{heading}\n\n" + "\n".join(body_parts)
    return heading


def select_messages(
    messages: list[dict],
    *,
    limit: int,
    start_n: int | None = None,
) -> tuple[list[dict], int, int]:
    """Return a chronological window, total count, and 0-based start index."""
    total = len(messages)
    if limit < 1:
        return [], total, 0
    if start_n is not None:
        if start_n < 1:
            raise ValueError("--start must be at least 1")
        idx = start_n - 1
        if idx >= total:
            return [], total, idx
        return messages[idx : idx + limit], total, idx
    if total <= limit:
        return list(messages), total, 0
    start = total - limit
    return messages[-limit:], total, start


def _format_view_footer(
    room: str,
    *,
    start_n: int,
    end_n: int,
    total: int,
    limit: int,
    node: str,
) -> str:
    lines = [
        "---",
        f"#{room}: viewed messages {start_n}–{end_n} of {total} (limit {limit}).",
    ]
    if start_n > 1:
        older_start = max(1, start_n - limit)
        lines.append(
            f'Older: tell {node} "/view {room} --start {older_start} --limit {limit}"'
        )
    if end_n < total:
        newer_start = end_n + 1
        lines.append(
            f'Newer: tell {node} "/view {room} --start {newer_start} --limit {limit}"'
        )
        lines.append(f'Latest: tell {node} "/view {room}"')
    lines.append(
        f'Window: tell {node} "/view {room} --start <n> --limit <m>" '
        f"(or tell {node} \"/view {room} <start> <limit>\")"
    )
    return "\n".join(lines)


def format_message_view(
    room: str,
    entry: dict,
    viewer: str,
    *,
    node: str | None = None,
) -> str:
    """Full single-message transcript (no notify truncation)."""
    viewer_key = (viewer or "").strip().lower()
    block = _format_entry(entry, room, viewer_key)
    if node:
        msg_id = (entry.get("id") or "").strip()
        block += f"\n\n---\n#{room}: message {msg_id}"
    return block


def format_room_view(
    room: str,
    messages: list[dict],
    viewer: str,
    *,
    limit: int = DEFAULT_VIEW_LIMIT,
    start_n: int | None = None,
    node: str | None = None,
) -> str:
    """Markdown transcript for a chat room, matching a8s convo heading style."""
    window, total, start_idx = select_messages(
        messages,
        limit=limit,
        start_n=start_n,
    )
    if total == 0:
        header = f"#{room}: no messages"
        if node:
            header += f'\n\ntell {node} "#{room} <message>"'
        return header

    viewer_key = (viewer or "").strip().lower()
    parts: list[str] = [_format_entry(entry, room, viewer_key) for entry in window]

    if node:
        if window:
            view_start = start_idx + 1
            view_end = start_idx + len(window)
        else:
            view_start = min((start_n or 1), total + 1)
            view_end = view_start - 1
        parts.append(
            _format_view_footer(
                room,
                start_n=view_start,
                end_n=view_end,
                total=total,
                limit=limit,
                node=node,
            )
        )

    return "\n\n".join(parts)


def parse_view_args(args: list[str]) -> ViewArgs:
    """Parse `/view <room> [<id> | [start] limit] [--id ID] [--start N] [--limit N]`."""
    if not args:
        raise ValueError("/view requires <room>")
    from rooms import normalize_slug

    slug = normalize_slug(args[0])
    limit = DEFAULT_VIEW_LIMIT
    start_n: int | None = None
    msg_id: str | None = None
    i = 1
    if i < len(args) and not args[i].startswith("-"):
        token = args[i]
        if token.isdigit():
            if i + 1 < len(args) and args[i + 1].isdigit():
                start_n = int(token)
                limit = int(args[i + 1])
                i += 2
            else:
                limit = int(token)
                i += 1
        elif is_message_id(token):
            msg_id = token.strip().upper()
            i += 1
        else:
            raise ValueError(f"unknown /view argument: {token}")
    while i < len(args):
        token = args[i]
        if token == "--limit":
            if i + 1 >= len(args):
                raise ValueError("--limit requires a number")
            try:
                limit = int(args[i + 1])
            except ValueError as exc:
                raise ValueError("--limit requires a number") from exc
            if limit < 1:
                raise ValueError("--limit must be at least 1")
            i += 2
            continue
        if token == "--start":
            if i + 1 >= len(args):
                raise ValueError("--start requires a number")
            try:
                start_n = int(args[i + 1])
            except ValueError as exc:
                raise ValueError("--start requires a number") from exc
            if start_n < 1:
                raise ValueError("--start must be at least 1")
            i += 2
            continue
        if token == "--id":
            if i + 1 >= len(args):
                raise ValueError("--id requires a message id")
            raw = args[i + 1].strip()
            if not is_message_id(raw):
                raise ValueError(f"invalid message id: {raw}")
            msg_id = raw.upper()
            i += 2
            continue
        raise ValueError(f"unknown /view argument: {token}")
    if limit < 1:
        raise ValueError("--limit must be at least 1")
    if msg_id is not None and start_n is not None:
        raise ValueError("/view <id> cannot be combined with --start / window args")
    return ViewArgs(slug=slug, limit=limit, start_n=start_n, msg_id=msg_id)
