#!/usr/bin/env python3
"""r4t — Router For Teams.

Turns a repo into a team of lightweight AI agents on the a8s network: the
repo is registered as one a8s node owning a namespace (e.g. `s1l:*`), a
human-readable ROSTER.md names the members, and an out-of-repo harness
config decides what each symbolic tier is actually allowed to run.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import state
import tasks
from dispatch import (
    DispatchContext,
    drain_until_quiet,
    handle_message,
    run_clear,
    run_idle,
    split_recipient,
)
from harness import (
    HarnessError,
    default_config_path,
    default_config_payload,
    load_harness_config,
    resolve_config_path,
)
from notify import resolve_tell_fn, simulate_enabled
from roster import RosterError, load_roster, resolve_roster_path

DEFAULT_TASK_TTL_SECONDS = 7 * 86400
R4T_DIR = Path(__file__).resolve().parent

ROSTER_TEMPLATE = """\
# Team Roster

Members are `### <Name>` blocks. `Status: Human` members are never
dispatched; `Harness:` names a SYMBOLIC tier defined in the out-of-repo
harness config (~/.r4t/harnesses.json). Free prose in a block becomes the
member's persona.

### Owner
- **Status:** Human
- **Address:** YOUR-A8S-NAME
- **Role:** Product owner

### Lead
- **Status:** AI
- **Harness:** leader
- **Leader:** yes
- **Role:** Team lead — delegates work and answers the owner

Coordinates the team. Delegates implementation, follows up on replies, and
synthesizes answers for whoever asked.

### Dev
- **Status:** AI
- **Harness:** member
- **Role:** Developer

