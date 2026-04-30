"""a8s mailbox — inbox/outbox/trash routing and queue helpers.

Mailbox routing is process-agnostic: a per-agent daemon may write into any
other agent's inbox even though it isn't handling them. Only `wake_once` (in
daemon.py) requires the handler attachment.

Routing runs in two phases per pass (issue #63 prep):

1. INGEST — atomically move `<root>/.outbox/<f>.json` into
   `~/.a8s/agents/<sender>/pending/<f>.json`. This is the only thing a8s
   ever does to a file in `<root>/.outbox/` — the agent's directory is
   one-way (agent writes, a8s renames out, never read-modify-write). After
   this phase the agent's outbox dir is empty for the duration of the pass.

2. PROCESS — for each pending file, load (or initialize) a `<f>.json.retry`
   sidecar that tracks attempts, next-attempt time, which configured remotes
   already accepted the publish, and whether local delivery has happened.
   Local delivery uses the existing maildir-style staging (`inbox.tmp/` →
   `inbox/` via `os.replace`). Remote publishing is the chunk-7 hook —
   the no-remote path stays semantically identical to the pre-#63 code:
   deliver locally, unlink.

Backoff / retry (issue #63): when some configured remote hasn't yet
accepted, attempts increment and the sidecar's `next_attempt` is bumped
according to `BACKOFF_SCHEDULE`. After `MAX_ATTEMPTS` failures the
message is moved to trash with a "discarded after backoff exhausted"
log.

Atomic alias fan-out (issue #67): each routed copy is written into
`<recipient>/inbox.tmp/<source-fname>` first, then renamed into
`<recipient>/inbox/` only after every recipient has staged. A crash mid-
fan-out leaves no partial state. Source filename (a ULID) is preserved
across staging so a recipient whose final inbox already has the file is
skipped — retries are idempotent.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from core import (
    BACKOFF_SCHEDULE,
    MAX_ATTEMPTS,
    MAX_FILE_BYTES,
    Participant,
    _preview,
    files_dir,
    inbox_dir,
    inbox_tmp_dir,
    outbox_dir,
    out_agent,
    pending_dir,
    retry_sidecar_path,
    trash_dir,
    unique_path,
)
from network import seen_id_append
from registry import resolve_name
from services import StorageError, StorageService
from ulid import new as new_ulid

# A function that publishes one routed-and-from-stamped message envelope to
# every configured remote that hasn't yet accepted it. Returns the updated
# `succeeded_remotes` list. Daemon wires this up at startup; passing None
# keeps the path purely local (no remotes configured).
PublishRemotes = Callable[[dict, str, list[str], int], list[str]]


# ---------- mailboxes ----------

def ensure_mailboxes(p: Participant) -> None:
    """Create mailbox dirs for `p`. Inbox, inbox.tmp, pending, and trash live
    under ~/.a8s/ (hidden from the agent); outbox and .files live in the
    agent's own root (so the agent can write to outbox and read from .files
    under a workspace sandbox)."""
    for d in (
        inbox_dir(p.name),
        inbox_tmp_dir(p.name),
        pending_dir(p.name),
        trash_dir(p.name),
    ):
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

def _ingest_outboxes(senders: list[Participant]) -> None:
    """Phase 1: atomically move every `<root>/.outbox/<f>.json` into
    `~/.a8s/agents/<sender>/pending/<f>.json`. The agent's `.outbox/` is
    one-way — a8s never opens a file there for read-modify-write, never
    writes a sidecar there. After this pass the outbox dir is empty and
    every subsequent step happens under ~/.a8s/.

    Cross-fs fallback uses `shutil.copy2` + `unlink` instead of `os.rename`.
    Not atomic; if the process dies mid-copy, the file may briefly exist in
    both places, but ULID-keyed dedup at the receive side tolerates the
    duplicate. We don't expect this path on a normal install (~/.a8s/ and
    the agent root are usually on the same filesystem)."""
    for sender in senders:
        ensure_mailboxes(sender)
        outbox = outbox_dir(sender.root)
        if not outbox.is_dir():
            continue
        dest_dir = pending_dir(sender.name)
        for f in sorted(outbox.iterdir()):
            if not (f.is_file() and f.name.endswith(".json")):
                continue
            dest = dest_dir / f.name
            try:
                os.rename(str(f), str(dest))
            except OSError:
                try:
                    shutil.copy2(str(f), str(dest))
                    f.unlink()
                except OSError as e:
                    out_agent(sender.name, f"ingest copy failed on {f.name}: {e}")


def _load_or_init_sidecar(pending_file: Path) -> dict:
    p = retry_sidecar_path(pending_file)
    if p.is_file():
        try:
            with p.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                data.setdefault("attempts", 0)
                data.setdefault("next_attempt", "")
                data.setdefault("succeeded_remotes", [])
                data.setdefault("local_delivered", False)
                data.setdefault("uploaded", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass  # corrupt sidecar — start fresh
    return {
        "attempts": 0,
        "next_attempt": "",
        "succeeded_remotes": [],
        "local_delivered": False,
        # Per-file × per-service upload cache for cross-cluster `FILE:`
        # payloads. Shape: {"<filename>": {"<service_id>": "<download_url>"}}.
        # A backoff retry only re-uploads to services still missing.
        "uploaded": {},
    }


def _save_sidecar(pending_file: Path, sidecar: dict) -> None:
    p = retry_sidecar_path(pending_file)
    try:
        with p.open("w", encoding="utf-8") as fp:
            json.dump(sidecar, fp, indent=2)
    except OSError:
        pass  # best-effort — bad write means we'll retry on the next pass


def _drop_sidecar(pending_file: Path) -> None:
    p = retry_sidecar_path(pending_file)
    try:
        p.unlink()
    except OSError:
        pass


def _trash_pending(sender: Participant, pending_file: Path) -> None:
    bad = unique_path(trash_dir(sender.name) / pending_file.name)
    try:
        pending_file.rename(bad)
    except OSError:
        pass


def _schedule_retry(pending_file: Path, sidecar: dict, sender: Participant) -> None:
    """Increment attempts and either set the next-attempt time per
    BACKOFF_SCHEDULE or trash the message if the schedule is exhausted."""
    sidecar["attempts"] += 1
    if sidecar["attempts"] > MAX_ATTEMPTS:
        out_agent(sender.name, f"discarded {pending_file.name} after backoff exhausted")
        _trash_pending(sender, pending_file)
        _drop_sidecar(pending_file)
        return
    delay_idx = min(sidecar["attempts"] - 1, len(BACKOFF_SCHEDULE) - 1)
    next_dt = datetime.now(timezone.utc) + timedelta(seconds=BACKOFF_SCHEDULE[delay_idx])
    sidecar["next_attempt"] = next_dt.isoformat().replace("+00:00", "Z")
    _save_sidecar(pending_file, sidecar)


def _resolve_file_source(sender_root: Path, entry: dict) -> Path | None:
    """Validate and resolve a `FILE:` entry's source path, returning a
    sandbox-safe absolute path or None if the path can't be used. Same
    validation rules as `_transfer_file_to_recipient` (exists, regular file,
    inside sender_root, under MAX_FILE_BYTES) — factored out so the upload
    path can reuse the defenses without copying bytes."""
    src_path = Path(entry.get("path") or "").expanduser()
    sender_root_resolved = sender_root.resolve()
    try:
        if src_path.is_absolute():
            src_resolved = src_path.resolve()
        else:
            src_resolved = (sender_root_resolved / src_path).resolve()
    except (OSError, RuntimeError):
        return None
    try:
        src_resolved.relative_to(sender_root_resolved)
    except ValueError:
        return None
    if not src_resolved.is_file():
        return None
    try:
        size = src_resolved.stat().st_size
    except OSError:
        return None
    if size > MAX_FILE_BYTES:
        return None
    return src_resolved


def _upload_files_for_remote(
    msg: dict,
    sender_name: str,
    sender_root: Path,
    services: list[StorageService],
    sidecar: dict,
) -> bool:
    """Upload every file in `msg["files"]` to every configured storage
    service that hasn't yet accepted it. Caches results in
    `sidecar["uploaded"][filename][service_id] = url` so a backoff retry
    only re-uploads to services still missing.

    Side-effects:
      - Mutates `sidecar["uploaded"]` with each successful upload.
      - On full success (every file × every service covered), rewrites
        `msg["files"]` in-place to the wire shape:
        `[{"filename": ..., "storage": [url1, url2, ...]}]` (drops `path`).

    Returns True if every file is now covered by every configured service
    (caller proceeds to publish). Returns False if any upload failed
    (caller schedules retry; msg["files"] stays in its on-disk shape)."""
    files = msg.get("files") or []
    if not files or not services:
        return True  # no work to do

    uploaded: dict = sidecar.setdefault("uploaded", {})
    all_done = True
    for entry in files:
        filename = entry.get("filename") or ""
        if not filename:
            continue  # malformed entry — skip silently
        per_file = uploaded.setdefault(filename, {})
        # We need the source path each pass — even on retry. The sender's
        # outbox bytes still live at the original location until the message
        # finalizes (we don't move them).
        src = _resolve_file_source(sender_root, entry)
        if src is None and any(s.id not in per_file for s in services):
            # Source unreadable / oversized / outside-root and at least one
            # service still needs it. Treat as upload failure for those
            # services so the message retries (the file may have appeared
            # mid-pass, etc.). If every service already has its URL, we're
            # fine even with the source gone.
            out_agent(
                sender_name,
                f"FILE: cannot read source for upload (filename={filename!r}); will retry",
            )
            all_done = False
            continue
        for service in services:
            if service.id in per_file:
                continue
            try:
                url = service.store(src)  # type: ignore[arg-type]
            except StorageError as e:
                out_agent(
                    sender_name,
                    f"WARN storage {service.id} upload failed for {filename!r} (attempt {sidecar['attempts'] + 1}): {e}",
                )
                all_done = False
                continue
            except Exception as e:
                out_agent(
                    sender_name,
                    f"WARN storage {service.id} upload raised for {filename!r} (attempt {sidecar['attempts'] + 1}): {e}",
                )
                all_done = False
                continue
            per_file[service.id] = url

    if not all_done:
        return False

    # Every file is covered by every service — rewrite to the wire shape.
    new_files: list[dict] = []
    for entry in files:
        filename = entry.get("filename") or ""
        urls = list(uploaded.get(filename, {}).values())
        new_files.append({"filename": filename, "storage": urls})
    msg["files"] = new_files
    return True


def _download_files_to_recipient(
    msg: dict,
    recipient: Participant,
    services: list[StorageService],
) -> dict:
    """Pull `msg["files"][i]["storage"]` URLs into the recipient's
    `<root>/.files/`. Returns a NEW dict with `files` rewritten to
    recipient-local shape `{filename, path}`. Files that no configured
    service can download are dropped + logged on the recipient's per-agent
    log; the message is delivered with the surviving files (matches local
    delivery's "rejected file dropped, message survives" semantics).

    Multiple `storage` URLs per file are tried in order until one service
    successfully downloads — equivalent uploads from the sender's POV, so
    first wins. Mismatched URLs (no configured service accepts) just fall
    through; only an actual `StorageError` from a matched service counts
    as failure for that URL."""
    out_msg = dict(msg)
    src_files = msg.get("files") or []
    new_files: list[dict] = []
    dest_dir = files_dir(recipient.root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for entry in src_files:
        filename = entry.get("filename") or ""
        urls = entry.get("storage") or []
        if not filename or not urls:
            continue  # malformed wire entry; drop
        dest = unique_path(dest_dir / filename)
        delivered = False
        for url in urls:
            for service in services:
                try:
                    if service.retrieve(url, dest):
                        delivered = True
                        break
                except StorageError as e:
                    out_agent(
                        recipient.name,
                        f"WARN storage {service.id} download failed for {filename!r}: {e}",
                    )
                    # Real failure on a matched URL — try next URL/service.
            if delivered:
                break
        if delivered:
            new_files.append({"filename": dest.name, "path": str(dest)})
        else:
            out_agent(
                recipient.name,
                f"WARN no configured storage service could download {filename!r} (urls={urls})",
            )
    out_msg["files"] = new_files
    return out_msg


def _process_pending(
    sender: Participant,
    by_name: dict[str, Participant],
    publish_remotes: Optional[PublishRemotes],
    configured_remote_ids: list[str],
    services: Optional[list[StorageService]] = None,
) -> int:
    """Phase 2: iterate `~/.a8s/agents/<sender>/pending/`, deliver each
    pending file locally and/or publish to not-yet-succeeded remotes. The
    sidecar tracks per-message progress so a partial pass can be resumed
    cheaply on the next routing iteration. Returns the count of completed
    local deliveries this pass (matches the legacy `route_outboxes` count
    for the local-only path)."""
    routed = 0
    pending = pending_dir(sender.name)
    if not pending.is_dir():
        return 0
    now = datetime.now(timezone.utc)
    files = sorted(
        f for f in pending.iterdir()
        if f.is_file() and f.name.endswith(".json")
    )
    for f in files:
        sidecar = _load_or_init_sidecar(f)
        # Backoff gate.
        if sidecar["next_attempt"]:
            try:
                next_dt = datetime.fromisoformat(sidecar["next_attempt"].replace("Z", "+00:00"))
                if now < next_dt:
                    continue
            except ValueError:
                pass  # corrupt timestamp — fall through and try
        try:
            with f.open("r", encoding="utf-8") as fp:
                msg = json.load(fp)
        except (OSError, json.JSONDecodeError) as e:
            out_agent(sender.name, f"pending parse error on {f.name}: {e}; trashing")
            _trash_pending(sender, f)
            _drop_sidecar(f)
            continue
        # Defense: the outbox was agent-writable, so the JSON could lie about
        # `from`. The unforgeable identity is the enclosing sender — overwrite.
        msg["from"] = sender.name
        recipient_name = (msg.get("to") or "").strip()
        preview = _preview(msg.get("content", ""))
        if not recipient_name:
            out_agent(sender.name, f"empty 'to' in {f.name}; rejecting")
            _trash_pending(sender, f)
            _drop_sidecar(f)
            continue
        # ----- LOCAL ROUTING -----
        local_target_known = False
        if not sidecar["local_delivered"]:
            kind = None
            member_names: list[str] = []
            try:
                kind, member_names = resolve_name(recipient_name)
                local_target_known = True
            except KeyError:
                pass  # unknown locally — remotes (if any) might still deliver
            except ValueError as e:
                out_agent(sender.name, f"{e} in {f.name}; trashing")
                _trash_pending(sender, f)
                _drop_sidecar(f)
                continue
            if local_target_known:
                recipients: list[Participant] = []
                for m in member_names:
                    rp = by_name.get(m.lower())
                    if rp is not None and rp.name != sender.name:
                        recipients.append(rp)
                if recipients:
                    staged: list[tuple[Path, Path]] = []
                    stage_failed = False
                    try:
                        for recipient in recipients:
                            ensure_mailboxes(recipient)
                            final = inbox_dir(recipient.name) / f.name
                            if final.is_file():
                                continue
                            staging = inbox_tmp_dir(recipient.name) / f.name
                            routed_msg = _build_routed_message(msg, sender.name, sender.root, recipient)
                            with staging.open("w", encoding="utf-8") as out_f:
                                json.dump(routed_msg, out_f, indent=2)
                            staged.append((staging, final))
                    except OSError as e:
                        out_agent(sender.name, f"stage failed on {f.name}: {e}")
                        for staging, _ in staged:
                            try:
                                staging.unlink()
                            except OSError:
                                pass
                        stage_failed = True
                    if not stage_failed:
                        try:
                            _commit_staged_inboxes(staged)
                            sidecar["local_delivered"] = True
                            # Claim the ULID in the cluster-wide seen-ids ring
                            # so a remote round-trip (we publish to MQTT below;
                            # the broker pushes back to our own subscriber)
                            # gets deduped instead of writing a second inbox
                            # entry. Whichever recipient(s) accepted the local
                            # write counts — one append per envelope.
                            local_msg_id = msg.get("id", "")
                            if local_msg_id:
                                seen_id_append(local_msg_id)
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
                        except OSError as e:
                            out_agent(sender.name, f"commit failed on {f.name}: {e}")
                            # leave for retry
                else:
                    # Local target resolved but no recipients (alias with only
                    # the sender as a member, or the sender targeting themselves).
                    # Nothing to deliver locally; mark done so we don't loop.
                    out_agent(sender.name, f"{recipient_name!r} has no local recipients (excluding self)")
                    sidecar["local_delivered"] = True
        # ----- REMOTE ROUTING -----
        has_files = bool(msg.get("files"))
        if publish_remotes is not None and configured_remote_ids:
            services_list = services or []
            if has_files and not services_list:
                # No storage services configured — file payloads can't make
                # the trip. Mark all remotes "succeeded" so the message
                # finalizes after local delivery instead of looping on
                # retries (#62 v1 fallback: local-only with files when no
                # services, full pipeline when services are configured).
                if sidecar["attempts"] == 0:
                    out_agent(sender.name, f"FILE: payloads in {f.name} not published to remotes (no storage configured)")
                sidecar["succeeded_remotes"] = list(configured_remote_ids)
            else:
                # Upload any files first (per-file × per-service cache lives
                # in the sidecar). On full success the in-memory msg gets
                # rewritten to the wire shape; on partial failure we fall
                # through to schedule a retry without publishing this pass.
                publish_msg = dict(msg)
                upload_ok = True
                if has_files:
                    publish_msg["files"] = list(msg.get("files") or [])
                    upload_ok = _upload_files_for_remote(
                        publish_msg, sender.name, sender.root, services_list, sidecar,
                    )
                if upload_ok:
                    sidecar["succeeded_remotes"] = publish_remotes(
                        publish_msg, sender.name, list(sidecar["succeeded_remotes"]), sidecar["attempts"]
                    )
                # else: leave succeeded_remotes alone; backoff retry will
                # finish the uploads and try the publish next pass.
        # ----- OUTCOME -----
        remaining_remotes = [
            rid for rid in configured_remote_ids
            if rid not in sidecar["succeeded_remotes"]
        ]
        no_path_at_all = (
            not local_target_known
            and not sidecar["local_delivered"]
            and not configured_remote_ids
        )
        if no_path_at_all:
            out_agent(sender.name, f"unknown recipient {recipient_name!r} in {f.name}; trashing")
            _trash_pending(sender, f)
            _drop_sidecar(f)
            continue
        all_done = (
            not remaining_remotes
            and (not local_target_known or sidecar["local_delivered"])
        )
        if all_done:
            try:
                f.unlink()
            except OSError:
                pass
            _drop_sidecar(f)
            continue
        # Still pending — schedule a retry with backoff.
        _schedule_retry(f, sidecar, sender)
    return routed


def route_outboxes(
    senders: list[Participant],
    all_agents: list[Participant] | None = None,
    publish_remotes: Optional[PublishRemotes] = None,
    configured_remote_ids: Optional[list[str]] = None,
    services: Optional[list[StorageService]] = None,
) -> int:
    """Two-phase routing pass:

      1. Ingest: move new outbox files out of every sender's `<root>/.outbox/`
         and into `~/.a8s/agents/<sender>/pending/`. The agent's directory is
         touched only by the rename — never read-modified-rewritten.
      2. Process: deliver each pending message to local recipients (via
         `inbox.tmp/` → `inbox/` atomic stage→commit) and/or publish to any
         configured remote that hasn't yet accepted it. Per-message retry
         state lives in `<f>.json.retry` alongside the pending file.

    `publish_remotes` and `configured_remote_ids` are the daemon-wired
    hooks for cross-cluster routing; both default to None / [] so an
    install with no remotes configured behaves identically to pre-#63
    except for the on-disk location of in-flight messages.

    `services` is the storage-service hook (#90) for cross-cluster `FILE:`
    payloads. When set and a message has files, each service uploads its
    bytes and the wire envelope carries `files[i].storage = [...]`. None /
    empty falls back to the v1 limitation (files local-only, remote skip)."""
    if all_agents is None:
        all_agents = senders
    by_name = {p.name.lower(): p for p in all_agents}
    if configured_remote_ids is None:
        configured_remote_ids = []
    _ingest_outboxes(senders)
    routed = 0
    for sender in senders:
        routed += _process_pending(
            sender, by_name, publish_remotes, configured_remote_ids, services,
        )
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
    msg_id = new_ulid()
    msg = {
        "id": msg_id,
        "date": now.isoformat().replace("+00:00", "Z"),
        "from": sender_name,
        "to": to,
        "content": content,
        "files": files,
    }
    dest = unique_path(outbox / f"{msg_id}.json")
    with dest.open("w", encoding="utf-8") as f:
        json.dump(msg, f, indent=2)
    return dest


