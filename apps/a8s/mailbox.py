"""a8s mailbox — inbox/outbox/trash routing and queue helpers.

Mailbox routing is process-agnostic: a per-agent daemon may write into any
other agent's inbox even though it isn't handling them. Only `wake_once` (in
daemon.py) requires the handler attachment.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core import (
    Participant,
    _preview,
    _safe_name,
    inbox_dir,
    outbox_dir,
    out_agent,
    trash_dir,
    unique_path,
)
from registry import resolve_name


# ---------- mailboxes ----------

def ensure_mailboxes(p: Participant) -> None:
    """Create mailbox dirs for `p`. Inbox and trash live under ~/.a8s/ (hidden
    from the agent); outbox lives in the agent's own root (so the agent can
    actually write to it under a workspace sandbox)."""
    for d in (inbox_dir(p.name), trash_dir(p.name)):
        d.mkdir(parents=True, exist_ok=True)
    outbox_dir(p.root).mkdir(parents=True, exist_ok=True)


# ---------- routing ----------

def route_outboxes(senders: list[Participant], all_agents: list[Participant] | None = None) -> int:
    """Route each sender's outbox to recipients found in `all_agents`.

    `all_agents` is the recipient lookup pool (defaults to senders for
    self-contained calls). Aliases fan out at routing time; sender is excluded
    from delivery (no self-echo). Each routed copy carries `alias` and
    `others_count` fields for the recipient's prompt template."""
    if all_agents is None:
        all_agents = senders
    by_name = {p.name.lower(): p for p in all_agents}
    routed = 0
    for sender in senders:
        ensure_mailboxes(sender)
        outbox = outbox_dir(sender.root)
        for f in sorted(outbox.iterdir()):
            if not (f.is_file() and f.name.endswith(".json")):
                continue
            try:
                with f.open("r", encoding="utf-8") as fp:
                    msg = json.load(fp)
            except (OSError, json.JSONDecodeError) as e:
                out_agent(sender.name, f"[{sender.name}] outbox parse error on {f.name}: {e}")
                continue
            # Defense: the outbox is writable by the agent, so it could try to
            # spoof a senderless prompt with `from: ""` or impersonate someone
            # else. Force `from` to the actual enclosing participant — outbox
            # location is the unforgeable identity.
            msg["from"] = sender.name
            recipient_name = (msg.get("to") or "").strip()
            preview = _preview(msg.get("content", ""))
            if not recipient_name:
                out_agent(sender.name, f"[{sender.name}] empty 'to' in {f.name}; rejecting (use an alias for groups)")
                bad = unique_path(trash_dir(sender.name) / f.name)
                f.rename(bad)
                continue
            try:
                kind, member_names = resolve_name(recipient_name)
            except KeyError:
                out_agent(sender.name, f"[{sender.name}] unknown recipient {recipient_name!r} in {f.name}")
                continue
            except ValueError as e:
                out_agent(sender.name, f"[{sender.name}] {e} in {f.name}")
                continue
            recipients: list[Participant] = []
            for member in member_names:
                rp = by_name.get(member.lower())
                if rp is not None and rp.name != sender.name:
                    # Skip self-copy: an alias that includes the sender doesn't
                    # echo the message back to them.
                    recipients.append(rp)
            if kind == "alias":
                msg["alias"] = recipient_name
                msg["others_count"] = max(0, len(recipients) - 1)
                out_agent(sender.name, f"routed: {sender.name} -> {recipient_name} (alias of {len(recipients)}): {preview}")
                for recipient in recipients:
                    ensure_mailboxes(recipient)
                    copy = dict(msg)
                    copy["to"] = recipient.name
                    dest = unique_path(inbox_dir(recipient.name) / f.name)
                    with dest.open("w", encoding="utf-8") as out_f:
                        json.dump(copy, out_f, indent=2)
                    out_agent(recipient.name, f"received from {sender.name} (via {recipient_name} alias): {preview}")
                f.unlink()
                routed += len(recipients)
            else:
                # Single agent recipient.
                if not recipients:
                    out_agent(sender.name, f"[{sender.name}] {recipient_name!r} resolved to no agents in {f.name}")
                    continue
                recipient = recipients[0]
                ensure_mailboxes(recipient)
                dest = unique_path(inbox_dir(recipient.name) / f.name)
                with dest.open("w", encoding="utf-8") as out_f:
                    json.dump(msg, out_f, indent=2)
                f.unlink()
                out_agent(sender.name, f"routed: {sender.name} -> {recipient.name}: {preview}")
                out_agent(recipient.name, f"received from {sender.name}: {preview}")
                routed += 1
    return routed


