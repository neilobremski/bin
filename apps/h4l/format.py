from __future__ import annotations

DEFAULT_VIEW_LIMIT = 10

HEADING_OUT = "## from {from} to {to} at {timestamp}"
HEADING_IN = "### from {from} to {to} at {timestamp}"


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
    parts: list[str] = []

    for entry in window:
        sent = (entry.get("from") or "").strip().lower() == viewer_key
        heading = _format_heading(
            HEADING_OUT if sent else HEADING_IN,
            entry,
            room,
        )
        content = entry.get("content", "")
        block = heading
        if content:
            block = f"{heading}\n\n{content}"
        parts.append(block)

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


def parse_view_args(args: list[str]) -> tuple[str, int, int | None]:
    """Parse `/view <room> [[start] limit] [--start N] [--limit N]`."""
    if not args:
        raise ValueError("/view requires <room>")
    from rooms import normalize_slug

    slug = normalize_slug(args[0])
    limit = DEFAULT_VIEW_LIMIT
    start_n: int | None = None
    i = 1
    if i < len(args) and args[i].isdigit():
        if i + 1 < len(args) and args[i + 1].isdigit():
            start_n = int(args[i])
            limit = int(args[i + 1])
            i += 2
        else:
            limit = int(args[i])
            i += 1
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
        raise ValueError(f"unknown /view argument: {token}")
    if limit < 1:
        raise ValueError("--limit must be at least 1")
    return slug, limit, start_n
