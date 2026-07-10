#!/usr/bin/env python3
"""r4t — Roster For Teams.

Turns a repo into a team of lightweight AI agents on the a8s network: a
human-readable ROSTER.md declares the members, an out-of-repo harness config
decides what each symbolic tier is allowed to run, and r4t dispatches turns
through the roster.
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
    HARNESS_PRESETS,
    add_preset_tier,
    build_preset_invoke,
    default_config_path,
    default_config_payload,
    format_preset_invoke,
    load_harness_config,
    preset_names,
    resolve_config_path,
)
from notify import resolve_tell_fn, simulate_enabled
from roster import RosterError, load_roster, resolve_roster_path

DEFAULT_TASK_TTL_SECONDS = 7 * 86400
R4T_DIR = Path(__file__).resolve().parent

COMMAND_HELP = [
    ("init", "Write starter ROSTER.md and ~/.r4t/harnesses.json; print a8s registration"),
    ("status", "Locks, buckets, open tasks, dead letters for one team"),
    ("harness list", "Tier invoke lines, limits, and roster tier resolution"),
    ("harness presets", "Named CLI presets aligned with a8s definitions"),
    ("harness add <tier> <preset>", "Add a tier to ~/.r4t/harnesses.json from a preset"),
    ("roster check", "Lint ROSTER.md against the harness config"),
    ("task list", "List open tasks for a team"),
    ("task show <id>", "Show one task ledger record as JSON"),
    ("clear", "Prune stale locks, expire idle tasks, drain deferred messages"),
    ("idle", "Nudge agents with unfinished work, then clear"),
    ("sandbox", "Disposable end-to-end run with graded report"),
    ("sandbox --fake", "Same pipeline with deterministic fake agents (no LLM)"),
    ("sandbox --preset NAME", "Live sandbox harness (see `r4t harness presets`)"),
    ("sandbox --preset opencode-ollama --model M", "Live sandbox via Ollama-local OpenCode"),
    ("dispatch", "Handle one delivered message (a8s invoke entry)"),
]

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


def _print_harness_summary(config_path: Path, roster_path: Path | None = None) -> None:
    try:
        config = load_harness_config(config_path)
    except HarnessError as e:
        print(f"  error: {e}")
        return
    if config.missing:
        print("  (missing — run `r4t init` to write a starter config)")
        return
    for name in sorted(config.tiers):
        tier = config.tiers[name]
        if tier.error:
            print(f"  {name}: INVALID — {tier.error}")
            continue
        pool = tier.pool()
        argv = " ".join(pool[0])
        if len(pool) > 1:
            argv += f"  [+{len(pool) - 1} pool variant(s)]"
        print(
            f"  {name}: {argv}  "
            f"(timeout={tier.timeout_seconds:g}s turns={tier.max_turns_per_task} "
            f"hop_limit={tier.hop_limit} sends={tier.max_sends_per_turn})"
        )
    if config.pins:
        print("  pins:")
        for agent in sorted(config.pins):
            print(f"    {agent} -> {config.pins[agent]}")
    print(
        f"  throttle: max_concurrent={config.throttle.max_concurrent} "
        f"min_seconds_between_turn_starts="
        f"{config.throttle.min_seconds_between_turn_starts:g}"
    )
    print(
        f"  governance: suppression_window={config.suppression_window_seconds:g}s "
        f"bucket_max={config.bucket_max:g} bucket_earn={config.bucket_earn_ratio:g} "
        f"nudge_cap={config.nudge_cap} active_ttl_rotations={config.active_ttl_rotations}"
    )
    if config.rebroadcast_senders:
        print(f"  rebroadcast_senders: {', '.join(config.rebroadcast_senders)}")
    if roster_path and roster_path.is_file():
        try:
            roster = load_roster(roster_path)
        except RosterError as e:
            print(f"  roster ({roster_path.name}): {e}")
            return
        print(f"  roster ({roster_path}):")
        for m in roster.members:
            if m.is_human:
                print(f"    {m.name}: Human")
            elif m.errors:
                print(f"    {m.name}: DISABLED — {m.error}")
            else:
                tier, err, pinned = config.tier_for(m)
                if tier is None:
                    print(f"    {m.name}: FAIL CLOSED — {err}")
                else:
                    print(f"    {m.name}: {tier.name}" + (" (pinned)" if pinned else ""))


def _print_team_summaries() -> None:
    teams = state.known_teams()
    if not teams:
        print("  (none — register a team after `r4t init`; see printed a8s steps)")
        return
    for node in teams:
        locks = state.live_locks(node)
        open_tasks = tasks.list_tasks(node)
        dead = len(state.list_dead_letters(node))
        pending = len(state.list_pending(node))
        parts = [
            f"{len(open_tasks)} task(s)",
            f"{len(locks)} lock(s)",
            f"{pending} pending",
            f"{dead} dead letter(s)",
        ]
        print(f"  {node}: {', '.join(parts)}")
        for lock in locks:
            print(f"    locked: {lock.get('agent', '?')} pid={lock.get('pid', '?')}")


def _next_steps(
    *,
    config_missing: bool,
    roster_path: Path,
    teams: list[str],
) -> list[str]:
    steps: list[str] = []
    if config_missing:
        steps.append("`r4t init` — write ~/.r4t/harnesses.json with default tiers")
    if not roster_path.is_file():
        steps.append("`r4t init` — write a starter ROSTER.md in the current repo")
    else:
        steps.append("`r4t roster check` — lint the roster and harness mapping")
        steps.append("`r4t harness presets` — named CLI tiers aligned with a8s definitions")
        steps.append("`r4t harness add <tier> <preset>` — add a tier to the harness config")
    if not teams:
        steps.append("`r4t init` — prints the a8s add / namespace / start sequence")
    elif len(teams) == 1:
        steps.append(f"`r4t status --node {teams[0]}` — live locks, buckets, tasks")
    else:
        steps.append("`r4t status --node <team>` — pick a team from the list above")
    steps.append("`r4t sandbox --fake` — end-to-end plumbing check without LLM calls")
    return steps


def cmd_default(_args: argparse.Namespace) -> int:
    root = Path.cwd().resolve()
    config_path = default_config_path()
    roster_path = resolve_roster_path(root, None)
    teams = state.known_teams()

    print("r4t — Roster For Teams")
    print("Define agents in ROSTER.md; ~/.r4t/harnesses.json maps roster tiers")
    print("to what actually runs. r4t dispatches governed turns on a8s.")
    print()
    print("Environment")
    print(f"  R4T_HOME: {state.r4t_home()}")
    print(f"  cwd: {root}")
    print(f"  harness config: {config_path}")
    print()
    print("Harness")
    _print_harness_summary(config_path, roster_path)
    print()
    print(f"Teams ({state.teams_dir()})")
    _print_team_summaries()
    print()
    print("This repo")
    if roster_path.is_file():
        try:
            roster = load_roster(roster_path)
            leaders = [m for m in roster.members if m.leader and not m.is_human]
            leader = leaders[0].name if len(leaders) == 1 else "(unset)"
            print(
                f"  {roster_path}: {len(roster.members)} member(s), "
                f"leader {leader}"
            )
        except RosterError as e:
            print(f"  {roster_path}: {e}")
    else:
        print(f"  no ROSTER.md under {root}")
    print()
    print("Commands")
    width = max(len(name) for name, _ in COMMAND_HELP)
    for name, blurb in COMMAND_HELP:
        print(f"  {name:<{width}}  {blurb}")
    print()
    print("Next steps")
    for step in _next_steps(
        config_missing=not config_path.is_file(),
        roster_path=roster_path,
        teams=teams,
    ):
        print(f"  - {step}")
    print()
    print("More: apps/r4t/README.md and `r4t <command> --help`")
    return 0


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
    print(f"harness config: {config_path}" + (" (missing)" if not config_path.is_file() else ""))
    roster_path = resolve_roster_path(_resolve_root(args.root), args.roster)
    _print_harness_summary(config_path, roster_path if roster_path.is_file() else None)
    return 0


def cmd_harness_presets(_args: argparse.Namespace) -> int:
    print("Named harness presets (from apps/a8s/definitions/):")
    width = max(len(name) for name in preset_names())
    for name in preset_names():
        entry = HARNESS_PRESETS[name]
        print(f"  {name:<{width}}  {entry['description']}")
        print(f"  {'':<{width}}  headless: {entry['headless']}")
        print(f"  {'':<{width}}  invoke: {format_preset_invoke(name)}")
    print()
    print("Add one: r4t harness add <tier-name> <preset>")
    print("Example: r4t harness add worker opencode")
    return 0


def cmd_harness_add(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.harness_config)
    preset_key = args.preset.strip().lower()
    try:
        tier_key = add_preset_tier(
            config_path,
            args.tier,
            args.preset,
            model=args.model,
            force=args.force,
        )
        invoke = build_preset_invoke(preset_key, model=args.model)
    except HarnessError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"added tier {tier_key!r} ({args.preset}) to {config_path}")
    print(f"  invoke: {' '.join(invoke)}")
    print(f"Reference it from ROSTER.md: `- **Harness:** {tier_key}`")
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
        preset=args.preset,
        model=args.model,
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
        description="Roster For Teams — define agents in ROSTER.md; govern turns on a8s.",
    )
    sub = p.add_subparsers(dest="command", required=False)
    p.set_defaults(func=cmd_default)

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

    harness_presets_p = harness_sub.add_parser(
        "presets",
        help="List named CLI presets aligned with a8s definitions.",
    )
    harness_presets_p.set_defaults(func=cmd_harness_presets)

    harness_add_p = harness_sub.add_parser(
        "add",
        help="Add a symbolic tier from a named CLI preset.",
    )
    harness_add_p.add_argument(
        "tier",
        help="Symbolic tier name (referenced from ROSTER.md Harness lines).",
    )
    harness_add_p.add_argument(
        "preset",
        choices=preset_names(),
        help="CLI preset name (see `r4t harness presets`).",
    )
    harness_add_p.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing tier with the same name.",
    )
    harness_add_p.add_argument(
        "--model",
        metavar="MODEL",
        help="Model name for presets that need it (e.g. opencode-ollama).",
    )
    harness_add_p.add_argument(
        "--harness-config",
        help="Harness config path (default: ~/.r4t/harnesses.json).",
    )
    harness_add_p.set_defaults(func=cmd_harness_add)

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
        "logs to stderr, report on stdout.",
    )
    sandbox_p.add_argument(
        "--fake",
        action="store_true",
        help="Use the bundled deterministic fake agents (no LLM calls).",
    )
    sandbox_p.add_argument(
        "--preset",
        default="opencode",
        metavar="NAME",
        help="Live-mode harness preset (default: opencode). See `r4t harness presets`.",
    )
    sandbox_p.add_argument(
        "--model",
        metavar="MODEL",
        help="Model name for presets that need it (e.g. opencode-ollama).",
    )
    sandbox_p.add_argument("--timeout", type=float, default=1800, metavar="SECS")
    sandbox_p.set_defaults(func=cmd_sandbox)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
