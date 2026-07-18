"""tells — wait for the next inbound message to this node.

Receive-side complement of `tell`. The node is resolved from `TELL_OUTBOX_DIR`
exactly as `tell` resolves the sender: the file-proxy inbox is `.inbox` beside
the outbox. By default `tells` snapshots what is already there, then blocks up to
`--timeout` seconds (default 5) for new envelopes to land, prints each
(sender + body) to stdout, and exits 0. Nothing new within the timeout prints
one line to stderr and exits 1.

With `-f` / `--follow`, poll the inbox continuously and print each new message
as it arrives until interrupted (Ctrl+C).

Non-destructive: it observes new arrivals without consuming them, so it never
races a competing reader for `.inbox` files and repeated runs each wait from
their own baseline.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from tell import agent_root_from_outbox, find_outbox

DEFAULT_TIMEOUT_SEC = 5.0
POLL_INTERVAL_SEC = 0.1
INBOX_DIRNAME = ".inbox"


class TellsUsageError(Exception):
    pass


class TellsHelp(Exception):
    pass


_USAGE = "usage: tells [-f|--follow] [--timeout SEC]"


def _print_usage() -> None:
    print(_USAGE, file=sys.stderr)
    print("       wait up to SEC (default 5) for the next inbound message", file=sys.stderr)
    print("       -f runs until interrupted instead of timing out", file=sys.stderr)


@dataclass(frozen=True)
class TellsOptions:
    timeout: float | None
    follow: bool = False


def parse_tells_argv(argv: list[str]) -> TellsOptions:
    timeout: float | None = DEFAULT_TIMEOUT_SEC
    follow = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-f", "--follow"):
            follow = True
            i += 1
            continue
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
    if follow:
        timeout = None
    return TellsOptions(timeout=timeout, follow=follow)


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


def _poll_new_messages(inbox: Path, seen: set[str]) -> int:
    printed = 0
    for name in sorted(_json_names(inbox) - seen):
        msg = _read_envelope(inbox / name)
        if msg is None:
            continue
        _print_message(msg)
        seen.add(name)
        printed += 1
    return printed


def tells_main(argv: list[str]) -> int:
    try:
        opts = parse_tells_argv(argv)
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
    if opts.follow:
        try:
            while True:
                _poll_new_messages(inbox, seen)
                time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            return 0

    assert opts.timeout is not None
    deadline = time.monotonic() + opts.timeout
    printed_any = False
    while True:
        printed = _poll_new_messages(inbox, seen)
        if printed:
            printed_any = True
        elif printed_any:
            return 0
        if not printed_any and time.monotonic() >= deadline:
            print(f"tells: no message within {opts.timeout:g}s", file=sys.stderr)
            return 1
        time.sleep(POLL_INTERVAL_SEC)
