from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from typing import TypeAlias

TellFn: TypeAlias = Callable[[str, str], None]

MAX_NOTIFY_CHARS = 1000


def default_tell(agent: str, body: str) -> None:
    subprocess.run(
        ["tell", agent, body],
        check=False,
    )


def truncate(text: str, limit: int = MAX_NOTIFY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


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


def error(tell_fn: TellFn, sender: str, text: str) -> None:
    print(text, file=sys.stderr)
    tell_fn(sender, f"h4l error: {text}")
