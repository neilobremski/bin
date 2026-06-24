"""a8s remote routing — config, publish-with-backoff, receive loop, dedup.

a8s only crosses cluster boundaries on outbound `tell` messages — every
message has a force-stamped agent `from`, no senderless channel exists.
State queries (`logs`, `ls`, `agents`) are strictly local.
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
    Participant,
    _preview,
    inbox_dir,
    inbox_tmp_dir,
    network_config_path,
    out,
    out_agent,
    seen_ids_path,
)
from registry import resolve_name
from services import StorageService
from transports import OnMessage, Transport, TransportError
import txlog
from ulid import is_ulid


# Process-local lock guarding the seen-ids ring rotation. Multiple subscriber
# threads (one per remote) call seen_id_append concurrently; the append
# itself is atomic per POSIX, but the truncate-after-rotate is not.
_SEEN_IDS_LOCK = threading.Lock()


# ---------- network.json ----------

def load_network_config() -> dict:
    p = network_config_path()
    if not p.is_file():
        return {"remotes": {}, "services": {}}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        out(f"WARN: ~/.a8s/network.json malformed ({e}); treating as empty")
        return {"remotes": {}, "services": {}}
    if not isinstance(data, dict):
        return {"remotes": {}, "services": {}}
    data.setdefault("remotes", {})
    if not isinstance(data["remotes"], dict):
        data["remotes"] = {}
    data.setdefault("services", {})
    if not isinstance(data["services"], dict):
        data["services"] = {}
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


# ---------- storage services (#90) ----------

# Top-level keys in a network.json `services` entry that the dispatcher
# consumes itself before forwarding the rest to the StorageService constructor.
_RESERVED_SERVICE_SPEC_KEYS = {"service", "url"}


def _build_service(name: str, spec: dict) -> StorageService:
    """Instantiate one StorageService from a network.json `services` entry.

    The persisted `service` field is the canonical kind name (e.g.
    `tempfile_org`). The dispatcher imports each known service class
    lazily so the import graph stays empty for installs without storage
    configured. Any keys past `service` and `url` are forwarded as
    `**opts`; each service class handles its own option vocabulary
    and rejects unknowns at construction time."""
    kind = (spec.get("service") or "").strip().lower()
    url = spec.get("url")
    if not url:
        raise ValueError(f"storage {name!r}: every service requires `url`")
    opts = {k: v for k, v in spec.items() if k not in _RESERVED_SERVICE_SPEC_KEYS}
    if kind == "tempfile_org":
        # Lazy import — keeps the storage modules out of the import graph
        # for users without storage configured.
        from services.tempfile_org import TempFileOrgService

        return TempFileOrgService(name, url=url, **opts)
    raise ValueError(f"storage {name!r}: unsupported service kind {kind!r}")


def load_services() -> list[StorageService]:
    """Return StorageService instances for every entry in
    `network.json`'s `services` map. Failures (bad config, missing
    module) are logged and skipped — never block a8s startup."""
    cfg = load_network_config()
    out_list: list[StorageService] = []
    for name, spec in cfg["services"].items():
        if not isinstance(spec, dict):
            out(f"WARN: storage {name!r} config is not an object; skipping")
            continue
        try:
            out_list.append(_build_service(name, spec))
        except Exception as e:
            out(f"WARN: storage {name!r} skipped: {e}")
    return out_list


def configured_service_ids() -> list[str]:
    """Just the ordered list of service IDs from network.json. Used by the
    routing pass to know which services need uploads before remote publish
    can finalize."""
    return list(load_network_config()["services"].keys())


def detect_service_kind(url: str) -> str | None:
    """Find the canonical service kind for an operator-typed URL by asking
    each known StorageService subclass `supports_config_url`. Returns the
    canonical kind string (e.g. `tempfile_org`) or None if no service
    accepted the URL. Used by the `a8s storage` CLI to persist the right
    `service` field at config-write time."""
    # Lazy imports keep the storage modules out of the import graph for
    # installs without storage configured.
    from services.tempfile_org import TempFileOrgService

    for kind, cls in (("tempfile_org", TempFileOrgService),):
        try:
            if cls.supports_config_url(url):
                return kind
        except Exception:
            continue
    return None


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


def _max_seen_ids() -> int:
    from settings import get_int

    return get_int("max_seen_ids")


def seen_id_append(ulid: str) -> None:
    """Append a ULID to the ring, rotating to the last max_seen_ids entries
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
        if len(lines) > _max_seen_ids():
            tmp = p.with_suffix(p.suffix + ".tmp")
            try:
                with tmp.open("w", encoding="utf-8") as out_f:
                    for u in lines[-_max_seen_ids():]:
                        out_f.write(u + "\n")
                os.replace(str(tmp), str(p))
            except OSError:
                pass


# ---------- send (publish_with_backoff) ----------

def make_publish_remotes(remotes: list[Transport]) -> Callable:
    """Build the `publish_remotes` callable that `route_outboxes` invokes.
    For each not-yet-succeeded remote, attempts a publish; on success logs to
    the sender's per-agent log (and stdout under `a8s run`); on failure logs
    a warning and leaves the remote in the `pending_remotes` set for the next
    pass. Returns the updated `succeeded_remotes` list."""

    def publish_with_backoff(
        msg: dict,
        sender_name: str,
        succeeded_so_far: list[str],
        attempt_count: int,
    ) -> list[str]:
        envelope = json.dumps(msg).encode("utf-8")
        recipient = (msg.get("to") or "").strip() or "?"
        preview = _preview(msg.get("content", ""))
        succeeded = list(succeeded_so_far)
        for remote in remotes:
            if remote.id in succeeded:
                continue
            try:
                remote.publish(envelope)
                succeeded.append(remote.id)
                out_agent(
                    sender_name,
                    f"remote {remote.id}: published -> {recipient}: {preview}",
                )
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

