from __future__ import annotations

from format import format_room_view, parse_view_args
from notify import TellFn, ack, error, footer, notify_members, usage_help

_CMD_ALIASES = {
    "part": "leave",
    "names": "members",
}
from rooms import RoomStore, normalize_agent, normalize_slug


def _join_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _format_list(store: RoomStore) -> str:
    rooms = store.list_rooms()
    if not rooms:
        return "no chat rooms"
    lines: list[str] = []
    for meta in rooms:
        slug = meta.get("slug", "?")
        members = store.member_names(meta)
        if members:
            lines.append(f"#{slug}: {', '.join(members)}")
        else:
            lines.append(f"#{slug}: (no members)")
    return "\n".join(lines)


def _format_members(store: RoomStore, slug: str) -> str:
    meta = store.load_meta(slug)
    members = store.member_names(meta)
    if not members:
        return f"#{slug}: no members"
    return f"#{slug}: {', '.join(members)}"


def dispatch_slash(
    store: RoomStore,
    *,
    sender: str,
    node: str,
    message: str,
    tell_fn: TellFn,
) -> int:
    sender = normalize_agent(sender)
    body = message.strip()
    if body.startswith("#"):
        return _dispatch_hash_post(store, sender, node, body, tell_fn)
    if not body.startswith("/"):
        error(
            tell_fn,
            sender,
            node,
            "send #<room> <message> or a /command",
            show_commands=True,
        )
        return 1

    parts = body[1:].split()
    if not parts:
        error(
            tell_fn,
            sender,
            node,
            "missing command after /",
            show_commands=True,
        )
        return 1

    cmd = _CMD_ALIASES.get(parts[0].lower(), parts[0].lower())
    args = parts[1:]

    try:
        if cmd == "post":
            return _cmd_post(store, sender, node, args, tell_fn)
        if cmd == "join":
            return _cmd_join(store, sender, node, args, tell_fn)
        if cmd == "leave":
            return _cmd_leave(store, sender, node, args, tell_fn)
        if cmd == "invite":
            return _cmd_invite(store, sender, node, args, tell_fn)
        if cmd == "list":
            return _cmd_list(store, sender, tell_fn)
        if cmd == "view":
            return _cmd_view(store, sender, node, args, tell_fn)
        if cmd == "members":
            return _cmd_members(store, sender, node, args, tell_fn)
        if cmd == "help":
            return _cmd_help(sender, node, tell_fn)
        error(
            tell_fn,
            sender,
            node,
            f"unknown command /{parts[0].lower()}",
            show_commands=True,
        )
        return 1
    except ValueError as exc:
        error(tell_fn, sender, node, str(exc), show_commands=True)
        return 1
    except KeyError:
        slug = args[0] if args else "?"
        error(
            tell_fn,
            sender,
            node,
            f"room not found: {slug}",
            hint="/list",
        )
        return 1


def _dispatch_hash_post(
    store: RoomStore,
    sender: str,
    node: str,
    body: str,
    tell_fn: TellFn,
) -> int:
    parts = body.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        error(
            tell_fn,
            sender,
            node,
            "#<room> requires a message",
            hint="#<room> <message>",
        )
        return 1
    try:
        slug = normalize_slug(parts[0])
    except ValueError as exc:
        error(tell_fn, sender, node, str(exc), hint="#<room> <message>")
        return 1
    return _do_post(store, sender, node, slug, parts[1].strip(), tell_fn)


def _cmd_post(
    store: RoomStore,
    sender: str,
    node: str,
    args: list[str],
    tell_fn: TellFn,
) -> int:
    if len(args) < 2:
        error(
            tell_fn,
            sender,
            node,
            "/post requires <room> and <message>",
            hint="#<room> <message>",
        )
        return 1
    slug = normalize_slug(args[0])
    content = " ".join(args[1:]).strip()
    if not content:
        error(
            tell_fn,
            sender,
            node,
            "/post requires <room> and <message>",
            hint="#<room> <message>",
        )
        return 1
    return _do_post(store, sender, node, slug, content, tell_fn)


def _do_post(
    store: RoomStore,
    sender: str,
    node: str,
    slug: str,
    content: str,
    tell_fn: TellFn,
) -> int:
    meta = store.ensure_room(slug)
    if not store.has_member(meta, sender):
        meta, _ = store.add_member(meta, sender)
    meta = store.touch_activity(slug, meta)
    store.save_meta(slug, meta)

    msg = store.append_message(slug, sender=sender, content=content, kind="post")
    members = store.member_names(meta)
    notify_members(
        tell_fn=tell_fn,
        node=node,
        members=members,
        poster=sender,
        room=slug,
        headline=f"{sender} posted in #{slug}:",
        body=content,
        skip={sender},
    )
    ack(tell_fn, sender, f"posted to #{slug} (id {msg['id']})")
    return 0


