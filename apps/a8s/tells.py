"""tells — wait for the next inbound message to this node.

Receive-side complement of `tell`. The node is resolved from `TELL_OUTBOX_DIR`
exactly as `tell` resolves the sender: the file-proxy inbox is `.inbox` beside
the outbox. `tells` snapshots what is already there, then blocks up to
`--timeout` seconds (default 5) for new envelopes to land, prints each
(sender + body) to stdout, and exits 0. Nothing new within the timeout prints
one line to stderr and exits 1.

Non-destructive: it observes new arrivals without consuming them, so it never
races a competing reader for `.inbox` files and repeated runs each wait from
their own baseline.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from tell import agent_root_from_outbox, find_outbox

DEFAULT_TIMEOUT_SEC = 5.0
POLL_INTERVAL_SEC = 0.1
INBOX_DIRNAME = ".inbox"


class TellsUsageError(Exception):
    pass


class TellsHelp(Exception):
    pass


_USAGE = "usage: tells [--timeout SEC]"


def _print_usage() -> None:
    print(_USAGE, file=sys.stderr)
    print("       wait up to SEC (default 5) for the next inbound message", file=sys.stderr)


def parse_tells_argv(argv: list[str]) -> float:
    """Return the timeout in seconds."""
    timeout = DEFAULT_TIMEOUT_SEC
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--timeout":
            i += 1
            if i >= len(argv):
                raise TellsUsageError("--timeout requires seconds")
            try:
                timeout = float(argv[i])
            except ValueError as e:
                raise TellsUsageError(f"--timeout: {e}") from e
            if timeout <= 0:
                raise TellsUsageError("--timeout must be positive")
        elif arg in ("-h", "--help"):
            raise TellsHelp()
        else:
            raise TellsUsageError(f"unexpected argument: {arg!r}")
        i += 1
    return timeout


def inbox_from_env() -> Path | None:
    outbox = find_outbox()
    if outbox is None:
        return None
    return agent_root_from_outbox(outbox) / INBOX_DIRNAME


def _json_names(inbox: Path) -> set[str]:
    if not inbox.is_dir():
        return set()
    return {p.name for p in inbox.iterdir() if p.is_file() and p.name.endswith(".json")}


def _read_envelope(path: Path) -> dict | None:
    try:
        msg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return msg if isinstance(msg, dict) else None


def _print_message(msg: dict) -> None:
    sender = msg.get("from") or "?"
    print(f"{sender}: {msg.get('content', '')}")


def tells_main(argv: list[str]) -> int:
    try:
        timeout = parse_tells_argv(argv)
    except TellsHelp:
        _print_usage()
        return 0
    except TellsUsageError as e:
        print(f"tells: {e}", file=sys.stderr)
        _print_usage()
        return 2

    inbox = inbox_from_env()
    if inbox is None:
        print("tells: cannot receive from this directory", file=sys.stderr)
        return 1

    seen = _json_names(inbox)
    deadline = time.monotonic() + timeout
    while True:
        printed = 0
        for name in sorted(_json_names(inbox) - seen):
            msg = _read_envelope(inbox / name)
            if msg is None:
                continue
            _print_message(msg)
            seen.add(name)
            printed += 1
        if printed:
            return 0
        if time.monotonic() >= deadline:
            print(f"tells: no message within {timeout:g}s", file=sys.stderr)
            return 1
        time.sleep(POLL_INTERVAL_SEC)
