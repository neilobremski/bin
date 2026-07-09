from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from rooms import RoomStore

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
        f'tell {node} "/view <room> [[start] limit] [--start N] [--limit N]"',
        f'tell {node} "/help"',
        "",
        "Also: /post, /leave, /members; # prefix optional on room names.",
    ]
    return "\n".join(lines)


def command_hint(node: str, slash_cmd: str) -> str:
    return f'tell {node} "{slash_cmd}"'


def onboard_footer(node: str, room: str) -> str:
    return (
        f"\n---\n"
        f"Post a message: tell {node} #{room} <message>\n"
        f"More commands: tell {node} /help"
    )


def _onboard_suffix(store: RoomStore, meta: dict, agent: str, node: str, room: str) -> tuple[str, dict, bool]:
    if store.has_seen_help(meta, agent):
        return "", meta, False
    meta = store.mark_help_seen(meta, agent)
    return onboard_footer(node, room), meta, True


def notify_members(
    *,
    store: RoomStore,
    slug: str,
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
    meta = store.load_meta(slug)
    dirty = False
    for member in members:
        if member.lower() in omitted:
            continue
        suffix, meta, changed = _onboard_suffix(store, meta, member, node, room)
        dirty = dirty or changed
        message = f"{headline}\n\n{text}{suffix}"
        tell_fn(member, message)
    if dirty:
        store.save_meta(slug, meta)


def notify_agent(
    *,
    store: RoomStore,
    slug: str,
    tell_fn: TellFn,
    node: str,
    agent: str,
    body: str,
) -> None:
    meta = store.load_meta(slug)
    suffix, meta, dirty = _onboard_suffix(store, meta, agent, node, slug)
    if dirty:
        store.save_meta(slug, meta)
    tell_fn(agent, f"{body}{suffix}")


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
