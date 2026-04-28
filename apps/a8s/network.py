"""a8s remote routing — config, publish-with-backoff, receive loop, dedup.

a8s only crosses cluster boundaries on outbound messages (`tell` /
`prompt`). State queries (`logs`, `ls`, `agents`) are strictly local.
This module wires the message side: `~/.a8s/network.json` (dict-shaped:
name → {transport, broker, topic, ...}) becomes a list of Transport
instances. The routing pass uses `publish_with_backoff` as its
`route_outboxes(publish_remotes=...)` hook; each running attached_loop
spawns one subscriber thread per remote that calls into
`receive_envelope`. Cluster-wide dedup lives in the seen-ids ring file
at `~/.a8s/seen-ids`.

Transport modules are imported lazily. `load_remotes()` only pulls in
e.g. `transports.mqtt` when it sees a `transport: mqtt` entry in the
config; an a8s install with no remotes never imports paho-mqtt or any
other transport library.

`_build_transport` forwards every key past `transport`/`broker`/`topic`
to the transport constructor as `**opts`, so adding a new transport
option doesn't require touching this dispatcher — only the transport's
own option-bag handling.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable

from core import (
    MAX_SEEN_IDS,
    Participant,
    _preview,
    inbox_dir,
    inbox_tmp_dir,
    network_config_path,
    out,
    out_agent,
    seen_ids_path,
    transient_inbox_dir,
    transient_inbox_tmp_dir,
)
from registry import resolve_name
import transient as transient_dirs
from transports import OnMessage, Transport, TransportError
from ulid import is_ulid


# Process-local lock guarding the seen-ids ring rotation. Multiple subscriber
# threads (one per remote) call seen_id_append concurrently; the append
# itself is atomic per POSIX, but the truncate-after-rotate is not.
_SEEN_IDS_LOCK = threading.Lock()


# ---------- network.json ----------

def load_network_config() -> dict:
    p = network_config_path()
    if not p.is_file():
        return {"remotes": {}}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        out(f"WARN: ~/.a8s/network.json malformed ({e}); treating as empty")
        return {"remotes": {}}
    if not isinstance(data, dict):
        return {"remotes": {}}
    data.setdefault("remotes", {})
    if not isinstance(data["remotes"], dict):
        data["remotes"] = {}
    return data


def save_network_config(cfg: dict) -> None:
    p = network_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# Top-level keys in a network.json entry that are not transport options
# (they're consumed by the dispatcher itself before forwarding the rest).
_RESERVED_SPEC_KEYS = {"transport", "broker", "topic"}


def _build_transport(name: str, spec: dict) -> Transport:
    """Instantiate one Transport from a network.json entry. Forwards every
    key past `transport` / `broker` / `topic` as `**opts` to the transport
    constructor — each transport handles its own option vocabulary,
    aliases (e.g. `user` → `username`), and rejects unknowns."""
    kind = (spec.get("transport") or "").strip().lower()
    broker = spec.get("broker")
    topic = spec.get("topic")
    if not broker or not topic:
        raise ValueError(f"remote {name!r}: every transport requires `broker` and `topic`")
    opts = {k: v for k, v in spec.items() if k not in _RESERVED_SPEC_KEYS}
    if kind == "mqtt":
        # Lazy import — keeps paho out of the import graph for users with no
        # remotes configured.
        from transports.mqtt import MqttTransport

        return MqttTransport(remote_id=name, broker=broker, topic=topic, **opts)
    raise ValueError(f"remote {name!r}: unsupported transport {kind!r}")


def load_remotes() -> list[Transport]:
    """Return Transport instances for every entry in `~/.a8s/network.json`.
    Failures (bad config, missing transport module) are logged and skipped —
    never block a8s startup."""
    cfg = load_network_config()
    out_list: list[Transport] = []
    for name, spec in cfg["remotes"].items():
        if not isinstance(spec, dict):
            out(f"WARN: remote {name!r} config is not an object; skipping")
            continue
        try:
            out_list.append(_build_transport(name, spec))
        except Exception as e:
            out(f"WARN: remote {name!r} skipped: {e}")
    return out_list


def configured_remote_ids() -> list[str]:
    """Just the ordered list of remote IDs from network.json. Used by the
    routing pass to know which remotes to wait on without paying the cost
    of building the full Transport instances."""
    return list(load_network_config()["remotes"].keys())


# ---------- seen-ids ring ----------

def seen_id_contains(ulid: str) -> bool:
    p = seen_ids_path()
    if not p.is_file():
        return False
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip() == ulid:
                    return True
    except OSError:
        pass
    return False


def seen_id_append(ulid: str) -> None:
    """Append a ULID to the ring, rotating to the last MAX_SEEN_IDS entries
    when the file grows past the cap. Best-effort — disk failures don't
    propagate (a missed append just means we might re-deliver a duplicate)."""
    with _SEEN_IDS_LOCK:
        p = seen_ids_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(ulid + "\n")
        except OSError:
            return
        # Rotation check.
        try:
            with p.open("r", encoding="utf-8") as f:
                lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        except OSError:
            return
        if len(lines) > MAX_SEEN_IDS:
            tmp = p.with_suffix(p.suffix + ".tmp")
            try:
                with tmp.open("w", encoding="utf-8") as out_f:
                    for u in lines[-MAX_SEEN_IDS:]:
                        out_f.write(u + "\n")
                os.replace(str(tmp), str(p))
            except OSError:
                pass


# ---------- send (publish_with_backoff) ----------

def make_publish_remotes(remotes: list[Transport]) -> Callable:
    """Build the `publish_remotes` callable that `route_outboxes` invokes.
    For each not-yet-succeeded remote, attempts a publish; on failure logs
    a warning to the sender's per-agent log and leaves the remote in the
    `pending_remotes` set for the next pass. Returns the updated
    `succeeded_remotes` list."""

    def publish_with_backoff(
        msg: dict,
        sender_name: str,
        succeeded_so_far: list[str],
        attempt_count: int,
    ) -> list[str]:
        envelope = json.dumps(msg).encode("utf-8")
        succeeded = list(succeeded_so_far)
        for remote in remotes:
            if remote.id in succeeded:
                continue
            try:
                remote.publish(envelope)
                succeeded.append(remote.id)
            except TransportError as e:
                out_agent(
                    sender_name,
                    f"WARN remote {remote.id} publish failed (attempt {attempt_count + 1}): {e}",
                )
            except Exception as e:
                out_agent(
                    sender_name,
                    f"WARN remote {remote.id} publish raised (attempt {attempt_count + 1}): {e}",
                )
        return succeeded

    return publish_with_backoff


# ---------- receive ----------

def _deliver_to_transient(name: str, msg_id: str, msg: dict) -> None:
    """Atomic stage→commit into a live transient's inbox. Used by both the
    daemon's subscribers (so a remote reply to `ASK_<ulid>` finds its way
    home even when the asker isn't running its own subscriber) and by
    `a8s ask` itself when its self-spawned subscriber receives the reply."""
    transient_inbox_dir(name).mkdir(parents=True, exist_ok=True)
    transient_inbox_tmp_dir(name).mkdir(parents=True, exist_ok=True)
    final = transient_inbox_dir(name) / f"{msg_id}.json"
    if final.is_file():
        return
    staging = transient_inbox_tmp_dir(name) / f"{msg_id}.json"
    try:
        with staging.open("w", encoding="utf-8") as f:
            json.dump(msg, f, indent=2)
        os.replace(str(staging), str(final))
    except OSError as e:
        out(f"WARN failed to write transient envelope id={msg_id} to {name}: {e}")


def receive_envelope(envelope: bytes, all_agents: list[Participant]) -> None:
    """Decode an incoming envelope, dedupe, filter against the local
    registry, and atomically write into each matched local recipient's
    inbox. Drops silently if the recipient isn't ours, the envelope is
    malformed, or the ULID has been seen before — nothing should crash the
    subscriber thread."""
    try:
        msg = json.loads(envelope)
        if not isinstance(msg, dict):
            raise ValueError("envelope is not a JSON object")
    except (ValueError, UnicodeDecodeError) as e:
        out(f"WARN: dropped malformed envelope ({e})")
        return
    msg_id = msg.get("id", "")
    if not isinstance(msg_id, str) or not is_ulid(msg_id):
        out(f"WARN: envelope without valid id; dropping (id={msg_id!r})")
        return
    if seen_id_contains(msg_id):
        return  # already delivered — silent dedup
    recipient_name = (msg.get("to") or "").strip()
    if not recipient_name:
        return  # malformed; nothing to filter on
    # Transient recipients (today: `ASK_<ulid>` minted by `a8s ask`) bypass the
    # registry. The asker process registers a live transient dir; subscribers
    # check it before the registry filter and deliver into its private inbox
    # so a remote reply lands where the asker is polling.
    if transient_dirs.is_live(recipient_name):
        if msg.get("files"):
            msg = dict(msg)
            msg["files"] = []
        _deliver_to_transient(recipient_name, msg_id, msg)
        seen_id_append(msg_id)
        return
    by_name = {p.name.lower(): p for p in all_agents}
    try:
        kind, member_names = resolve_name(recipient_name)
    except (KeyError, ValueError):
        # Recipient name unknown locally — receive-side filter says "not
        # for me." Drop silently; logging every miss would be noisy on a
        # busy network.
        return
    recipients: list[Participant] = []
    for m in member_names:
        rp = by_name.get(m.lower())
        if rp is not None:
            # No sender exclusion here: the sender lives on a different
            # cluster and its name (if it happens to also be a local agent)
            # is the dual-name foot-gun. Per the design, deliver locally.
            recipients.append(rp)
    if not recipients:
        return  # alias resolved to nothing locally
    # v1 limitation: file payloads stay local-only — the FILE: paths point
    # at the sender's filesystem and would just produce errors on the
    # recipient side. Strip them before writing if any arrived via remote.
    if msg.get("files"):
        out(f"WARN: stripped FILE: payloads from incoming envelope id={msg_id}")
        msg = dict(msg)
        msg["files"] = []
    sender_label = msg.get("from") or "?"
    preview = _preview(msg.get("content", ""))
    for recipient in recipients:
        # ensure_mailboxes lives in mailbox.py; importing it here would form
        # a cycle (mailbox imports from registry; network imports from
        # mailbox would be fine but is unnecessary). Just create dirs.
        inbox_dir(recipient.name).mkdir(parents=True, exist_ok=True)
        inbox_tmp_dir(recipient.name).mkdir(parents=True, exist_ok=True)
        final = inbox_dir(recipient.name) / f"{msg_id}.json"
        if final.is_file():
            continue  # already there
        staging = inbox_tmp_dir(recipient.name) / f"{msg_id}.json"
        try:
            with staging.open("w", encoding="utf-8") as f:
                json.dump(msg, f, indent=2)
            os.replace(str(staging), str(final))
        except OSError as e:
            out_agent(recipient.name, f"WARN failed to write incoming envelope id={msg_id}: {e}")
            continue
        out_agent(recipient.name, f"received from {sender_label} (via remote): {preview}")
    seen_id_append(msg_id)


def make_receive_callback(
    get_participants: Callable[[], list[Participant]],
) -> OnMessage:
    """Wrap `receive_envelope` so the subscriber thread always passes the
    CURRENT participant list — agents added via `a8s add` after the
    subscriber started are picked up without restarting the loop."""

    def callback(envelope: bytes) -> None:
        try:
            receive_envelope(envelope, get_participants())
        except Exception as e:
            out(f"WARN: receive_envelope raised: {e}")

    return callback


# ---------- lifecycle ----------

def start_remotes(
    remotes: list[Transport],
    get_participants: Callable[[], list[Participant]],
) -> list[Transport]:
    """Start every remote's subscriber loop. A failure to start one remote
    logs a warning and continues with the others — no remote is allowed to
    block a8s startup. Returns the list of successfully-started remotes."""
    started: list[Transport] = []
    cb = make_receive_callback(get_participants)
    for r in remotes:
        try:
            r.start(cb)
            started.append(r)
            out(f"remote {r.id}: subscriber started")
        except Exception as e:
            out(f"WARN: remote {r.id} failed to start: {e}")
    return started


def stop_remotes(remotes: list[Transport]) -> None:
    for r in remotes:
        try:
            r.stop()
        except Exception as e:
            out(f"WARN: remote {r.id} stop raised: {e}")


# ---------- one-shot supervisor publish ----------

# How long to keep retrying the publish while transports finish their
# (possibly slow) initial handshake. TLS to a cloud broker can take 1–3s
# beyond the local-only case, so the retry budget needs to comfortably
# cover that. The inner per-attempt sleep is short so we exit promptly
# once at least one remote becomes ready.
_SUPERVISOR_PUBLISH_TIMEOUT_S = 30.0
_SUPERVISOR_PUBLISH_POLL_S = 0.25


def publish_once_to_remotes(msg: dict) -> tuple[list[str], list[str]]:
    """Synchronously publish one envelope to every configured remote and
    return (succeeded_ids, failed_ids).

    Used by supervisor commands (`a8s prompt`, `a8s clear`) when the
    recipient is unknown locally — those commands don't have a sender
    context to drop the message into an outbox, so they go through this
    direct path instead of the daemon's per-message backoff retry. No
    persistence: if every remote fails, the user retries the CLI command.

    Each transport is started, publish() is retried quietly while paho's
    background thread completes the (possibly slow) TLS handshake, then
    stop()'d. Unlike the daemon path, the retry here doesn't go through
    `make_publish_remotes` — that one warn-logs every attempt, which
    would be noisy during the normal handshake window. The first
    successful publish per remote ends its retry."""
    import time as _time

    remotes = load_remotes()
    if not remotes:
        return [], []
    started = start_remotes(remotes, lambda: [])
    envelope = json.dumps(msg).encode("utf-8")
    succeeded: list[str] = []
    last_errors: dict[str, str] = {}
    pending: list[Transport] = list(started)
    deadline = _time.time() + _SUPERVISOR_PUBLISH_TIMEOUT_S
    try:
        while pending and _time.time() < deadline:
            still_pending: list[Transport] = []
            for r in pending:
                try:
                    r.publish(envelope)
                    succeeded.append(r.id)
                except TransportError as e:
                    last_errors[r.id] = str(e)
                    still_pending.append(r)
                except Exception as e:
                    last_errors[r.id] = f"{type(e).__name__}: {e}"
                    still_pending.append(r)
            pending = still_pending
            if pending:
                _time.sleep(_SUPERVISOR_PUBLISH_POLL_S)
    finally:
        stop_remotes(started)
    failed = [r.id for r in started if r.id not in succeeded]
    for fid in failed:
        out(f"WARN remote {fid} publish failed: {last_errors.get(fid, 'unknown error')}")
    return succeeded, failed
