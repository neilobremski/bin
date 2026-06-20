"""tell — drop a JSON envelope in the nearest `.outbox/`.

Walks up from CWD for `.outbox/` and writes; falls back to `TELL_DEFAULT_DIR`
(walks up from that path too) when CWD has no outbox. No registry required.
`~/.a8s` is reachable and CWD sits inside a registered agent, validates the
recipient (with remote fallback), stamps `from`, and logs to the agent log.

`--sync` uses a file drop protocol with a8s: control envelopes to `!a8s`, reply
and ack files under `<agent-root>/.temp/` that both sides poll.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from core import _preview, out_agent
from mailbox import _split_content_and_files
from sync_listen import (
    DEFAULT_SYNC_TIMEOUT_SEC,
    build_cancel_envelope,
    build_listen_envelope,
    sync_paths,
)
from ulid import new as new_ulid

DEFAULT_SYNC_TIMEOUT = DEFAULT_SYNC_TIMEOUT_SEC
SYNC_ACK_TIMEOUT = 30.0
SYNC_POLL_INTERVAL = 0.25
TELL_DEFAULT_DIR_ENV = "TELL_DEFAULT_DIR"


def _outbox_at(root: Path) -> Path | None:
    candidate = root / ".outbox"
    return candidate if candidate.is_dir() else None


def _walk_outbox_from(start: Path) -> Path | None:
    cur = start.resolve()
    for d in (cur, *cur.parents):
        found = _outbox_at(d)
        if found is not None:
            return found
    return None


def find_outbox() -> Path | None:
    found = _walk_outbox_from(Path.cwd())
    if found is not None:
        return found
    default_dir = os.environ.get(TELL_DEFAULT_DIR_ENV, "").strip()
    if not default_dir:
        return None
    try:
        return _walk_outbox_from(Path(default_dir).expanduser())
    except OSError:
        return None


def agent_root_from_outbox(outbox: Path) -> Path:
    return outbox.parent.resolve()


def join_args(args: list[str]) -> str:
    parts: list[str] = []
    for a in args:
        if a.lstrip().startswith("FILE:"):
            parts.append("\n" + a.lstrip())
        else:
            if parts:
                parts.append(" ")
            parts.append(a)
    return "".join(parts).strip()


def parse_tell_argv(
    argv: list[str],
) -> tuple[str, list[str], list[str], bool, float]:
    """Return `(recipient, attachments, message_argv, sync, timeout_sec)`."""
    attachments: list[str] = []
    recipient: str | None = None
    message_argv: list[str] = []
    sync = False
    timeout = DEFAULT_SYNC_TIMEOUT
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--attach", "--file"):
            i += 1
            if i >= len(argv):
                raise TellUsageError("--attach requires a path")
            attachments.append(argv[i])
        elif arg == "--sync":
            sync = True
        elif arg == "--timeout":
            i += 1
            if i >= len(argv):
                raise TellUsageError("--timeout requires seconds")
            try:
                timeout = float(argv[i])
            except ValueError as e:
                raise TellUsageError(f"--timeout: {e}") from e
            if timeout <= 0:
                raise TellUsageError("--timeout must be positive")
        elif arg in ("-h", "--help"):
            raise TellHelp()
        elif recipient is None:
            if arg.startswith("-") and arg != "-":
                raise TellUsageError(f"unknown option: {arg}")
            recipient = arg
        else:
            message_argv.append(arg)
        i += 1
    if recipient is None:
        raise TellUsageError("recipient name required")
    return recipient, attachments, message_argv, sync, timeout


def resolve_message_body(message_argv: list[str]) -> str | None:
    if message_argv == ["-"]:
        if sys.stdin.isatty():
            return None
        return sys.stdin.read()
    if message_argv:
        return join_args(message_argv)
    if sys.stdin.isatty():
        return None
    data = sys.stdin.read()
    if not data:
        return None
    return data


def write_outbox_envelope(
    outbox: Path,
    to: str,
    content: str,
    files: list[dict],
    *,
    from_name: str | None = None,
    extra: dict | None = None,
) -> dict:
    msg_id = new_ulid()
    msg: dict = {
        "id": msg_id,
        "date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "to": to,
        "content": content,
        "files": files,
    }
    if from_name is not None:
        msg["from"] = from_name
    if extra:
        msg.update(extra)
    dest = outbox / f"{msg_id}.json"
    tmp = outbox / f".{msg_id}.tmp"
    tmp.write_text(json.dumps(msg, indent=2), encoding="utf-8")
    os.replace(tmp, dest)
    return msg


def write_outbox_control(
    outbox: Path,
    fields: dict,
    *,
    from_name: str | None = None,
) -> dict:
    return write_outbox_envelope(
        outbox,
        fields.get("to", ""),
        fields.get("content", ""),
        fields.get("files", []),
        from_name=from_name,
        extra={k: v for k, v in fields.items() if k not in ("to", "content", "files")},
    )


class TellUsageError(Exception):
    pass


class TellHelp(Exception):
    pass


_USAGE = (
    "usage: tell [--sync] [--timeout SEC] [--attach PATH] <name> [<message...>]"
)


def _print_usage() -> None:
    print(_USAGE, file=sys.stderr)
    print("       message may be `-` to read stdin; stdin is used when piped", file=sys.stderr)
    print("       --sync block until the recipient replies (default timeout 300s)", file=sys.stderr)


def _optional_sender() -> tuple[str, dict] | None:
    try:
        from registry import sender_from_cwd

        return sender_from_cwd()
    except OSError:
        return None


def _validate_recipient(target_query: str) -> tuple[int, str | None, str | None]:
    from network import configured_remote_ids
    from registry import load_aliases, resolve_name

    try:
        kind, members = resolve_name(target_query)
    except KeyError:
        if not configured_remote_ids():
            print(f"tell: no agent or alias named {target_query!r}", file=sys.stderr)
            return 1, None, None
        return 0, target_query, None
    except ValueError as e:
        print(f"tell: {e}", file=sys.stderr)
        return 1, None, None
    if not members:
        print(f"tell: {target_query!r} resolves to no agents", file=sys.stderr)
        return 1, None, None
    if kind == "agent":
        canonical = members[0]
    else:
        aliases = load_aliases()
        canonical = next(
            (k for k in aliases if k.lower() == target_query.lower()),
            target_query,
        )
    return 0, canonical, kind


def _poll_until(
    path: Path,
    deadline: float,
    *,
    interrupted: Callable[[], bool] | None = None,
) -> bool:
    while time.monotonic() < deadline:
        if interrupted is not None and interrupted():
            return False
        if path.is_file():
            return True
        time.sleep(SYNC_POLL_INTERVAL)
    return False


def _sync_rel_paths(agent_root: Path, session_id: str) -> dict[str, str]:
    paths = sync_paths(agent_root, session_id)
    return {
        "reply_path": paths["reply"].relative_to(agent_root).as_posix(),
        "listen_ack_path": paths["listen_ack"].relative_to(agent_root).as_posix(),
        "cancel_ack_path": paths["cancel_ack"].relative_to(agent_root).as_posix(),
    }


def _drop_sync_cancel(
    outbox: Path,
    session_id: str,
    cancel_ack_path: str,
    from_name: str | None,
) -> None:
    write_outbox_control(
        outbox,
        build_cancel_envelope(session_id, cancel_ack_path),
        from_name=from_name,
    )


def _run_sync(
    outbox: Path,
    agent_root: Path,
    to: str,
    expect_from: str,
    content: str,
    files: list[dict],
    from_name: str | None,
    timeout: float,
) -> int:
    session_id = new_ulid()
    paths = sync_paths(agent_root, session_id)
    paths["base"].mkdir(parents=True, exist_ok=True)
    rel = _sync_rel_paths(agent_root, session_id)

    write_outbox_envelope(outbox, to, content, files, from_name=from_name)
    write_outbox_control(
        outbox,
        build_listen_envelope(
            session_id,
            expect_from,
            rel["reply_path"],
            rel["listen_ack_path"],
            rel["cancel_ack_path"],
            timeout_sec=timeout,
        ),
        from_name=from_name,
    )

    session_started = True
    interrupted = {"flag": False}

    def _on_signal(_signum: int, _frame: object | None) -> None:
        interrupted["flag"] = True

    old_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _on_signal)
    is_interrupted: Callable[[], bool] = lambda: interrupted["flag"]

    exit_code = 1
    try:
        deadline = time.monotonic() + timeout
        listen_deadline = min(deadline, time.monotonic() + SYNC_ACK_TIMEOUT)
        if not _poll_until(paths["listen_ack"], listen_deadline, interrupted=is_interrupted):
            if is_interrupted():
                print("tell: sync interrupted", file=sys.stderr)
                exit_code = 130
            else:
                print("tell: sync listen not acknowledged by a8s", file=sys.stderr)
            return exit_code

        got_reply = _poll_until(paths["reply"], deadline, interrupted=is_interrupted)
        if is_interrupted():
            print("tell: sync interrupted", file=sys.stderr)
            return 130
        if not got_reply:
            print(f"tell: sync timed out after {timeout:g}s waiting for {expect_from!r}", file=sys.stderr)
            return 1

        try:
            reply = json.loads(paths["reply"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"tell: sync reply unreadable: {e}", file=sys.stderr)
            return 1

        body = reply.get("content", "")
        sys.stdout.write(body)
        if body and not body.endswith("\n"):
            sys.stdout.write("\n")
        exit_code = 0
        return exit_code
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        if session_started:
            _drop_sync_cancel(outbox, session_id, rel["cancel_ack_path"], from_name)
            _poll_until(
                paths["cancel_ack"],
                time.monotonic() + SYNC_ACK_TIMEOUT,
                interrupted=is_interrupted,
            )


def tell_main(argv: list[str]) -> int:
    try:
        recipient, attachments, message_argv, sync, timeout = parse_tell_argv(argv)
    except TellHelp:
        _print_usage()
        return 0
    except TellUsageError as e:
        print(f"tell: {e}", file=sys.stderr)
        _print_usage()
        return 2

    body = resolve_message_body(message_argv)
    if body is None:
        _print_usage()
        return 2

    content, files = _split_content_and_files(body)
    for path in attachments:
        files.append({"filename": Path(path).name, "path": path})

    outbox = find_outbox()
    if outbox is None:
        print("tell: cannot send from this directory", file=sys.stderr)
        return 1

    agent_root = agent_root_from_outbox(outbox)
    sender = _optional_sender()
    to = recipient
    kind: str | None = None
    if sender is not None:
        rc, canonical, kind = _validate_recipient(recipient)
        if rc != 0:
            return rc
        assert canonical is not None
        to = canonical

    if sync:
        rc = _run_sync(
            outbox,
            agent_root,
            to,
            to,
            content,
            files,
            sender[0] if sender is not None else None,
            timeout,
        )
        if rc == 0 and sender is not None:
            sender_name, _ = sender
            out_agent(sender_name, f"tell --sync -> {to}: reply received")
        return rc

    msg = write_outbox_envelope(
        outbox,
        to,
        content,
        files,
        from_name=sender[0] if sender is not None else None,
    )

    preview = content.replace("\n", " ")[:80]
    if len(content) > 80:
        preview += "..."
    print(f"tell -> {to}: {preview}")

    if sender is not None:
        sender_name, _ = sender
        if kind == "alias":
            from registry import resolve_name

            _, members = resolve_name(recipient)
            out_agent(
                sender_name,
                f"tell -> {to} (alias of {len(members)}): {_preview(content)}",
            )
        else:
            out_agent(sender_name, f"tell -> {to}: {_preview(content)}")

    return 0
