"""tells — wait for the next inbound message to this node.

Receive-side complement of `tell`. The node is resolved from `TELL_OUTBOX_DIR`
exactly as `tell` resolves the sender: the file-proxy inbox is `.inbox` beside
the outbox. By default `tells` snapshots what is already there, then blocks up to
`--timeout` seconds (default 5) for new envelopes to land, prints each
(sender + body) to stdout, and exits 0. Nothing new within the timeout prints
one line to stderr and exits 1.

With `-f` / `--follow` or `--timeout 0`, poll the inbox continuously and print
each new message as it arrives until interrupted (Ctrl+C). An explicit
`--timeout` greater than zero follows for that many seconds; `-f` cannot be
combined with a positive `--timeout`.

`--glow [theme]` and `--heading-out` / `--heading-in` reuse convo's markdown
formatting (and optional GlowStream rendering). Plain `sender: body` remains
the default when those options are omitted.

Non-destructive: it observes new arrivals without consuming them, so it never
races a competing reader for `.inbox` files and repeated runs each wait from
their own baseline.
"""
from __future__ import annotations

import json
import os
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


_USAGE = (
    "usage: tells [-f|--follow] [--timeout SEC] [--glow [THEME]] "
    "[--heading-out LINE ...] [--heading-in LINE ...]"
)


def _print_usage() -> None:
    from convo import convo_help_epilog

    print(_USAGE, file=sys.stderr)
    print("       default: wait up to 5s for the next message burst, then exit", file=sys.stderr)
    print("       --timeout SEC: follow the inbox for SEC seconds (0 = until Ctrl+C)", file=sys.stderr)
    print("       -f: same as --timeout 0 (cannot combine with positive --timeout)", file=sys.stderr)
    print("       --glow [THEME]: render markdown via glow (default theme from A8S_GLOW)", file=sys.stderr)
    print(convo_help_epilog(), file=sys.stderr)


def _argv_looks_like_option(arg: str) -> bool:
    return arg.startswith("-") and arg != "-"


@dataclass(frozen=True)
class TellsOptions:
    timeout: float
    follow: bool = False
    timeout_explicit: bool = False
    glow_theme: str | None = None
    heading_out: str | None = None
    heading_in: str | None = None

    @property
    def follow_forever(self) -> bool:
        return self.follow or (self.timeout_explicit and self.timeout == 0)

    @property
    def markdown(self) -> bool:
        return (
            self.glow_theme is not None
            or self.heading_out is not None
            or self.heading_in is not None
        )


def parse_tells_argv(argv: list[str]) -> TellsOptions:
    from convo import extract_heading_templates

    try:
        rest, heading_out, heading_in = extract_heading_templates(argv)
    except ValueError as e:
        raise TellsUsageError(str(e)) from e

    timeout = DEFAULT_TIMEOUT_SEC
    follow = False
    timeout_explicit = False
    default_glow = os.environ.get("A8S_GLOW", "").strip() or None
    glow_theme = default_glow
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("-f", "--follow"):
            follow = True
            i += 1
            continue
        if arg == "--timeout":
            i += 1
            if i >= len(rest):
                raise TellsUsageError("--timeout requires seconds")
            try:
                timeout = float(rest[i])
            except ValueError as e:
                raise TellsUsageError(f"--timeout: {e}") from e
            if timeout < 0:
                raise TellsUsageError("--timeout must be zero or positive")
            timeout_explicit = True
        elif arg == "--glow":
            if i + 1 < len(rest) and not _argv_looks_like_option(rest[i + 1]):
                i += 1
                glow_theme = rest[i]
            else:
                glow_theme = "auto"
        elif arg in ("-h", "--help"):
            raise TellsHelp()
        else:
            raise TellsUsageError(f"unexpected argument: {arg!r}")
        i += 1
    opts = TellsOptions(
        timeout=timeout,
        follow=follow,
        timeout_explicit=timeout_explicit,
        glow_theme=glow_theme,
        heading_out=heading_out,
        heading_in=heading_in,
    )
    if follow and timeout_explicit and timeout != 0:
        raise TellsUsageError("cannot use -f/--follow with a positive --timeout")
    return opts


