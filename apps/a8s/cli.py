"""a8s CLI — the COMMANDS table, dispatcher, and main argparse entry."""
from __future__ import annotations

import argparse
import sys

from commands import (
    cmd_add,
    cmd_agents,
    cmd_alias,
    cmd_aliases,
    cmd_clear,
    cmd_define,
    cmd_discover,
    cmd_exit,
    cmd_install,
    cmd_kill,
    cmd_logs,
    cmd_ls,
    cmd_prompt,
    cmd_run,
    cmd_start,
    cmd_step,
    cmd_stop,
    cmd_tell,
    cmd_unalias,
)


COMMANDS: list[tuple[str, str, str]] = [
    ("add",      "<name> <dir> [<def>]", "Register a new agent. Without <def>, scans <dir> for marker files (CLAUDE.md/GEMINI.md/CODEX.md) and auto-links the matching built-in definition."),
    ("agents",   "",                     "List every registered agent and definition status."),
    ("discover", "<path>",               "Walk <path> for marker files; print suggested `a8s add`/`a8s define` commands. Read-only."),
    ("define",   "<name> [<path>]",      "Show or set <name>'s definition JSON. Without <path>, prints the effective definition."),
    ("alias",    "[<alias> <member>]",   "Add member to alias (creates if new). Bare lists all aliases. Members may be agents or other aliases."),
    ("unalias",  "<alias> [<member>]",   "Remove a member from alias, or remove the whole alias."),
    ("aliases",  "",                     "List every alias and its members."),
    ("start",    "<name>",               "Detached background process handling <name>. Aliases produce ONE process handling all members (each member's pid file points at it)."),
    ("run",      "<name>",               "Foreground attached loop. Aliases produce one process handling all members (interleaved output). Ctrl+C: graceful detach. 2nd Ctrl+C: kill subprocess group."),
    ("step",     "<name>",               "Attach as handler, one route+drain pass across all members, release. Heavyweight: detaches current handler."),
    ("stop",     "<name>",               "SIGTERM each unique handler PID (one signal per multi-agent handler). Graceful detach — collateral on other members of the same handler."),
    ("kill",     "<name>",               "Per unique handler PID: SIGTERM, brief grace, 2nd SIGTERM (kills subprocess group), SIGKILL fallback."),
    ("exit",     "",                     "SIGTERM every running handler. Each detaches gracefully on its own."),
    ("ls",       "",                     "List only running agents and their handler PIDs."),
    ("prompt",   "<name> <message>",     "Queue a senderless message. <name> may be an agent or alias (queues per member)."),
    ("tell",     "<name> <message>",     "Routed message to <name>. <name> may be an agent or alias (fans out at routing time). Sender = agent enclosing CWD."),
    ("clear",    "<name>",               "Queue a CLEAR sentinel (wipes inbox first; next wake runs invokeClear). Aliases iterate."),
    ("install",  "",                     "Install canonical skills into each supported tool's user scope."),
    ("logs",     "<name>... [--tail N] [-f]", "Read per-agent log files; merge-sort multiple by timestamp. Names may include aliases."),
]

KNOWN_COMMANDS = {name for name, _, _ in COMMANDS}


def _format_commands(rows: list[tuple[str, str, str]], indent: int = 2) -> str:
    headers = [(n + " " + a).strip() for n, a, _ in rows]
    width = max(len(h) for h in headers)
    return "\n".join(
        f"{' ' * indent}{header.ljust(width)}    {help_text}"
        for header, (_, _, help_text) in zip(headers, rows)
    )


CLI_EPILOG = "Commands:\n" + _format_commands(COMMANDS)


def dispatch(cmd: str, args: list[str], interval: float) -> int:
    if cmd == "add":
        return cmd_add(args)
    if cmd == "agents":
        return cmd_agents()
    if cmd == "discover":
        return cmd_discover(args)
    if cmd == "define":
        return cmd_define(args)
    if cmd == "alias":
        return cmd_alias(args)
    if cmd == "unalias":
        return cmd_unalias(args)
    if cmd == "aliases":
        return cmd_aliases()
    if cmd == "start":
        return cmd_start(args)
    if cmd == "run":
        return cmd_run(args, interval)
    if cmd == "step":
        return cmd_step(args, interval)
    if cmd == "stop":
        return cmd_stop(args)
    if cmd == "kill":
        return cmd_kill(args)
    if cmd == "exit":
        return cmd_exit()
    if cmd == "ls":
        return cmd_ls()
    if cmd == "prompt":
        return cmd_prompt(args)
    if cmd == "tell":
        return cmd_tell(args)
    if cmd == "clear":
        return cmd_clear(args)
    if cmd == "install":
        return cmd_install()
    if cmd == "logs":
        return cmd_logs(args)
    raise ValueError(f"unknown command: {cmd!r}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="a8s",
        description="Agent Infinity System — route messages between Claude / Gemini / Codex projects.",
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--interval", type=float, default=1.0, help="loop poll interval seconds (default: 1.0)")
    parser.add_argument("command", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command in KNOWN_COMMANDS:
        return dispatch(args.command, args.rest, args.interval)

    print(f"unknown command: {args.command!r}", file=sys.stderr)
    return 2
