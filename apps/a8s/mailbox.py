"""a8s mailbox — inbox/outbox/trash routing and queue helpers.

Mailbox routing is process-agnostic: a per-agent daemon may write into any
other agent's inbox even though it isn't handling them. Only `wake_once` (in
daemon.py) requires the handler attachment.

Atomic alias fan-out (issue #67): `route_outboxes` writes each routed copy
into `<recipient>/inbox.tmp/<source-fname>` first, then renames the staged
files into `<recipient>/inbox/` only after every recipient has staged
successfully — so a process killed mid-fan-out leaves no half-routed state
that would deliver duplicates on retry. Source filename is preserved (not
uniquified) so retries are idempotent: a recipient whose final-inbox copy
already exists is skipped.

FILE: payload transfer (issue #62): each `FILE:` entry's bytes are copied
into `<recipient>/.files/<filename>` and the routed message's path rewritten
to the recipient-local copy. Sender paths are validated to live inside the
sender's own root before copy — the outbox is agent-writable, so an
unvalidated path could be a probe for files outside the sandbox.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from core import (
    MAX_FILE_BYTES,
    Participant,
    _preview,
    _safe_name,
    files_dir,
    inbox_dir,
    inbox_tmp_dir,
    outbox_dir,
    out_agent,
    trash_dir,
    unique_path,
)
from registry import resolve_name


# ---------- mailboxes ----------

def ensure_mailboxes(p: Participant) -> None:
    """Create mailbox dirs for `p`. Inbox, inbox.tmp, and trash live under
    ~/.a8s/ (hidden from the agent); outbox and .files live in the agent's
    own root (so the agent can write to outbox and read from .files under a
    workspace sandbox)."""
    for d in (inbox_dir(p.name), inbox_tmp_dir(p.name), trash_dir(p.name)):
        d.mkdir(parents=True, exist_ok=True)
    outbox_dir(p.root).mkdir(parents=True, exist_ok=True)
    files_dir(p.root).mkdir(parents=True, exist_ok=True)


def _transfer_file_to_recipient(
    sender_name: str,
    sender_root: Path,
    recipient_root: Path,
    entry: dict,
) -> dict | None:
    """Copy one `FILE:` payload from sender to `<recipient_root>/.files/`.
    Returns the rewritten file entry (recipient-local path) or None if the
    file was rejected (logged to sender's per-agent log).

    Defenses:
      - source must resolve INSIDE `sender_root` (the outbox is
        agent-writable, so an unvalidated path could be a probe for files
        outside the sandbox)
      - source must exist and be a regular file
      - source size must be <= MAX_FILE_BYTES (large payloads belong on a
        side-channel; see issue #63)

    On collision in `.files/`, `unique_path` appends `.1`, `.2`, ... — the
    routed message's `files[i].path` is updated to match.
    """
    src_path = Path(entry.get("path") or "").expanduser()
    sender_root_resolved = sender_root.resolve()
    try:
        if src_path.is_absolute():
            src_resolved = src_path.resolve()
        else:
            src_resolved = (sender_root_resolved / src_path).resolve()
    except (OSError, RuntimeError) as e:
        out_agent(sender_name, f"FILE: cannot resolve {src_path!s}: {e}")
        return None
    try:
        src_resolved.relative_to(sender_root_resolved)
    except ValueError:
        out_agent(
            sender_name,
            f"FILE: rejected — path outside sender root: {src_resolved}",
        )
        return None
    if not src_resolved.is_file():
        out_agent(sender_name, f"FILE: source missing or not a regular file: {src_resolved}")
        return None
    try:
        size = src_resolved.stat().st_size
    except OSError as e:
        out_agent(sender_name, f"FILE: stat failed for {src_resolved}: {e}")
        return None
    if size > MAX_FILE_BYTES:
        out_agent(
            sender_name,
            f"FILE: too large ({size} > {MAX_FILE_BYTES}): {src_resolved}",
        )
        return None
    dest_dir = files_dir(recipient_root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_path(dest_dir / src_resolved.name)
    try:
        shutil.copyfile(src_resolved, dest)
        os.chmod(dest, 0o644)
    except OSError as e:
        out_agent(sender_name, f"FILE: copy failed {src_resolved} -> {dest}: {e}")
        return None
    return {"filename": dest.name, "path": str(dest)}


def _build_routed_message(
    base_msg: dict,
    sender_name: str,
    sender_root: Path,
    recipient: Participant,
) -> dict:
    """Construct the routed copy of `base_msg` for `recipient` — copies any
    `FILE:` payloads into the recipient's `.files/` and rewrites the file
    paths in the returned message. Files that fail validation are dropped
    (logged); the message is delivered with the surviving files.

    Strict opacity (#69, #70): the `to` field is left at whatever the sender
    wrote — alias for fanned messages, agent name for direct ones — same as
    a public mailing list's `To:` header."""
    routed = dict(base_msg)
    src_files = base_msg.get("files") or []
    if src_files:
        new_files: list[dict] = []
        for entry in src_files:
            rewritten = _transfer_file_to_recipient(
                sender_name, sender_root, recipient.root, entry
            )
            if rewritten is not None:
                new_files.append(rewritten)
        routed["files"] = new_files
    return routed


def _commit_staged_inboxes(staged: list[tuple[Path, Path]]) -> None:
    """Atomically promote each `(staging, final)` pair from `inbox.tmp/` into
    `inbox/`. Uses `os.replace` so the rename is atomic on POSIX even if the
    final already exists (re-route after partial crash). Skips entries whose
    staging file is already gone (already promoted by a prior partial commit)."""
    for staging, final in staged:
        if not staging.is_file():
            continue
        os.replace(str(staging), str(final))


# ---------- routing ----------

def route_outboxes(senders: list[Participant], all_agents: list[Participant] | None = None) -> int:
    """Route each sender's outbox to recipients found in `all_agents`.

    `all_agents` is the recipient lookup pool (defaults to senders for
    self-contained calls). Aliases fan out at routing time; sender is excluded
    from delivery (no self-echo). Routed copies preserve the original `to`
    field — for an alias-fanned message that's the alias name, opaque about
    the other recipients (issues #69, #70).

    Atomicity (issue #67): each outbox file is staged into every recipient's
    `inbox.tmp/` first, then promoted into `inbox/` via `os.replace`, then
    the source outbox file is unlinked. The staging filename mirrors the
    source filename (no `unique_path` randomization), so a recipient whose
    `inbox/<source-name>` already exists is skipped on retry — the routing
    pass becomes idempotent across crashes."""
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
            if kind != "alias" and not recipients:
                out_agent(sender.name, f"[{sender.name}] {recipient_name!r} resolved to no agents in {f.name}")
                continue

            # Stage every recipient's copy into inbox.tmp/<source-name>. Skip
            # any recipient whose final inbox already has the file (prior
            # partial run already promoted that recipient's copy).
            staged: list[tuple[Path, Path]] = []
            try:
                for recipient in recipients:
                    ensure_mailboxes(recipient)
                    final = inbox_dir(recipient.name) / f.name
                    if final.is_file():
                        continue  # already delivered on a prior pass
                    staging = inbox_tmp_dir(recipient.name) / f.name
                    routed_msg = _build_routed_message(msg, sender.name, sender.root, recipient)
                    with staging.open("w", encoding="utf-8") as out_f:
                        json.dump(routed_msg, out_f, indent=2)
                    staged.append((staging, final))
            except OSError as e:
                # Stage failed — leave the outbox file in place for retry. Best-
                # effort gc of any partially-staged copies; they'll be replaced
                # on the next pass anyway (overwritten under the same name).
                out_agent(sender.name, f"[{sender.name}] stage failed on {f.name}: {e}")
                for staging, _ in staged:
                    try:
                        staging.unlink()
                    except OSError:
                        pass
                continue

            # All copies staged — atomic commit phase. `os.replace` is atomic
            # on POSIX; even if we crash between iterations, the next pass
            # detects already-promoted recipients via the `final.is_file()`
            # skip above and finishes the rest.
            try:
                _commit_staged_inboxes(staged)
            except OSError as e:
                out_agent(sender.name, f"[{sender.name}] commit failed on {f.name}: {e}")
                continue

            if kind == "alias":
                out_agent(sender.name, f"routed: {sender.name} -> {recipient_name} (alias of {len(recipients)}): {preview}")
                for recipient in recipients:
                    out_agent(recipient.name, f"received from {sender.name} (via {recipient_name} alias): {preview}")
                routed += len(recipients)
            else:
                recipient = recipients[0]
                out_agent(sender.name, f"routed: {sender.name} -> {recipient.name}: {preview}")
                out_agent(recipient.name, f"received from {sender.name}: {preview}")
                routed += 1
            try:
                f.unlink()
            except OSError:
                pass
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

    The empty `from` is the signal to `select_verb` to dispatch via
    `invokePrompt` (raw content, no sender header). The next inbox-drain
    wakes the agent."""
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