def receive_envelope(
    envelope: bytes,
    all_agents: list[Participant],
    services: list[StorageService] | None = None,
) -> None:
    """Decode an incoming envelope, dedupe, filter against the local
    registry, and atomically write into each matched local recipient's
    inbox. Drops silently if the recipient isn't ours, the envelope is
    malformed, or the ULID has been seen before — nothing should crash the
    subscriber thread.

    `services`: configured storage services (#90). When set and the
    envelope's `files[i].storage` URLs point at a service we know, the
    helper downloads each file into the recipient's `<root>/.files/` and
    rewrites the entry to local `{filename, path}` shape. None / empty
    falls back to the v1 limitation (strip files; log warning)."""
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
    # File payloads (#90): when storage services are configured, download
    # each file's bytes into the recipient's `.files/` and rewrite the
    # envelope entry to local-path shape. Without storage services
    # configured, fall back to the v1 limitation (strip + warn).
    raw_files = msg.get("files") or []
    files_have_storage = any((isinstance(e, dict) and e.get("storage")) for e in raw_files)
    if raw_files and (not services or not files_have_storage):
        out(f"WARN: stripped FILE: payloads from incoming envelope id={msg_id}")
        msg = dict(msg)
        msg["files"] = []
    sender_label = msg.get("from") or "?"
    preview = _preview(msg.get("content", ""))
    delivered_names: list[str] = []
    for recipient in recipients:
        # Per-recipient download: each recipient has its own `.files/`, so
        # the bytes land in the right place even on alias fan-out. Imported
        # lazily — `mailbox` imports `network`, so a top-level import here
        # would form an import cycle.
        msg_for_recipient = msg
        if services and files_have_storage:
            from mailbox import _download_files_to_recipient

            msg_for_recipient = _download_files_to_recipient(msg, recipient, services)
        from sync_listen import try_sync_capture

        if try_sync_capture(recipient, msg_for_recipient):
            out_agent(
                recipient.name,
                f"sync: captured remote reply from {sender_label}: {preview}",
            )
            file_names = [
                e.get("filename", "")
                for e in (msg_for_recipient.get("files") or [])
                if e.get("filename")
            ]
            txlog.log(
                "RECEIVED_REMOTE",
                msg_id=msg_id,
                sender=sender_label,
                recipient=recipient.name,
                files=file_names or None,
                remote="remote",
                detail=f"sync capture: {preview}",
            )
            delivered_names.append(recipient.name)
            continue
        # ensure_mailboxes lives in mailbox.py; importing it here would form
        # a cycle. Just create dirs.
        inbox_dir(recipient.name).mkdir(parents=True, exist_ok=True)
        inbox_tmp_dir(recipient.name).mkdir(parents=True, exist_ok=True)
        final = inbox_dir(recipient.name) / f"{msg_id}.json"
        if final.is_file():
            delivered_names.append(recipient.name)
            continue
        staging = inbox_tmp_dir(recipient.name) / f"{msg_id}.json"
        try:
            with staging.open("w", encoding="utf-8") as f:
                json.dump(msg_for_recipient, f, indent=2)
            os.replace(str(staging), str(final))
        except OSError as e:
            out_agent(recipient.name, f"WARN failed to write incoming envelope id={msg_id}: {e}")
            continue
        out_agent(recipient.name, f"received from {sender_label} (via remote): {preview}")
        file_names = [e.get("filename", "") for e in (msg_for_recipient.get("files") or []) if e.get("filename")]
        txlog.log("RECEIVED_REMOTE", msg_id=msg_id, sender=sender_label, recipient=recipient.name, files=file_names or None, remote="remote", detail=preview)
        delivered_names.append(recipient.name)
    if delivered_names:
        import convo

        convo.record(msg, recipients=delivered_names)
    seen_id_append(msg_id)


def make_receive_callback(
    get_participants: Callable[[], list[Participant]],
    services: list[StorageService] | None = None,
) -> OnMessage:
    """Wrap `receive_envelope` so the subscriber thread always passes the
    CURRENT participant list — agents added via `a8s add` after the
    subscriber started are picked up without restarting the loop. Storage
    services (#90) are passed in once at startup; the receive helper uses
    them to download cross-cluster `FILE:` payloads."""

    def callback(envelope: bytes) -> None:
        try:
            receive_envelope(envelope, get_participants(), services=services)
        except Exception as e:
            out(f"WARN: receive_envelope raised: {e}")

    return callback


# ---------- lifecycle ----------

def start_remotes(
    remotes: list[Transport],
    get_participants: Callable[[], list[Participant]],
    services: list[StorageService] | None = None,
) -> list[Transport]:
    """Start every remote's subscriber loop. A failure to start one remote
    logs a warning and continues with the others — no remote is allowed to
    block a8s startup. Returns the list of successfully-started remotes.

    `services` is passed through to the receive callback so cross-cluster
    `FILE:` payloads (#90) can be downloaded into each recipient's
    `.files/` as envelopes arrive. None / empty preserves pre-#90
    behavior (incoming files are stripped + warned)."""
    started: list[Transport] = []
    cb = make_receive_callback(get_participants, services=services)
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


