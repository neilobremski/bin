from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from typing import TypeAlias

TellFn: TypeAlias = Callable[[str, str], None]

MAX_NOTIFY_CHARS = 1000
SIMULATE_ENV = "H4L_SIMULATE_TELL"


def default_tell(agent: str, body: str) -> None:
    subprocess.run(
        ["tell", agent, body],
        check=False,
    )


def simulate_tell(agent: str, body: str) -> None:
    print(f"h4l> tell {agent}:", file=sys.stderr)
    for line in body.splitlines():
        print(f"h4l>   {line}", file=sys.stderr)


def noop_tell(_agent: str, _body: str) -> None:
    return None


def resolve_tell_fn(*, notify: bool, simulate: bool) -> TellFn:
    if simulate:
        return simulate_tell
    if notify:
        return default_tell
    return noop_tell


def simulate_enabled(flag: bool) -> bool:
    if flag:
        return True
    raw = os.environ.get(SIMULATE_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def truncate(text: str, limit: int = MAX_NOTIFY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def usage_help(node: str) -> str:
    lines = [
        "Post (IRC style):",
        f'tell {node} "#<room> <message>"',
        "",
        "Commands:",
        f'tell {node} "/join <room>"',
        f'tell {node} "/part <room>"  (/leave)',
        f'tell {node} "/invite <room> <agent> [<agent>...]"',
        f'tell {node} "/list"',
        f'tell {node} "/names <room>"  (/members)',
        f'tell {node} "/view <room> [[start] limit] [--start N] [--limit N] [--before <id>]"',
        f'tell {node} "/help"',
        "",
        "Also: /post, /leave, /members; # prefix optional on room names.",
    ]
    return "\n".join(lines)


def command_hint(node: str, slash_cmd: str) -> str:
    return f'tell {node} "{slash_cmd}"'


def footer(node: str, room: str | None = None) -> str:
    room_token = room or "<room>"
    return (
        f"\n---\n"
        f"tell {node} /view {room_token}\n"
        f"tell {node} /leave {room_token}\n"
        f"tell {node} /list"
    )


def notify_members(
    *,
    tell_fn: TellFn,
    node: str,
    members: list[str],
    poster: str,
    room: str,
    headline: str,
    body: str,
    skip: set[str] | None = None,
) -> None:
    omitted = {m.lower() for m in (skip or set())}
    text = truncate(body)
    message = f"{headline}\n\n{text}{footer(node, room)}"
    for member in members:
        if member.lower() in omitted:
            continue
        tell_fn(member, message)


def ack(tell_fn: TellFn, sender: str, text: str) -> None:
    print(text)
    tell_fn(sender, text)


def error(
    tell_fn: TellFn,
    sender: str,
    node: str,
    detail: str,
    *,
    show_commands: bool = False,
    hint: str | None = None,
) -> None:
    parts = [f"Error: {detail}"]
    if hint:
        parts.append(command_hint(node, hint))
    if show_commands:
        parts.append("")
        parts.append(usage_help(node))
    text = "\n".join(parts)
    print(text, file=sys.stderr)
    tell_fn(sender, text)
