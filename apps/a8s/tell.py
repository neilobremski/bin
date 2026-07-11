"""tell — drop a JSON envelope in the outbox directory.

Requires `TELL_OUTBOX_DIR` (set by a8s on agent wake). No filesystem
discovery. `~/.a8s` reachable and CWD inside a registered agent validates the
recipient (with remote fallback), stamps `from`, and logs to the agent log.

Attachments: any path tell can read is copied into `.outbox/<msg_id>/` before
the envelope is written. The JSON `files` array carries basename only (no
`path` field). Ingest moves the bundle with the JSON; routing delivers into
`.files/<msg_id>/` on each recipient.

`--sync` uses a file drop protocol with a8s: control envelopes to `!a8s`, reply
and ack files under `<agent-root>/.temp/` that both sides poll.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from core import _preview, out_agent, outbox_bundle_dir, TELL_OUTBOX_DIR_ENV
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


def _probe_outbox_writable(outbox: Path) -> str | None:
    probe = outbox / f".tell-check-{os.getpid()}.tmp"
    try:
        probe.write_text("{}", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return str(e)
    return None


def _outbox_from_env() -> Path | None:
    raw = os.environ.get(TELL_OUTBOX_DIR_ENV, "").strip()
    if not raw:
        return None
    try:
        outbox = Path(raw).expanduser().resolve()
        outbox.mkdir(parents=True, exist_ok=True)
        if _probe_outbox_writable(outbox) is not None:
            return None
        return outbox
    except OSError:
        return None


def find_outbox() -> Path | None:
    return _outbox_from_env()


def _report_outbox_unavailable() -> None:
    print("tell: cannot send from this directory", file=sys.stderr)
    if os.environ.get(TELL_OUTBOX_DIR_ENV, "").strip():
        print(f"tell: {TELL_OUTBOX_DIR_ENV} is set but outbox is unavailable", file=sys.stderr)
    else:
        print(f"tell: {TELL_OUTBOX_DIR_ENV} is not set", file=sys.stderr)


def agent_root_from_outbox(outbox: Path) -> Path:
    return outbox.parent.resolve()


def _absolutize_file_path(path: str) -> str:
    p = Path(path).expanduser()
    if p.is_absolute():
        return str(p.resolve())
    return str((Path.cwd() / p).resolve())


def _normalize_file_entries(entries: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for entry in entries:
        raw = (entry.get("path") or "").strip()
        if not raw:
            normalized.append(dict(entry))
            continue
        abs_path = _absolutize_file_path(raw)
        normalized.append({"filename": Path(abs_path).name, "path": abs_path})
    return normalized


def _validate_attachment_sources(entries: list[dict]) -> int:
    if not entries:
        return 0
    for entry in entries:
        raw = (entry.get("path") or "").strip()
        if not raw:
            print("tell: attachment path required", file=sys.stderr)
            return 1
        try:
            resolved = Path(raw).resolve()
        except (OSError, RuntimeError) as e:
            print(f"tell: attachment path invalid: {raw}: {e}", file=sys.stderr)
            return 1
        if not resolved.is_file():
            print(f"tell: attachment not found: {resolved}", file=sys.stderr)
            return 1
    return 0


def stage_outbox_attachments(
    outbox: Path,
    msg_id: str,
    entries: list[dict],
) -> list[dict]:
    """Copy sources into `.outbox/<msg_id>/<basename>`; return filename-only
    envelope entries."""
    bundle = outbox_bundle_dir(outbox, msg_id)
    bundle.mkdir(parents=True, exist_ok=True)
    staged: list[dict] = []
    for entry in entries:
        src = Path((entry.get("path") or "").strip()).resolve()
        name = src.name
        dest = bundle / name
        tmp = bundle / f".{name}.tmp"
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
        staged.append({"filename": name})
    return staged


def stage_sender_attachment(
    outbox: Path,
    msg_id: str,
    source: Path | str,
) -> dict:
    """Stage one file for tests simulating outbox traffic."""
    src = Path(source).expanduser().resolve()
    return stage_outbox_attachments(outbox, msg_id, [{"path": str(src)}])[0]


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
) -> tuple[str | None, list[str], list[str], bool, float, bool]:
    """Return `(recipient, attachments, message_argv, sync, timeout_sec, check)`."""
    attachments: list[str] = []
    recipient: str | None = None
    message_argv: list[str] = []
    sync = False
    check = False
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
        elif arg == "--check":
            check = True
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
            recipient = arg
        else:
            message_argv.append(arg)
        i += 1
    return recipient, attachments, message_argv, sync, timeout, check


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
    msg_id: str | None = None,
) -> dict:
    envelope_id = msg_id or new_ulid()
    msg: dict = {
        "id": envelope_id,
        "date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "to": to,
        "content": content,
        "files": files,
    }
    if from_name is not None:
        msg["from"] = from_name
    if extra:
        msg.update(extra)
    dest = outbox / f"{envelope_id}.json"
    tmp = outbox / f".{envelope_id}.tmp"
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
    from registry import load_aliases, load_namespaces, resolve_name, split_namespace_address

    try:
        kind, members = resolve_name(target_query)
    except KeyError:
        if ":" not in target_query:
            ns_lookup = {k.lower(): k for k in load_namespaces()}
            key = target_query.strip().lower()
            if key in ns_lookup:
                prefix = ns_lookup[key]
                print(
                    f"tell: {target_query!r} is a namespace prefix, not a recipient; "
                    f"use tell {prefix}:<member> …",
                    file=sys.stderr,
                )
                return 1, None, None
        if not configured_remote_ids():
            if ":" in target_query:
                print(f"tell: no namespace bound for {target_query!r}", file=sys.stderr)
            else:
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
    elif kind == "namespace":
        prefix, sub = split_namespace_address(target_query)
        canonical = f"{prefix}:{sub}"
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
    *,
    msg_id: str,
) -> int:
    session_id = new_ulid()
    paths = sync_paths(agent_root, session_id)
    paths["base"].mkdir(parents=True, exist_ok=True)
    rel = _sync_rel_paths(agent_root, session_id)

    write_outbox_envelope(
        outbox, to, content, files, from_name=from_name, msg_id=msg_id,
    )
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


def run_check(recipient: str | None) -> int:
    outbox = find_outbox()
    if outbox is None:
        _report_outbox_unavailable()
        return 1

    lines = ["tell: ok", f"  outbox: {outbox}"]

    if recipient is not None:
        rc, canonical, kind = _validate_recipient(recipient)
        if rc != 0:
            return rc
        assert canonical is not None
        if kind == "alias":
            lines.append(f"  recipient {recipient!r}: ok (alias -> {canonical})")
        elif kind == "namespace":
            from registry import resolve_name

            _, members = resolve_name(recipient)
            lines.append(f"  recipient {recipient!r}: ok (namespace -> {members[0]})")
        else:
            lines.append(f"  recipient {recipient!r}: ok")

    for line in lines:
        print(line)
    return 0


def tell_main(argv: list[str]) -> int:
    try:
        recipient, attachments, message_argv, sync, timeout, check = parse_tell_argv(argv)
    except TellHelp:
        _print_usage()
        return 0
    except TellUsageError as e:
        print(f"tell: {e}", file=sys.stderr)
        _print_usage()
        return 2

    if check:
        if sync:
            print("tell: --check cannot be used with --sync", file=sys.stderr)
            return 2
        if attachments or message_argv:
            print("tell: --check does not accept a message or attachments", file=sys.stderr)
            return 2
        return run_check(recipient)

    if recipient is None:
        _print_usage()
        return 2

    body = resolve_message_body(message_argv)
    if body is None:
        _print_usage()
        return 2

    content, files = _split_content_and_files(body)
    for path in attachments:
        files.append({"filename": Path(path).name, "path": path})
    files = _normalize_file_entries(files)

    outbox = find_outbox()
    if outbox is None:
        _report_outbox_unavailable()
        return 1

    rc = _validate_attachment_sources(files)
    if rc != 0:
        return rc

    msg_id = new_ulid()
    try:
        staged_files = stage_outbox_attachments(outbox, msg_id, files) if files else []
    except OSError as e:
        print(f"tell: attachment staging failed: {e}", file=sys.stderr)
        return 1

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
            agent_root_from_outbox(outbox),
            to,
            to,
            content,
            staged_files,
            sender[0] if sender is not None else None,
            timeout,
            msg_id=msg_id,
        )
        if rc == 0 and sender is not None:
            sender_name, _ = sender
            out_agent(sender_name, f"tell --sync -> {to}: reply received")
        return rc

    msg = write_outbox_envelope(
        outbox,
        to,
        content,
        staged_files,
        from_name=sender[0] if sender is not None else None,
        msg_id=msg_id,
    )

    preview = _preview(content)
    line = f"tell -> {to}: {preview}"
    if sender is not None:
        sender_name, _ = sender
        if kind == "alias":
            from registry import resolve_name

            _, members = resolve_name(recipient)
            out_agent(
                sender_name,
                f"tell -> {to} (alias of {len(members)}): {preview}",
            )
        else:
            out_agent(sender_name, line)
    else:
        print(line)

    return 0