def inbox_from_env() -> Path | None:
    outbox = find_outbox()
    if outbox is None:
        return None
    return agent_root_from_outbox(outbox) / INBOX_DIRNAME


def _agent_name_for_outbox(outbox: Path) -> str | None:
    try:
        from registry import participants_from_registry

        target = outbox.resolve()
        for p in participants_from_registry():
            try:
                if p.outbox_path().resolve() == target:
                    return p.name
            except (OSError, RuntimeError):
                continue
    except OSError:
        return None
    return None


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


def _print_plain(msg: dict) -> None:
    sender = msg.get("from") or "?"
    print(f"{sender}: {msg.get('content', '')}")


def _print_markdown(
    msg: dict,
    *,
    agent: str,
    glow_stream: object | None,
    heading_out: str,
    heading_in: str,
) -> None:
    from convo import entry_from_message, print_entries

    entry = entry_from_message(msg, recipients=[agent])
    print_entries(
        agent,
        [entry],
        glow_stream=glow_stream,
        heading_out=heading_out,
        heading_in=heading_in,
    )


def _poll_new_messages(
    inbox: Path,
    seen: set[str],
    *,
    agent: str,
    markdown: bool,
    glow_stream: object | None,
    heading_out: str,
    heading_in: str,
) -> int:
    printed = 0
    for name in sorted(_json_names(inbox) - seen):
        msg = _read_envelope(inbox / name)
        if msg is None:
            continue
        if markdown:
            local = agent or (msg.get("to") or "").strip() or "me"
            _print_markdown(
                msg,
                agent=local,
                glow_stream=glow_stream,
                heading_out=heading_out,
                heading_in=heading_in,
            )
        else:
            _print_plain(msg)
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

    from convo import DEFAULT_HEADING_IN, DEFAULT_HEADING_OUT, open_glow_stdout

    inbox = inbox_from_env()
    if inbox is None:
        print("tells: cannot receive from this directory", file=sys.stderr)
        return 1

    outbox = find_outbox()
    agent = _agent_name_for_outbox(outbox) if outbox is not None else None
    heading_out = opts.heading_out if opts.heading_out is not None else DEFAULT_HEADING_OUT
    heading_in = opts.heading_in if opts.heading_in is not None else DEFAULT_HEADING_IN
    glow_stream = None
    if opts.glow_theme is not None:
        try:
            glow_stream = open_glow_stdout(opts.glow_theme)
        except FileNotFoundError:
            print("tells: glow not found on PATH", file=sys.stderr)

    poll_kwargs = {
        "agent": agent or "",
        "markdown": opts.markdown,
        "glow_stream": glow_stream,
        "heading_out": heading_out,
        "heading_in": heading_in,
    }

    try:
        seen = _json_names(inbox)
        if opts.follow_forever:
            try:
                while True:
                    _poll_new_messages(inbox, seen, **poll_kwargs)
                    time.sleep(POLL_INTERVAL_SEC)
            except KeyboardInterrupt:
                return 0

        if opts.timeout_explicit:
            deadline = time.monotonic() + opts.timeout
            printed_any = False
            while time.monotonic() < deadline:
                if _poll_new_messages(inbox, seen, **poll_kwargs):
                    printed_any = True
                time.sleep(POLL_INTERVAL_SEC)
            if printed_any:
                return 0
            print(f"tells: no message within {opts.timeout:g}s", file=sys.stderr)
            return 1

        deadline = time.monotonic() + opts.timeout
        printed_any = False
        while True:
            printed = _poll_new_messages(inbox, seen, **poll_kwargs)
            if printed:
                printed_any = True
            elif printed_any:
                return 0
            if not printed_any and time.monotonic() >= deadline:
                print(f"tells: no message within {opts.timeout:g}s", file=sys.stderr)
                return 1
            time.sleep(POLL_INTERVAL_SEC)
    finally:
        if glow_stream is not None:
            glow_stream.close()
