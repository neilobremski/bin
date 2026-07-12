"""tell — drop a JSON envelope in the outbox directory.

Requires `TELL_OUTBOX_DIR` (set by a8s on agent wake). No filesystem
discovery. `~/.a8s` reachable and CWD inside a registered agent validates the
recipient (with remote fallback), stamps `from`, and logs to the agent log.

Attachments: any path tell can read is copied into `.outbox/<msg_id>/` before
the envelope is written. The JSON `files` array carries basename only (no
`path` field). Ingest moves the bundle with the JSON; routing delivers into
`.files/<msg_id>/` on each recipient.

Waiting for a reply is the receive-side complement `tells` (see `tells.py`).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from core import _preview, out_agent, outbox_bundle_dir, TELL_OUTBOX_DIR_ENV
from mailbox import _split_content_and_files
from ulid import new as new_ulid


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
) -> tuple[str | None, list[str], list[str], bool]:
    """Return `(recipient, attachments, message_argv, check)`."""
    attachments: list[str] = []
    recipient: str | None = None
    message_argv: list[str] = []
    check = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--attach", "--file"):
            i += 1
            if i >= len(argv):
                raise TellUsageError("--attach requires a path")
            attachments.append(argv[i])
        elif arg == "--check":
            check = True
        elif arg in ("-h", "--help"):
            raise TellHelp()
        elif recipient is None:
            recipient = arg
        else:
            message_argv.append(arg)
        i += 1
    return recipient, attachments, message_argv, check


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


class TellUsageError(Exception):
    pass


class TellHelp(Exception):
    pass


_USAGE = "usage: tell [--attach PATH] <name> [<message...>]"


def _print_usage() -> None:
    print(_USAGE, file=sys.stderr)
    print("       message may be `-` to read stdin; stdin is used when piped", file=sys.stderr)


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
        split = split_namespace_address(target_query)
        if split is not None:
            prefix, sub = split
            canonical = f"{prefix}:{sub}"
        else:
            canonical = next(
                k for k in load_namespaces()
                if k.lower() == target_query.strip().lower()
            )
    else:
        aliases = load_aliases()
        canonical = next(
            (k for k in aliases if k.lower() == target_query.lower()),
            target_query,
        )
    return 0, canonical, kind


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
        recipient, attachments, message_argv, check = parse_tell_argv(argv)
    except TellHelp:
        _print_usage()
        return 0
    except TellUsageError as e:
        print(f"tell: {e}", file=sys.stderr)
        _print_usage()
        return 2

    if check:
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
