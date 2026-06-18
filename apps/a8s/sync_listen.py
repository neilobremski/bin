"""File-based sync listen protocol between `tell --sync` and a8s.

`tell` drops control envelopes to `!a8s` in `.outbox/`; a8s ingests them and
manages listeners under `~/.a8s/agents/<NAME>/sync-listeners.json`. Matching
replies are written to agent-root paths (typically `.temp/<session>.reply.json`)
that `tell` polls — the only channel guaranteed to work across containers and
file-proxy mounts.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import Participant, out_agent
from registry import resolve_name


SYNC_TEMP_DIR = ".temp"
A8S_CONTROL = "!a8s"
DEFAULT_SYNC_TIMEOUT_SEC = 300.0


def sync_paths(agent_root: Path, session_id: str) -> dict[str, Path]:
    base = agent_root / SYNC_TEMP_DIR
    return {
        "base": base,
        "reply": base / f"{session_id}.reply.json",
        "listen_ack": base / f"{session_id}.listen.ack",
        "cancel_ack": base / f"{session_id}.cancel.ack",
    }


def _listeners_path(agent_name: str) -> Path:
    from core import agent_dir

    return agent_dir(agent_name) / "sync-listeners.json"


def _load_listeners(agent_name: str) -> list[dict]:
    p = _listeners_path(agent_name)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("listeners"), list):
        return data["listeners"]
    return []


def _save_listeners(agent_name: str, listeners: list[dict]) -> None:
    p = _listeners_path(agent_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"listeners": listeners}, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _resolve_under_root(root: Path, rel_path: str) -> Path | None:
    rel = rel_path.strip()
    if not rel or rel.startswith("/"):
        return None
    root_resolved = root.resolve()
    try:
        dest = (root_resolved / rel).resolve()
        dest.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    return dest


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _atomic_touch_ack(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"ok": True, "date": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _expect_from_names(expect_from: str) -> set[str]:
    try:
        kind, members = resolve_name(expect_from)
    except (KeyError, ValueError):
        return {expect_from.strip().lower()} if expect_from.strip() else set()
    if kind == "agent":
        return {members[0].lower()}
    return {m.lower() for m in members}


def _matches_listener(msg: dict, listener: dict) -> bool:
    sender = (msg.get("from") or "").strip().lower()
    if not sender:
        return False
    expected = listener.get("expect_from_names") or []
    return sender in {n.lower() for n in expected}


def build_listen_envelope(
    session_id: str,
    expect_from: str,
    reply_path: str,
    listen_ack_path: str,
    cancel_ack_path: str,
    *,
    timeout_sec: float = DEFAULT_SYNC_TIMEOUT_SEC,
) -> dict:
    return {
        "to": A8S_CONTROL,
        "command": "sync_listen",
        "args": {
            "session_id": session_id,
            "expect_from": expect_from,
            "reply_path": reply_path,
            "listen_ack_path": listen_ack_path,
            "cancel_ack_path": cancel_ack_path,
            "timeout_sec": timeout_sec,
        },
    }


def build_cancel_envelope(session_id: str, cancel_ack_path: str) -> dict:
    return {
        "to": A8S_CONTROL,
        "command": "sync_cancel",
        "args": {
            "session_id": session_id,
            "cancel_ack_path": cancel_ack_path,
        },
    }


def _parse_iso_z(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _listener_expired(listener: dict, now: datetime) -> bool:
    expires = _parse_iso_z(str(listener.get("expires_at") or ""))
    if expires is not None:
        return now >= expires
    created = _parse_iso_z(str(listener.get("created") or ""))
    if created is None:
        return True
    fallback = float(listener.get("timeout_sec") or DEFAULT_SYNC_TIMEOUT_SEC)
    return now >= created + timedelta(seconds=fallback)


def expire_stale_listeners(participant: Participant) -> int:
    """Drop listeners past `expires_at`. Returns count removed."""
    now = datetime.now(timezone.utc)
    listeners = _load_listeners(participant.name)
    kept: list[dict] = []
    removed = 0
    for listener in listeners:
        if _listener_expired(listener, now):
            removed += 1
            out_agent(
                participant.name,
                f"sync: expired listener {listener.get('session_id', '?')} "
                f"(expect {listener.get('expect_from', '?')!r})",
            )
        else:
            kept.append(listener)
    if removed:
        _save_listeners(participant.name, kept)
    return removed


def expire_stale_listeners_for_participants(participants: list[Participant]) -> int:
    total = 0
    for p in participants:
        if _listeners_path(p.name).is_file():
            total += expire_stale_listeners(p)
    return total


def handle_a8s_command(sender: Participant, msg: dict) -> bool:
    command = (msg.get("command") or "").strip()
    args = msg.get("args")
    if not isinstance(args, dict):
        out_agent(sender.name, f"sync: {command or '?'} missing args in {msg.get('id', '?')}")
        return False
    if command == "sync_listen":
        return _handle_sync_listen(sender, args)
    if command == "sync_cancel":
        return _handle_sync_cancel(sender, args)
    out_agent(sender.name, f"sync: unknown command {command!r}")
    return False


def _handle_sync_listen(sender: Participant, args: dict) -> bool:
    session_id = (args.get("session_id") or "").strip()
    expect_from = (args.get("expect_from") or "").strip()
    reply_path = (args.get("reply_path") or "").strip()
    listen_ack_path = (args.get("listen_ack_path") or "").strip()
    cancel_ack_path = (args.get("cancel_ack_path") or "").strip()
    if not all([session_id, expect_from, reply_path, listen_ack_path, cancel_ack_path]):
        out_agent(sender.name, "sync_listen: missing required args")
        return False

    reply_dest = _resolve_under_root(sender.root, reply_path)
    listen_ack_dest = _resolve_under_root(sender.root, listen_ack_path)
    cancel_ack_dest = _resolve_under_root(sender.root, cancel_ack_path)
    if reply_dest is None or listen_ack_dest is None or cancel_ack_dest is None:
        out_agent(sender.name, "sync_listen: path must be relative inside agent root")
        return False

    try:
        timeout_sec = float(args.get("timeout_sec", DEFAULT_SYNC_TIMEOUT_SEC))
    except (TypeError, ValueError):
        out_agent(sender.name, "sync_listen: invalid timeout_sec")
        return False
    if timeout_sec <= 0:
        out_agent(sender.name, "sync_listen: timeout_sec must be positive")
        return False

    expire_stale_listeners(sender)

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=timeout_sec)).isoformat().replace("+00:00", "Z")

    listeners = _load_listeners(sender.name)
    listeners = [l for l in listeners if l.get("session_id") != session_id]
    listener = {
        "session_id": session_id,
        "expect_from": expect_from,
        "expect_from_names": sorted(_expect_from_names(expect_from)),
        "reply_path": reply_path,
        "cancel_ack_path": cancel_ack_path,
        "timeout_sec": timeout_sec,
        "created": now.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at,
    }
    listeners.append(listener)
    _save_listeners(sender.name, listeners)
    out_agent(
        sender.name,
        f"sync_listen: waiting for reply from {expect_from!r} -> {reply_path} "
        f"(expires {expires_at})",
    )

    captured = scan_inbox_for_listeners(sender)
    if captured:
        out_agent(sender.name, f"sync_listen: captured {captured} pending inbox message(s)")

    _atomic_touch_ack(listen_ack_dest)
    return True


def _handle_sync_cancel(sender: Participant, args: dict) -> bool:
    session_id = (args.get("session_id") or "").strip()
    cancel_ack_path = (args.get("cancel_ack_path") or "").strip()
    if not session_id or not cancel_ack_path:
        out_agent(sender.name, "sync_cancel: missing required args")
        return False
    cancel_ack_dest = _resolve_under_root(sender.root, cancel_ack_path)
    if cancel_ack_dest is None:
        out_agent(sender.name, "sync_cancel: ack path outside agent root")
        return False

    before = len(_load_listeners(sender.name))
    listeners = [l for l in _load_listeners(sender.name) if l.get("session_id") != session_id]
    _save_listeners(sender.name, listeners)
    removed = before - len(listeners)
    out_agent(sender.name, f"sync_cancel: session {session_id} removed ({removed} listener(s))")
    _atomic_touch_ack(cancel_ack_dest)
    return True


def try_sync_capture(recipient: Participant, msg: dict) -> bool:
    """If `msg` satisfies an active listener, write it to the reply path."""
    expire_stale_listeners(recipient)
    listeners = _load_listeners(recipient.name)
    if not listeners:
        return False
    for listener in list(listeners):
        if not _matches_listener(msg, listener):
            continue
        reply_dest = _resolve_under_root(recipient.root, listener.get("reply_path", ""))
        if reply_dest is None:
            out_agent(recipient.name, f"sync: reply path rejected for {listener.get('session_id')}")
            continue
        _atomic_write_json(reply_dest, msg)
        remaining = [l for l in listeners if l.get("session_id") != listener.get("session_id")]
        _save_listeners(recipient.name, remaining)
        out_agent(
            recipient.name,
            f"sync: captured reply from {msg.get('from', '?')} -> {listener.get('reply_path')}",
        )
        return True
    return False


def scan_inbox_for_listeners(participant: Participant) -> int:
    from mailbox import _inbox_json_files, trash_dir, unique_path

    captured = 0
    for path in list(_inbox_json_files(participant)):
        try:
            msg = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if try_sync_capture(participant, msg):
            dest = unique_path(trash_dir(participant.name) / path.name)
            try:
                path.rename(dest)
            except OSError:
                try:
                    path.unlink()
                except OSError:
                    pass
            captured += 1
    return captured