def _cmd_join(
    store: RoomStore,
    sender: str,
    node: str,
    args: list[str],
    tell_fn: TellFn,
) -> int:
    if len(args) != 1:
        error(
            tell_fn,
            sender,
            node,
            "/join requires <room>",
            hint="/join <room>",
        )
        return 1
    slug = normalize_slug(args[0])
    meta = store.ensure_room(slug)
    meta, added = store.add_member(meta, sender)
    meta = store.touch_activity(slug, meta)
    store.save_meta(slug, meta)
    text = f"joined #{slug}" if added else f"already in #{slug}"
    ack(tell_fn, sender, text)
    return 0


def _cmd_leave(
    store: RoomStore,
    sender: str,
    node: str,
    args: list[str],
    tell_fn: TellFn,
) -> int:
    if len(args) != 1:
        error(
            tell_fn,
            sender,
            node,
            "/leave requires <room>",
            hint="/leave <room>",
        )
        return 1
    slug = normalize_slug(args[0])
    meta = store.load_meta(slug)
    meta, removed = store.remove_member(meta, sender)
    if removed:
        meta = store.touch_activity(slug, meta)
        store.save_meta(slug, meta)
        ack(tell_fn, sender, f"left #{slug}")
    else:
        ack(tell_fn, sender, f"not a member of #{slug}")
    return 0


def _cmd_invite(
    store: RoomStore,
    sender: str,
    node: str,
    args: list[str],
    tell_fn: TellFn,
) -> int:
    if len(args) < 2:
        error(
            tell_fn,
            sender,
            node,
            "/invite requires <room> and at least one <agent>",
            hint="/invite <room> <agent> [<agent>...]",
        )
        return 1
    slug = normalize_slug(args[0])
    invitees = [normalize_agent(a) for a in args[1:]]
    meta = store.ensure_room(slug)
    added: list[str] = []
    skipped: list[str] = []
    for agent in invitees:
        meta, did_add = store.add_member(meta, agent)
        if did_add:
            added.append(agent)
        else:
            skipped.append(agent)
    meta = store.touch_activity(slug, meta)
    store.save_meta(slug, meta)

    if added:
        summary = f"{sender} invited {_join_names(added)} to chat"
        store.append_message(slug, sender=sender, content=summary, kind="system")
        members = store.member_names(meta)
        notify_members(
            tell_fn=tell_fn,
            node=node,
            members=members,
            poster=sender,
            room=slug,
            headline=f"{summary} (#{slug}):",
            body=summary,
            skip=set(added),
        )
        for agent in added:
            tell_fn(
                agent,
                f"{summary} in #{slug}.{footer(node, slug)}",
            )

    parts = []
    if added:
        parts.append(f"invited {_join_names(added)} to #{slug}")
    if skipped:
        parts.append(f"already members: {_join_names(skipped)}")
    ack(tell_fn, sender, "; ".join(parts) if parts else f"no changes for #{slug}")
    return 0


def _cmd_list(store: RoomStore, sender: str, tell_fn: TellFn) -> int:
    text = _format_list(store)
    ack(tell_fn, sender, text)
    return 0


def _cmd_help(sender: str, node: str, tell_fn: TellFn) -> int:
    ack(tell_fn, sender, usage_help(node))
    return 0


def _cmd_view(
    store: RoomStore,
    sender: str,
    node: str,
    args: list[str],
    tell_fn: TellFn,
) -> int:
    try:
        slug, limit, before_id, start_n = parse_view_args(args)
    except ValueError as exc:
        error(
            tell_fn,
            sender,
            node,
            str(exc),
            hint="/view <room> [[start] limit] [--start N] [--limit N] [--before <id>]",
        )
        return 1
    try:
        store.load_meta(slug)
    except KeyError:
        error(
            tell_fn,
            sender,
            node,
            f"room not found: {slug}",
            hint="/list",
        )
        return 1
    messages = store.list_messages(slug)
    try:
        text = format_room_view(
            slug,
            messages,
            sender,
            limit=limit,
            before_id=before_id,
            start_n=start_n,
            node=node,
        )
    except KeyError:
        error(
            tell_fn,
            sender,
            node,
            f"message id not found in #{slug}",
            hint="/view <room> [--limit N]",
        )
        return 1
    ack(tell_fn, sender, text)
    return 0


def _cmd_members(
    store: RoomStore,
    sender: str,
    node: str,
    args: list[str],
    tell_fn: TellFn,
) -> int:
    if len(args) != 1:
        error(
            tell_fn,
            sender,
            node,
            "/members requires <room>",
            hint="/members <room>",
        )
        return 1
    slug = normalize_slug(args[0])
    text = _format_members(store, slug)
    ack(tell_fn, sender, text)
    return 0