Implements what the Lead asks for and reports back.
"""


def _resolve_root(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_node(raw: str | None) -> str | None:
    if raw:
        return raw.strip().lower()
    teams = state.known_teams()
    if len(teams) == 1:
        return teams[0]
    if not teams:
        print("no teams found under ~/.r4t/teams — pass --node", file=sys.stderr)
    else:
        print(f"multiple teams ({', '.join(teams)}) — pass --node", file=sys.stderr)
    return None


def _context(args: argparse.Namespace, node: str) -> DispatchContext:
    root = _resolve_root(args.root)
    return DispatchContext(
        root=root,
        node=node,
        roster_path=resolve_roster_path(root, getattr(args, "roster", None)),
        config_path=resolve_config_path(getattr(args, "harness_config", None)),
        tell_fn=resolve_tell_fn(
            notify=getattr(args, "notify", True),
            simulate=simulate_enabled(getattr(args, "simulate_tell", False)),
        ),
    )


def cmd_dispatch(args: argparse.Namespace) -> int:
    node, _sub = split_recipient(args.to)
    if not node:
        print("dispatch: --to must carry the node name", file=sys.stderr)
        return 2
    ctx = _context(args, node.lower())
    if not args.no_drain:
        drain_until_quiet(ctx)
    rc = handle_message(ctx, args.from_agent, args.to, args.message)
    if not args.no_drain:
        drain_until_quiet(ctx)
    return rc


def cmd_clear(args: argparse.Namespace) -> int:
    node = _resolve_node(args.node)
    if node is None:
        return 2
    ctx = _context(args, node)
    summary = run_clear(ctx, args.older_than)
    expired = summary["tasks_expired"]
    print(
        f"pruned {summary['locks_pruned']} stale lock(s); "
        f"expired {len(expired)} task(s)"
        + (f" ({', '.join(expired)})" if expired else "")
        + f"; drained {summary['drained']} deferred message(s)"
    )
    return 0


def cmd_idle(args: argparse.Namespace) -> int:
    node = _resolve_node(args.node)
    if node is None:
        return 2
    ctx = _context(args, node)
    summary = run_idle(ctx)
    print(
        f"watched {summary['watched']} active agent(s); "
        f"nudged {len(summary['nudged'])}"
        + (f" ({', '.join(summary['nudged'])})" if summary["nudged"] else "")
        + f"; dropped {len(summary['dropped'])}"
    )
    clear_summary = run_clear(ctx, args.older_than)
    expired = clear_summary["tasks_expired"]
    print(
        f"pruned {clear_summary['locks_pruned']} stale lock(s); "
        f"expired {len(expired)} task(s); "
        f"drained {clear_summary['drained']} deferred message(s)"
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    node = _resolve_node(args.node)
    if node is None:
        return 2
    ctx = _context(args, node)
    print(f"team: {node}")
    print(f"state: {state.team_dir(node)}")

    locks = {lock["agent"]: lock for lock in state.live_locks(node)}
    config = None
    try:
        roster = load_roster(ctx.roster_path)
    except RosterError as e:
        print(f"roster: {e}")
        roster = None
    if roster is not None:
        try:
            config = load_harness_config(ctx.config_path)
        except HarnessError as e:
            print(f"harness config: {e}")
        print(f"roster: {roster.path} ({len(roster.members)} member(s))")
        for m in roster.members:
            flags = []
            if m.leader:
                flags.append("leader")
            if m.name.lower() in locks:
                flags.append(f"LOCKED pid {locks[m.name.lower()].get('pid')}")
            if m.is_human:
                detail = f"Human, address={m.address or '(none)'}"
            elif m.errors:
                detail = f"DISABLED: {m.error}"
            elif config is not None:
                tier, err, pinned = config.tier_for(m)
                if tier is None:
                    detail = f"FAIL CLOSED: {err}"
                else:
                    detail = f"tier={tier.name}" + (" (pinned)" if pinned else "")
                    level = state.bucket_level(node, m.name, config.bucket_max)
                    detail += f"  bucket={level:.1f}/{config.bucket_max:g}"
                    if state.bucket_muted(level, config.bucket_max):
                        detail += " (MUTED)"
            else:
                detail = f"tier={m.harness or '?'} (config unavailable)"
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            print(f"  {m.name}: {detail}{suffix}")

    open_tasks = tasks.list_tasks(node)
    print(f"tasks: {len(open_tasks)}")
    for task in open_tasks:
        print(
            f"  {task['id']}  creator={task.get('creator', '?')}  "
            f"turns={task.get('turns', 0)}  "
            f"used={task.get('used', 0.0):.2f}/{task.get('budget', 1.0):.2f}  "
            f"status={task.get('status', '?')}"
            + ("  synthesized" if task.get("synthesized") else "")
        )
    print(f"pending (deferred): {len(state.list_pending(node))}")
    dead = state.list_dead_letters(node)
    print(f"dead letters: {len(dead)}  ({state.dead_letter_dir(node)})")
    reasons: dict[str, int] = {}
    for record in dead:
        reasons[record.get("reason", "?")] = reasons.get(record.get("reason", "?"), 0) + 1
    for reason in sorted(reasons):
        print(f"  {reason}: {reasons[reason]}")
    active = state.load_active(node)
    print(f"active watch list: {len(active)}")
    for agent in sorted(active):
        entry = active[agent] if isinstance(active[agent], dict) else {}
        print(
            f"  {agent}: ttl={entry.get('ttl', '?')}"
            + (f"  last_nudge={entry['last_nudge_at']}" if entry.get("last_nudge_at") else "")
        )
    return 0


def cmd_harness_list(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.harness_config)
    try:
        config = load_harness_config(config_path)
    except HarnessError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"harness config: {config_path}" + (" (missing)" if config.missing else ""))
    for name in sorted(config.tiers):
        tier = config.tiers[name]
        if tier.error:
            print(f"  {name}: INVALID — {tier.error}")
            continue
        pool = tier.pool()
        shown = " ".join(pool[0]) + (
            f"  [+{len(pool) - 1} pool variant(s)]" if len(pool) > 1 else ""
        )
        print(
            f"  {name}: {shown}  "
            f"(timeout={tier.timeout_seconds:g}s concurrency={tier.concurrency} "
            f"max_turns_per_task={tier.max_turns_per_task} hop_limit={tier.hop_limit} "
            f"max_sends_per_turn={tier.max_sends_per_turn})"
        )
    if config.pins:
        print("pins:")
        for agent in sorted(config.pins):
            print(f"  {agent} -> {config.pins[agent]}")
    print(
        f"throttle: max_concurrent={config.throttle.max_concurrent} "
        f"min_seconds_between_turn_starts={config.throttle.min_seconds_between_turn_starts:g}"
    )

    root = _resolve_root(args.root)
    roster_path = resolve_roster_path(root, args.roster)
    if roster_path.is_file():
        try:
            roster = load_roster(roster_path)
        except RosterError:
            return 0
        print(f"resolved tiers for {roster_path}:")
        for m in roster.members:
            if m.is_human:
                print(f"  {m.name}: Human (never dispatched)")
            elif m.errors:
                print(f"  {m.name}: DISABLED — {m.error}")
            else:
                tier, err, pinned = config.tier_for(m)
                if tier is None:
                    print(f"  {m.name}: FAIL CLOSED — {err}")
                else:
                    print(f"  {m.name}: {tier.name}" + (" (pinned)" if pinned else ""))
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    node = _resolve_node(args.node)
    if node is None:
        return 2
    if args.action == "list":
        listing = tasks.list_tasks(node)
        if not listing:
            print("no tasks")
            return 0
        for task in listing:
            print(
                f"{task['id']}  creator={task.get('creator', '?')}  "
                f"turns={task.get('turns', 0)}  "
                f"used={task.get('used', 0.0):.2f}/{task.get('budget', 1.0):.2f}  "
                f"status={task.get('status', '?')}"
                + ("  synthesized" if task.get("synthesized") else "")
            )
        return 0
    if not args.id:
        print("task show: <id> is required", file=sys.stderr)
        return 2
    task = tasks.load_task(node, args.id.strip().upper())
    if task is None:
        print(f"task not found: {args.id}", file=sys.stderr)
        return 1
    import json

    print(json.dumps(task, indent=2))
    return 0


def cmd_roster_check(args: argparse.Namespace) -> int:
    root = _resolve_root(args.root)
    roster_path = resolve_roster_path(root, args.roster)
    try:
        roster = load_roster(roster_path)
    except RosterError as e:
        print(str(e), file=sys.stderr)
        return 1
    config_path = resolve_config_path(args.harness_config)
    try:
        config = load_harness_config(config_path)
    except HarnessError as e:
        print(f"warning: {e}", file=sys.stderr)
        config = None

    problems = 0
    if not roster.members:
        print(f"{roster_path}: no `### <Name>` member blocks found")
        problems += 1
    for m in roster.members:
        for err in m.errors:
            print(f"{m.name}: {err}")
            problems += 1
        if m.is_human:
            if not m.address:
                print(f"{m.name}: note — Human without an Address (team cannot tell them)")
            continue
        if config is not None and not m.errors:
            tier, err, _pinned = config.tier_for(m)
            if tier is None:
                print(f"{m.name}: {err}")
                problems += 1
    leaders = [m for m in roster.members if m.leader and not m.is_human]
    if not leaders:
        print(
            "no leader: mark one AI member with `- **Leader:** yes` "
            "(bare messages to the node have no recipient)"
        )
        problems += 1
    elif len(leaders) > 1:
        print(
            f"multiple leaders: {', '.join(m.name for m in leaders)} "
            f"(first one wins: {leaders[0].name})"
        )
        problems += 1
    if problems:
        print(f"{problems} problem(s)")
        return 1
    print(
        f"{roster_path}: OK ({len(roster.members)} member(s), leader {leaders[0].name})"
    )
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    root = _resolve_root(args.root)
    if not root.is_dir():
        print(f"init: not a directory: {root}", file=sys.stderr)
        return 1

    roster_path = root / "ROSTER.md"
    if roster_path.is_file():
        print(f"roster: {roster_path} exists, left unchanged")
    else:
        roster_path.write_text(ROSTER_TEMPLATE, encoding="utf-8")
        print(f"roster: wrote starter {roster_path}")

    config_path = default_config_path()
    if config_path.is_file():
        print(f"harness config: {config_path} exists, left unchanged")
    else:
        state.atomic_write_json(config_path, default_config_payload())
        print(f"harness config: wrote starter {config_path}")

    team = re.sub(r"[^a-z0-9_-]+", "-", root.name.lower()).strip("-") or "team"
    node = f"{team}-node"
    definition = R4T_DIR / "example-definition.json"
    print()
    print("Register and start the team (a namespace prefix cannot share a")
    print("name with its agent, so the node is registered as <team>-node):")
    print()
    print(f"  a8s add {node} {root} {definition}")
    print(f"  a8s namespace {team} {node}")
    print(f"  a8s start {node}")
    print(f'  tell {node} "hello"            # bare node -> the roster leader')
    print(f'  tell {team}:dev "hello"        # namespace -> a specific member')
    return 0


def cmd_sandbox(args: argparse.Namespace) -> int:
    from sandbox import run_sandbox

    return run_sandbox(
        fake=args.fake,
        timeout=args.timeout,
        out=Path(args.out).expanduser().resolve(),
    )


def _add_common(p: argparse.ArgumentParser, *, with_node: bool = False) -> None:
    p.add_argument("--root", help="Team repo root (default: cwd).")
    p.add_argument(
        "--roster",
        help="Roster path, absolute or root-relative (default: <root>/ROSTER.md).",
    )
    p.add_argument(
        "--harness-config",
        help="Harness config path (default: ~/.r4t/harnesses.json).",
    )
    if with_node:
        p.add_argument("--node", help="Team node name (default: sole ~/.r4t team).")


def _add_tell_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--simulate-tell",
        action="store_true",
        help="Print would-be tell calls to stderr instead of invoking tell "
        "(also R4T_SIMULATE_TELL=1).",
    )
    p.add_argument(
        "--no-notify",
        dest="notify",
        action="store_false",
        default=True,
        help="Drop tell output entirely (unit tests).",
    )


def _add_older_than(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--older-than",
        type=float,
        default=DEFAULT_TASK_TTL_SECONDS,
        metavar="SECS",
        help=f"Expire tasks idle longer than SECS (default {DEFAULT_TASK_TTL_SECONDS}).",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="r4t",
        description="Router For Teams — a repo as a team of AI agents on a8s.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    dispatch_p = sub.add_parser(
        "dispatch", help="Handle one delivered message (the a8s invoke entry)."
    )
    _add_common(dispatch_p)
    dispatch_p.add_argument("--from", dest="from_agent", required=True)
    dispatch_p.add_argument(
        "--to",
        required=True,
        help="Full recipient as delivered ($RECIPIENT): <node> or <node>:<member>.",
    )
    dispatch_p.add_argument("--message", required=True)
    dispatch_p.add_argument(
        "--no-drain",
        action="store_true",
        help="Skip the deferred-message drain passes around this message.",
    )
    _add_tell_flags(dispatch_p)
    dispatch_p.set_defaults(func=cmd_dispatch)

    clear_p = sub.add_parser(
        "clear", help="Maintenance: prune stale locks, expire tasks, drain."
    )
    _add_common(clear_p, with_node=True)
    _add_older_than(clear_p)
    _add_tell_flags(clear_p)
    clear_p.set_defaults(func=cmd_clear)

    idle_p = sub.add_parser(
        "idle",
        help="Idle pass: nudge active agents with unfinished business, then clear.",
    )
    _add_common(idle_p, with_node=True)
    _add_older_than(idle_p)
    _add_tell_flags(idle_p)
    idle_p.set_defaults(func=cmd_idle)

    status_p = sub.add_parser("status", help="Per-team status.")
    _add_common(status_p, with_node=True)
    _add_tell_flags(status_p)
    status_p.set_defaults(func=cmd_status)

    harness_p = sub.add_parser("harness", help="Harness config commands.")
    harness_sub = harness_p.add_subparsers(dest="action", required=True)
    harness_list_p = harness_sub.add_parser(
        "list", help="Show configured tiers and resolved roster tiers."
    )
    _add_common(harness_list_p)
    harness_list_p.set_defaults(func=cmd_harness_list)

    task_p = sub.add_parser("task", help="Task ledger commands.")
    task_p.add_argument("action", choices=["list", "show"])
    task_p.add_argument("id", nargs="?", help="Task ULID.")
    task_p.add_argument("--node", help="Team node name (default: sole ~/.r4t team).")
    task_p.set_defaults(func=cmd_task)

    roster_p = sub.add_parser("roster", help="Roster commands.")
    roster_sub = roster_p.add_subparsers(dest="action", required=True)
    roster_check_p = roster_sub.add_parser("check", help="Lint the roster.")
    _add_common(roster_check_p)
    roster_check_p.set_defaults(func=cmd_roster_check)

    init_p = sub.add_parser(
        "init",
        help="Write a starter ROSTER.md and ~/.r4t/harnesses.json; print the "
        "a8s registration sequence.",
    )
    init_p.add_argument("--root", help="Repo to initialize (default: cwd).")
    init_p.set_defaults(func=cmd_init)

    sandbox_p = sub.add_parser(
        "sandbox",
        help="Disposable end-to-end team run in a temp A8S_HOME/R4T_HOME; "
        "writes a self-contained report.",
    )
    sandbox_p.add_argument(
        "--fake",
        action="store_true",
        help="Use the bundled deterministic fake agents (no LLM calls).",
    )
    sandbox_p.add_argument("--timeout", type=float, default=900, metavar="SECS")
    sandbox_p.add_argument(
        "--out", default="./r4t-sandbox-report.md", help="Report path."
    )
    sandbox_p.set_defaults(func=cmd_sandbox)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