def next_inbox_message(p: Participant) -> Path | None:
    inbox = inbox_dir(p.name)
    if not inbox.is_dir():
        return None
    files = sorted(f for f in inbox.iterdir() if f.is_file() and f.name.endswith(".json"))
    return files[0] if files else None


# ---------- queue helpers (used by cmd_tell, cmd_prompt, cmd_clear) ----------

def _split_content_and_files(raw: str) -> tuple[str, list[dict]]:
    lines = raw.splitlines()
    files: list[dict] = []
    while lines and lines[-1].strip().startswith("FILE:"):
        path = lines.pop().strip()[len("FILE:"):].strip()
        if path:
            files.insert(0, {"filename": Path(path).name, "path": path})
    return "\n".join(lines).rstrip(), files


def _write_outbox(sender_name: str, sender_root: Path, to: str, content: str, files: list[dict]) -> Path:
    outbox = outbox_dir(sender_root)
    outbox.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    msg = {
        "date": now.isoformat().replace("+00:00", "Z"),
        "from": sender_name,
        "to": to,
        "content": content,
        "files": files,
    }
    safe_sender = _safe_name(sender_name)
    fname = f"{now.strftime('%Y%m%dT%H%M%S%f')}_{safe_sender}.json"
    dest = unique_path(outbox / fname)
    with dest.open("w", encoding="utf-8") as f:
        json.dump(msg, f, indent=2)
    return dest


def _queue_prompt(p: Participant, content: str) -> Path:
    """Drop a senderless message JSON directly into <p>/inbox/.

    The empty `from` is the signal to `build_prompt` to deliver the raw
    content (no `tells you` template wrapping). The next inbox-drain wakes
    the agent."""
    ensure_mailboxes(p)
    now = datetime.now(timezone.utc)
    msg = {
        "date": now.isoformat().replace("+00:00", "Z"),
        "from": "",
        "to": p.name,
        "content": content,
        "files": [],
    }
    fname = f"{now.strftime('%Y%m%dT%H%M%S%f')}_PROMPT.json"
    dest = unique_path(inbox_dir(p.name) / fname)
    with dest.open("w", encoding="utf-8") as f:
        json.dump(msg, f, indent=2)
    return dest


def _queue_clear_sentinel(p: Participant) -> Path:
    """Drop a CLEAR sentinel into <p>/inbox/. Per the locked design (Q1,
    belt-and-suspenders): wipe everything currently in the inbox to trash
    so the sentinel is the only message at write time. Read-time wipe in
    `wake_once` handles anything that arrives in the gap.

    The sentinel has `from: ""` and `clear: true`. `select_verb` routes
    it to `invokeClear`, which runs without a prompt to start a fresh
    conversation."""
    ensure_mailboxes(p)
    # Write-time wipe: trash everything in the inbox.
    for f in inbox_dir(p.name).iterdir():
        if f.is_file():
            trashed = unique_path(trash_dir(p.name) / f.name)
            f.rename(trashed)
    now = datetime.now(timezone.utc)
    msg = {
        "date": now.isoformat().replace("+00:00", "Z"),
        "from": "",
        "to": p.name,
        "content": "",
        "files": [],
        "clear": True,
    }
    fname = f"{now.strftime('%Y%m%dT%H%M%S%f')}_CLEAR.json"
    dest = unique_path(inbox_dir(p.name) / fname)
    with dest.open("w", encoding="utf-8") as f:
        json.dump(msg, f, indent=2)
    return dest
