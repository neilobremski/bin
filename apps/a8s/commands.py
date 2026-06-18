"""a8s commands — every cmd_* function dispatched by cli.py.

Grouped by section:
  registry mgmt    — add, define, agents, discover, install
  aliases          — alias, unalias, aliases
  process control  — start, run, step, stop, kill, exit, ls
  messaging        — tell
  logs             — logs
  remotes          — remote, unremote

`cmd_start` re-execs the entry script via `core.ENTRYPOINT` (NOT __file__,
which would resolve to commands.py after the modular split).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from core import (
    ENTRYPOINT,
    SKILLS_DIR,
    BIN_ROOT,
    _pid_alive,
    _preview,
    agent_dir,
    agent_log_path,
    canonical_name,
    inbox_dir,
    out,
    out_agent,
    pid_path,
    trash_dir,
    unique_path,
)
from definitions import _autodiscover_definition, default_definition_path
from daemon import (
    _clear_kill_request,
    _read_handler_pid,
    _write_kill_request,
    attached_loop,
)
from network import (
    configured_remote_ids,
    detect_service_kind,
    load_network_config,
    save_network_config,
)
from registry import (
    _scan_for_markers,
    find_participant,
    load_aliases,
    load_registry,
    participants_from_registry,
    resolve_name,
    resolve_recipient,
    save_aliases,
    save_registry,
)


# ---------- skill installation helpers ----------

def _install_skill_claude(skill_dir: Path) -> str:
    docs_dir = BIN_ROOT / "docs"
    if not docs_dir.is_dir():
        return f"  claude: {docs_dir} not found; skipping"
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return f"  claude: {skill_md} missing; skipping"
    target_link = docs_dir / f"{skill_dir.name}.md"
    rel = Path("..") / skill_md.relative_to(BIN_ROOT)
    if target_link.is_symlink():
        if os.readlink(target_link) == str(rel):
            return f"  claude: docs/{target_link.name} already linked"
        target_link.unlink()
    elif target_link.exists():
        return f"  claude: {target_link} exists and is not a symlink; refusing to overwrite"
    target_link.symlink_to(rel)
    return f"  claude: linked docs/{target_link.name} -> {rel} (install.sh will sync to ~/.claude/skills/)"


def _install_skill_agy(skill_dir: Path) -> str:
    if shutil.which("agy") is None:
        return "  agy: not on PATH; skipping"
    return "  agy: plugin install not yet supported; skipping"


def _install_skill_codex(skill_dir: Path) -> str:
    codex_skills = Path.home() / ".codex" / "skills"
    if not codex_skills.is_dir():
        return f"  codex: {codex_skills} not found; skipping (codex may not be installed)"
    skill_name = skill_dir.name
    target = codex_skills / skill_name
    src = str(skill_dir)
    if target.is_symlink():
        if os.readlink(target) == src:
            return f"  codex: '{skill_name}' already linked"
        target.unlink()
    elif target.exists():
        return f"  codex: {target} exists and is not a symlink; refusing to overwrite"
    target.symlink_to(src)
    return f"  codex: linked '{skill_name}' at {target}"


def _install_skill_copilot(skill_dir: Path) -> str:
    """Copilot CLI has no `copilot skills link <local-dir>` analog of the
    other tools — `copilot plugin install` only accepts git sources, and
    `--plugin-dir` is per-session. Until a stable user-scope mechanism
    exists, the `tell` instructions live directly in each Copilot agent's
    `.github/copilot-instructions.md` (which is also the a8s marker AND
    the file Copilot itself auto-loads — see `tests/agents/copilot-agent/`)."""
    if shutil.which("copilot") is None:
        return "  copilot: not on PATH; skipping"
    return "  copilot: skill install delegated to per-agent .github/copilot-instructions.md (no user-scope skill mechanism)"


def _install_skill_opencode(skill_dir: Path) -> str:
    """OpenCode auto-loads `AGENTS.md` plus `.claude/CLAUDE.md` and
    `.claude/skills/` per project, but lacks a user-scope skill registry.
    Per-agent `tell` instructions live directly in each agent's
    `AGENTS.md` (the a8s marker — see `tests/agents/opencode-agent/`)."""
    if shutil.which("opencode") is None:
        return "  opencode: not on PATH; skipping"
    return "  opencode: skill install delegated to per-agent AGENTS.md (no user-scope skill mechanism)"


# ---------- registry management commands ----------

def cmd_add(args: list[str]) -> int:
    """`a8s add <name> <dir> [<definition>]` — register a new agent.

    The name is canonicalized (lowercase, alphanumeric) at registration so
    `a8s add CLAUDE` and `a8s add claude` collapse to the same agent — closes
    the case-collision footgun where independent registry entries each got
    their own dir but lookups conflated them (issue #65).

    Without `<definition>`, `<dir>` is scanned for a marker file
    (CLAUDE.md/GEMINI.md/CODEX.md) and the matching built-in definition is
    auto-linked. Multiple or zero markers fall back to the bundled default.

    With `<definition>`, the JSON file is validated and set as the agent's
    definition.

    Errors on duplicate name (vs. agents or aliases) or non-directory path."""
    if len(args) < 2 or len(args) > 3:
        print("usage: a8s add <name> <dir> [<definition>]", file=sys.stderr)
        return 2
    raw_name, dir_str = args[0], args[1]
    definition_arg = args[2] if len(args) == 3 else None
    try:
        name = canonical_name(raw_name)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    root = Path(dir_str).expanduser()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1
    root = root.resolve()
    reg = load_registry()
    for k in reg:
        if k.lower() == name:
            print(f"agent already exists with name: {k}", file=sys.stderr)
            return 1
    aliases = load_aliases()
    for k in aliases:
        if k.lower() == name:
            print(f"alias already exists with name: {k} — pick a different agent name", file=sys.stderr)
            return 1

    if definition_arg:
        path = Path(definition_arg).expanduser().resolve()
        if not path.is_file():
            print(f"not a file: {path}", file=sys.stderr)
            return 1
        try:
            with path.open("r", encoding="utf-8") as f:
                json.loads(f.read())
        except (OSError, json.JSONDecodeError) as e:
            print(f"definition is not valid JSON: {e}", file=sys.stderr)
            return 1
        definition_path = str(path)
        note = "explicit"
    else:
        definition_path, note = _autodiscover_definition(root)

    reg[name] = {"root": str(root), "definition": definition_path}
    save_registry(reg)
    print(f"added {name} -> {root}")
    print(f"definition: {definition_path}  ({note})")
    return 0


def cmd_remove(args: list[str]) -> int:
    """`a8s remove <name>` — unregister an agent. Refuses if a handler is
    running (the user must `a8s stop` it first). Cascades into aliases:
    drops <name> from any alias's member list, and deletes any alias that
    becomes empty as a result. Wipes the on-disk per-agent dir
    (~/.a8s/agents/<NAME>/) — inbox, trash, log, pid file all gone."""
    if len(args) != 1:
        print("usage: a8s remove <name>", file=sys.stderr)
        return 2
    raw = args[0]
    try:
        canonical_name(raw)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    match = resolve_recipient(raw)
    if match is None:
        print(f"no agent named {raw!r}", file=sys.stderr)
        return 1
    name = match[0]
    reg = load_registry()
    holder = _read_handler_pid(name)
    if holder is not None:
        print(f"{name} is running (PID {holder}); stop it first: `a8s stop {name}`", file=sys.stderr)
        return 1
    aliases = load_aliases()
    pruned: list[str] = []
    dropped: list[str] = []
    for alias_name in list(aliases.keys()):
        members = aliases[alias_name]
        kept = [m for m in members if m.lower() != name.lower()]
        if len(kept) == len(members):
            continue
        if kept:
            aliases[alias_name] = kept
            pruned.append(alias_name)
        else:
            del aliases[alias_name]
            dropped.append(alias_name)
    if pruned or dropped:
        save_aliases(aliases)
    d = agent_dir(name)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    del reg[name]
    save_registry(reg)
    print(f"removed {name}")
    if pruned:
        print(f"  pruned from aliases: {', '.join(sorted(pruned))}")
    if dropped:
        print(f"  dropped now-empty aliases: {', '.join(sorted(dropped))}")
    return 0


def cmd_define(args: list[str]) -> int:
    """`a8s define <name>`           — show <name>'s effective definition + source.
    `a8s define <name> <path>`       — set <name>'s definition file path in the registry."""
    if not args:
        print("usage: a8s define <name> [<path-to-definition.json>]", file=sys.stderr)
        return 2
    name = args[0]
    reg = load_registry()
    target_key: str | None = None
    for k in reg:
        if k.lower() == name.lower():
            target_key = k
            break
    if target_key is None:
        print(f"no agent named {name!r}", file=sys.stderr)
        return 1
    info = reg[target_key]

    if len(args) == 1:
        custom = info.get("definition")
        if not custom:
            print(f"{target_key}: no definition set", file=sys.stderr)
            print(f"hint: a8s define {target_key} apps/a8s/definitions/<kind>.json", file=sys.stderr)
            return 1
        source = Path(custom).expanduser()
        print(f"{target_key}: {source}")
        try:
            with source.open("r", encoding="utf-8") as f:
                sys.stdout.write(f.read())
        except OSError as e:
            print(f"(could not read: {e})", file=sys.stderr)
            return 1
        return 0

    if len(args) > 2:
        print("usage: a8s define <name> [<path-to-definition.json>]", file=sys.stderr)
        return 2
    path = Path(args[1]).expanduser().resolve()
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 1
    try:
        with path.open("r", encoding="utf-8") as f:
            json.loads(f.read())
    except (OSError, json.JSONDecodeError) as e:
        print(f"definition is not valid JSON: {e}", file=sys.stderr)
        return 1
    info["definition"] = str(path)
    save_registry(reg)
    print(f"{target_key}: definition set to {path}")
    return 0


def cmd_agents() -> int:
    """`a8s agents` — list every registered agent and its definition path.
    Every agent always has a definition (default fallback applies if the
    registry's `definition` field is missing)."""
    reg = load_registry()
    if not reg:
        print("(no agents registered — use `a8s add <name> <dir>`)")
        return 0
    default_fallback = str(default_definition_path("default"))
    width = max(len(name) for name in reg)
    for name in sorted(reg, key=str.lower):
        info = reg[name]
        root = info.get("root", "?")
        defn = info.get("definition") or f"{default_fallback} (fallback)"
        print(f"  {name.ljust(width)}  {root}  [{defn}]")
    return 0


def cmd_discover(args: list[str]) -> int:
    """`a8s discover <path>` — read-only walk for marker files. Prints suggested
    `a8s add` / `a8s define` commands; never mutates the registry."""
    if len(args) != 1:
        print("usage: a8s discover <path>", file=sys.stderr)
        return 2
    root = Path(args[0]).expanduser()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1
    found = _scan_for_markers(root.resolve())
    if not found:
        print(f"no marker files (CLAUDE.md/GEMINI.md/CODEX.md with `# Name` line) found under {root}")
        return 0
    reg = load_registry()
    registered_names = {n.lower() for n in reg}
    registered_roots = {Path(v.get("root", "")).resolve() for v in reg.values() if v.get("root")}
    print(f"found {len(found)} candidate(s) under {root}:\n")
    for name, kind, dir_path in found:
        already = name.lower() in registered_names or dir_path in registered_roots
        marker = "  [already registered]" if already else ""
        print(f"# {name} ({kind}) at {dir_path}{marker}")
        if not already:
            print(f"a8s add {name} {dir_path}")
            print(f"a8s define {name} {default_definition_path(kind)}")
        print()
    return 0


def cmd_install() -> int:
    if not SKILLS_DIR.is_dir():
        print(f"no skills directory at {SKILLS_DIR}", file=sys.stderr)
        return 1
    skill_dirs = [
        d for d in sorted(SKILLS_DIR.iterdir())
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]
    if not skill_dirs:
        print(f"no skills found in {SKILLS_DIR}")
        return 0
    print(f"installing {len(skill_dirs)} skill(s) from {SKILLS_DIR}:")
    for skill_dir in skill_dirs:
        print(f"\n[{skill_dir.name}]")
        print(_install_skill_claude(skill_dir))
        print(_install_skill_agy(skill_dir))
        print(_install_skill_codex(skill_dir))
        print(_install_skill_copilot(skill_dir))
        print(_install_skill_opencode(skill_dir))
    return 0


# ---------- alias commands ----------

def cmd_alias(args: list[str]) -> int:
    """`a8s alias` — manage aliases.

    Forms (mirror `a8s remote` / `a8s storage`):
      a8s alias                      list all
      a8s alias <name>               show one alias's members
      a8s alias <alias> <member>     add or create

    Names are canonicalized (lowercase) so `a8s alias Devs CLAUDE` and
    `a8s alias devs claude` are the same operation (issue #65). Members
    may be agent names OR existing alias names (nesting OK, cycles
    rejected at resolve time). The alias name must not collide with an
    existing agent name."""
    if len(args) == 0:
        return cmd_aliases()
    if len(args) == 1:
        return _cmd_alias_show(args[0])
    if len(args) != 2:
        print("usage: a8s alias <alias> <member>     # add or create", file=sys.stderr)
        print("       a8s alias <name>               # show one", file=sys.stderr)
        print("       a8s alias                      # list", file=sys.stderr)
        return 2
    raw_alias, raw_member = args
    try:
        alias_name = canonical_name(raw_alias)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        member = canonical_name(raw_member)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    agents = load_registry()
    aliases = load_aliases()
    for k in agents:
        if k.lower() == alias_name:
            print(f"agent already exists with name: {k} — pick a different alias", file=sys.stderr)
            return 1
    member_resolved: str | None = None
    for k in agents:
        if k.lower() == member:
            member_resolved = k
            break
    if member_resolved is None:
        for k in aliases:
            if k.lower() == member:
                member_resolved = k
                break
    if member_resolved is None:
        print(f"unknown member {raw_member!r} (not an agent or alias)", file=sys.stderr)
        return 1
    if member_resolved.lower() == alias_name:
        print(f"cannot add alias {alias_name!r} to itself", file=sys.stderr)
        return 1
    canonical_alias = alias_name
    for k in aliases:
        if k.lower() == alias_name:
            canonical_alias = k
            break
    members = aliases.get(canonical_alias) or []
    if any(m.lower() == member_resolved.lower() for m in members):
        print(f"{canonical_alias} already includes {member_resolved}")
        return 0
    members.append(member_resolved)
    aliases[canonical_alias] = members
    save_aliases(aliases)
    # Cycle check via resolve_name; revert on failure.
    try:
        resolve_name(canonical_alias)
    except ValueError as e:
        members.remove(member_resolved)
        if not members:
            aliases.pop(canonical_alias, None)
        else:
            aliases[canonical_alias] = members
        save_aliases(aliases)
        print(f"refusing add: {e}", file=sys.stderr)
        return 1
    print(f"{canonical_alias} += {member_resolved}")
    return 0


def cmd_unalias(args: list[str]) -> int:
    """`a8s unalias <alias> [<member>]` — remove a single member, or the whole
    alias if no member given. Both names are case-insensitive."""
    if not args or len(args) > 2:
        print("usage: a8s unalias <alias> [<member>]", file=sys.stderr)
        return 2
    target = args[0].strip().lower()
    aliases = load_aliases()
    canonical: str | None = None
    for k in aliases:
        if k.lower() == target:
            canonical = k
            break
    if canonical is None:
        print(f"unknown alias: {args[0]!r}", file=sys.stderr)
        return 1
    if len(args) == 1:
        del aliases[canonical]
        save_aliases(aliases)
        print(f"removed alias {canonical}")
        return 0
    member = args[1]
    member_lc = member.strip().lower()
    members = aliases[canonical]
    new_members = [m for m in members if m.lower() != member_lc]
    if len(new_members) == len(members):
        print(f"{canonical}: not a member: {member!r}", file=sys.stderr)
        return 1
    if not new_members:
        del aliases[canonical]
    else:
        aliases[canonical] = new_members
    save_aliases(aliases)
    print(f"{canonical} -= {member}")
    return 0


def _cmd_alias_show(name: str) -> int:
    """`a8s alias <name>` — show one alias's members. Mirrors `remote <name>`
    and `storage <name>`."""
    try:
        target = canonical_name(name)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    aliases = load_aliases()
    canonical: str | None = None
    for k in aliases:
        if k.lower() == target:
            canonical = k
            break
    if canonical is None:
        print(f"no alias named {name!r}", file=sys.stderr)
        return 1
    members = aliases[canonical]
    try:
        _, resolved = resolve_name(canonical)
        tail = "" if len(members) == len(resolved) else f"  → {len(resolved)} agents"
    except (KeyError, ValueError) as e:
        tail = f"  [{e}]"
    print(f"{canonical}: [{', '.join(members)}]{tail}")
    return 0


def cmd_aliases() -> int:
    """`a8s aliases` — list every alias and its members."""
    aliases = load_aliases()
    if not aliases:
        print("(no aliases — use `a8s alias <alias> <member>` to create one)")
        return 0
    width = max(len(name) for name in aliases)
    for name in sorted(aliases, key=str.lower):
        members = aliases[name]
        try:
            _, resolved = resolve_name(name)
            tail = "" if len(members) == len(resolved) else f"  → {len(resolved)} agents"
        except (KeyError, ValueError) as e:
            tail = f"  [{e}]"
        print(f"  {name.ljust(width)}  [{', '.join(members)}]{tail}")
    return 0


# ---------- process control commands ----------

def _expand_to_agents(name: str) -> list[str] | None:
    """Resolve `name` to a flat list of agent names. Returns None on error
    (already-printed usage)."""
    try:
        _, members = resolve_name(name)
    except KeyError:
        print(f"no agent or alias named {name!r}", file=sys.stderr)
        return None
    except ValueError as e:
        print(f"{e}", file=sys.stderr)
        return None
    if not members:
        print(f"{name!r} resolves to no agents", file=sys.stderr)
        return None
    return members


def cmd_run(args: list[str], interval: float) -> int:
    """`a8s run <name> [--drain <seconds>]` — foreground attached loop. <name>
    may be an agent or an alias; aliases produce ONE process that handles every
    member (each member's pid file points at this PID). Ctrl+C: graceful detach.
    2nd Ctrl+C: kills the wake subprocess group.

    --drain <seconds>: connect to MQTT remotes and trash incoming messages for
    the specified duration without invoking. Default 1s when given without a
    value."""
    drain_seconds = 0.0
    filtered = []
    i = 0
    while i < len(args):
        if args[i] == "--drain":
            i += 1
            if i < len(args) and not args[i].startswith("-"):
                try:
                    drain_seconds = float(args[i])
                except ValueError:
                    print("--drain requires a number (seconds)", file=sys.stderr)
                    return 2
            else:
                drain_seconds = 1.0
                continue
        else:
            filtered.append(args[i])
        i += 1
    if len(filtered) != 1:
        print("usage: a8s run <name> [--drain <seconds>]", file=sys.stderr)
        return 2
    members = _expand_to_agents(filtered[0])
    if members is None:
        return 1
    return attached_loop(members, interval, drain_seconds=drain_seconds)


def cmd_start(args: list[str]) -> int:
    """`a8s start <name>` — spawn ONE detached background process. The child
    runs `a8s run <name>` and (if <name> is an alias) handles every member in
    a single process. Returns the child's PID."""
    if len(args) != 1:
        print("usage: a8s start <name>", file=sys.stderr)
        return 2
    name = args[0]
    # Validate (resolve_name raises if unknown / cycle).
    try:
        _, members = resolve_name(name)
    except KeyError:
        print(f"start: no agent or alias named {name!r}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"start: {e}", file=sys.stderr)
        return 1
    if not members:
        print(f"start: {name!r} resolves to no agents", file=sys.stderr)
        return 1
    # NOTE: the child must launch the entrypoint script (a8s.py), NOT this
    # commands.py module. That's why core.ENTRYPOINT exists.
    cmd = [sys.executable, str(ENTRYPOINT), "run", name]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if len(members) == 1:
        print(f"started {members[0]} as PID {proc.pid}")
    else:
        print(f"started {name} (alias of {len(members)}) as PID {proc.pid}")
    return 0


def cmd_step(args: list[str], interval: float) -> int:
    """`a8s step <name>` — attach as handler, one route+drain pass, release.
    Aliases handled in a single process: one acquire across all members, one
    pass, one release."""
    if len(args) != 1:
        print("usage: a8s step <name>", file=sys.stderr)
        return 2
    members = _expand_to_agents(args[0])
    if members is None:
        return 1
    return attached_loop(members, interval, single_pass=True)


def cmd_stop(args: list[str]) -> int:
    """`a8s stop <name>` — SIGTERM the handler(s). One handler may serve
    multiple members of an alias; we dedupe by PID so we signal each unique
    handler exactly once. Detaches the WHOLE handler (collateral on any other
    members it was handling)."""
    if len(args) != 1:
        print("usage: a8s stop <name>", file=sys.stderr)
        return 2
    members = _expand_to_agents(args[0])
    if members is None:
        return 1
    seen_pids: dict[int, str] = {}
    not_running: list[str] = []
    for name in members:
        pid = _read_handler_pid(name)
        if pid is None:
            not_running.append(name)
            continue
        if pid not in seen_pids:
            seen_pids[pid] = name
    if not seen_pids:
        for n in not_running:
            print(f"{n}: not running", file=sys.stderr)
        return 1
    for pid, label in seen_pids.items():
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"{label}: sent SIGTERM to PID {pid}")
        except OSError as e:
            print(f"{label}: could not signal PID {pid}: {e}", file=sys.stderr)
    for n in not_running:
        print(f"{n}: not running")
    return 0


KILL_TIMEOUT_S = 10.0
KILL_POLL_S = 0.1


def cmd_kill(args: list[str]) -> int:
    """`a8s kill <name>` — per-agent force-detach. For each member, write
    a kill-request file and SIGUSR1 the holder; the holder's iteration top
    releases just that agent (and its SIGUSR1 handler kills any in-flight
    wake subprocess group iff the current wake target matches), so siblings
    keep running. Falls back to whole-process SIGTERM if the holder doesn't
    honor the request within KILL_TIMEOUT_S — that's the only path that
    still creates collateral, and it's the user's explicit force escalation."""
    if len(args) != 1:
        print("usage: a8s kill <name>", file=sys.stderr)
        return 2
    members = _expand_to_agents(args[0])
    if members is None:
        return 1
    rc = 0
    for name in members:
        holder = _read_handler_pid(name)
        if holder is None:
            print(f"{name}: not running")
            continue
        _write_kill_request(name, os.getpid())
        print(f"{name}: kill request → PID {holder}")
        try:
            os.kill(holder, signal.SIGUSR1)
        except ProcessLookupError:
            _clear_kill_request(name)
            continue
        deadline = time.time() + KILL_TIMEOUT_S
        released = False
        while time.time() < deadline:
            if not pid_path(name).is_file():
                released = True
                break
            time.sleep(KILL_POLL_S)
        if not released:
            print(
                f"{name}: holder PID {holder} did not honor kill within {KILL_TIMEOUT_S}s — "
                f"escalating to whole-process SIGTERM",
                file=sys.stderr,
            )
            try:
                os.kill(holder, signal.SIGTERM)
            except ProcessLookupError:
                pass
            rc = 1
        _clear_kill_request(name)
    return rc


def cmd_exit() -> int:
    """`a8s exit` — SIGTERM every running agent's handler. Each daemon
    detaches gracefully on its own."""
    parts = participants_from_registry()
    sent = 0
    for p in parts:
        pid = _read_handler_pid(p.name)
        if pid is None:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"{p.name}: SIGTERM PID {pid}")
            sent += 1
        except OSError as e:
            print(f"{p.name}: could not signal PID {pid}: {e}", file=sys.stderr)
    if sent == 0:
        print("no agents running")
    return 0


def cmd_ls() -> int:
    """`a8s ls` — list only running agents and their handler PIDs."""
    parts = participants_from_registry()
    running: list[tuple[str, int, Path]] = []
    for p in parts:
        pid = _read_handler_pid(p.name)
        if pid is not None:
            running.append((p.name, pid, p.root))
    if not running:
        print("(no agents running)")
        return 0
    width = max(len(n) for n, _, _ in running)
    for name, pid, root in sorted(running, key=lambda x: x[0].lower()):
        print(f"  {name.ljust(width)}  PID {pid}  {root}")
    return 0


# ---------- messaging commands ----------

def cmd_tell(args: list[str]) -> int:
    """`a8s tell <name> <msg>` — write a single outbox message; `name` may be
    an agent or alias. Fan-out to alias members happens at routing time and
    preserves the original `to` (alias name) — strict opacity, mailing-list
    style: the recipient knows it came via the list, not who else got it."""
    from tell import tell_main

    return tell_main(args)


# ---------- drain ----------

def cmd_drain(args: list[str]) -> int:
    """`a8s drain <name>` — move all inbox messages to trash without invoking.
    Prints a summary of each drained message."""
    if len(args) != 1:
        print("usage: a8s drain <name>", file=sys.stderr)
        return 2
    match = resolve_recipient(args[0])
    if match is None:
        print(f"no agent named {args[0]!r}", file=sys.stderr)
        return 1
    name = match[0]
    inbox = inbox_dir(name)
    trash = trash_dir(name)
    if not inbox.is_dir():
        print(f"no inbox for {name!r}", file=sys.stderr)
        return 1
    trash.mkdir(parents=True, exist_ok=True)

    files = sorted(f for f in inbox.iterdir() if f.is_file() and f.name.endswith(".json"))
    if not files:
        print(f"{name}: inbox empty")
        return 0

    count = 0
    for f in files:
        try:
            msg = json.loads(f.read_text())
            sender = msg.get("from", "?")
            content = msg.get("content", "")
            preview = content.replace("\n", " ")[:80]
            print(f"  {sender}: {preview}")
        except Exception:
            print(f"  (unreadable: {f.name})")
        dest = unique_path(trash / f.name)
        f.rename(dest)
        count += 1

    print(f"{name}: drained {count} message(s)")
    return 0


# ---------- logs ----------

def cmd_logs(args: list[str]) -> int:
    """Read each named agent's log.txt and emit lines merge-sorted by ISO
    timestamp prefix. -f follows each file; new lines from any source are
    interleaved using a small ordering buffer so multi-file output stays
    roughly chronological."""
    if not args:
        print("usage: a8s logs <name> [<name>...] [--tail N] [-f|--follow]", file=sys.stderr)
        return 2
    names: list[str] = []
    tail_n: int | None = None
    follow = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-f", "--follow"):
            follow = True
            i += 1
        elif a == "--tail" and i + 1 < len(args):
            try:
                tail_n = int(args[i + 1])
            except ValueError:
                print(f"--tail: not an integer: {args[i + 1]!r}", file=sys.stderr)
                return 2
            i += 2
        elif a.startswith("--tail="):
            try:
                tail_n = int(a.split("=", 1)[1])
            except ValueError:
                print(f"--tail: not an integer: {a!r}", file=sys.stderr)
                return 2
            i += 1
        elif a.startswith("-"):
            print(f"unknown logs arg: {a!r}", file=sys.stderr)
            return 2
        else:
            names.append(a)
            i += 1

    if not names:
        print("usage: a8s logs <name> [<name>...] [--tail N] [-f|--follow]", file=sys.stderr)
        return 2

    # Expand aliases. Names may include agents and aliases; dedupe agent names
    # (an agent listed twice via overlapping aliases shouldn't double up).
    expanded: list[str] = []
    seen: set[str] = set()
    for n in names:
        try:
            _, members = resolve_name(n)
        except KeyError:
            print(f"logs: no agent or alias named {n!r}", file=sys.stderr)
            return 1
        except ValueError as e:
            print(f"logs: {e}", file=sys.stderr)
            return 1
        for m in members:
            if m.lower() not in seen:
                seen.add(m.lower())
                expanded.append(m)

    paths = [agent_log_path(n) for n in expanded]
    missing = [p for p in paths if not p.is_file()]
    if len(missing) == len(paths):
        for p in missing:
            print(f"no log yet at {p}", file=sys.stderr)
        return 1

    # Initial dump: read all existing lines, merge-sort by leading timestamp.
    lines: list[str] = []
    for p in paths:
        if p.is_file():
            with p.open("r", encoding="utf-8", errors="replace") as f:
                lines.extend(f)
    lines.sort(key=lambda ln: ln.split(" ", 1)[0] if ln else "")
    if tail_n is not None:
        lines = lines[-tail_n:]
    for ln in lines:
        sys.stdout.write(ln)
    sys.stdout.flush()

    if not follow:
        return 0

    # Follow: poll each file's tail, emit new lines in chronological order
    # using a ~1s ordering window. Cheap and correct enough for v1.
    handles: list[tuple[Path, "os.IOBase"]] = []
    try:
        for p in paths:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch(exist_ok=True)
            f = p.open("r", encoding="utf-8", errors="replace")
            f.seek(0, 2)
            handles.append((p, f))
        buf: list[str] = []
        last_emit = time.time()
        try:
            while True:
                progress = False
                for _path, f in handles:
                    while True:
                        ln = f.readline()
                        if not ln:
                            break
                        buf.append(ln)
                        progress = True
                now = time.time()
                if buf and (not progress or now - last_emit >= 1.0):
                    buf.sort(key=lambda ln: ln.split(" ", 1)[0] if ln else "")
                    for ln in buf:
                        sys.stdout.write(ln)
                    sys.stdout.flush()
                    buf.clear()
                    last_emit = now
                if not progress:
                    time.sleep(0.25)
        except KeyboardInterrupt:
            if buf:
                buf.sort(key=lambda ln: ln.split(" ", 1)[0] if ln else "")
                for ln in buf:
                    sys.stdout.write(ln)
                sys.stdout.flush()
            return 0
    finally:
        for _p, f in handles:
            try:
                f.close()
            except Exception:
                pass


# ---------- remotes (issue #63) ----------

_REMOTE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SECRET_KEYS = {"pass", "password"}


def _remote_usage() -> int:
    print(
        "usage: a8s remote                                         # list all\n"
        "       a8s remote <name>                                  # show one\n"
        "       a8s remote <name> <broker> <topic> [--<k> <v> ...]   # add or overwrite\n"
        "       a8s unremote <name>                                # remove\n"
        "\n"
        "Any --<opt> <value> pair past the broker and topic is passed verbatim\n"
        "to the transport (e.g. --user / --pass for mqtt). Unknown options are\n"
        "rejected by the transport at load time.",
        file=sys.stderr,
    )
    return 2


def _format_remote_summary(spec: dict) -> str:
    kind = spec.get("transport", "?")
    broker = spec.get("broker", "?")
    topic = spec.get("topic", "?")
    extras = " ".join(
        f"--{k}=***" if k in _SECRET_KEYS else f"--{k}={v}"
        for k, v in spec.items()
        if k not in {"transport", "broker", "topic"}
    )
    line = f"{kind} {broker} topic={topic}"
    if extras:
        line += f" {extras}"
    return line


def cmd_remote(args: list[str]) -> int:
    """`a8s remote` — manage cross-cluster remotes declared in `~/.a8s/network.json`.

    Forms (mirror `a8s alias`):
      a8s remote                                          list all
      a8s remote <name>                                   show one
      a8s remote <name> <broker> <topic> [--<k> <v> ...]  add or overwrite
      a8s unremote <name>                                 remove (see `cmd_unremote`)
    """
    if len(args) == 0:
        return _cmd_remote_list()
    if len(args) == 1:
        return _cmd_remote_show(args[0])
    if len(args) >= 3:
        return _cmd_remote_set(args[0], args[1], args[2], args[3:])
    return _remote_usage()


def _cmd_remote_list() -> int:
    cfg = load_network_config()
    remotes = cfg.get("remotes", {})
    if not remotes:
        print("(no remotes configured)")
        return 0
    name_w = max(len(n) for n in remotes)
    for name, spec in remotes.items():
        print(f"  {name.ljust(name_w)}  {_format_remote_summary(spec)}")
    return 0


def _cmd_remote_show(name: str) -> int:
    cfg = load_network_config()
    if name not in cfg["remotes"]:
        print(f"no remote named {name!r}", file=sys.stderr)
        return 1
    print(f"{name}: {_format_remote_summary(cfg['remotes'][name])}")
    return 0


def _cmd_remote_set(name: str, broker: str, topic: str, opt_tokens: list[str]) -> int:
    if not _REMOTE_NAME_RE.match(name):
        print(f"remote name must be alphanumeric (with -, _, .): {name!r}", file=sys.stderr)
        return 2
    extras: dict = {}
    i = 0
    while i < len(opt_tokens):
        tok = opt_tokens[i]
        if not tok.startswith("--") or len(tok) <= 2:
            print(f"expected --<opt> <value> pair, got: {tok!r}", file=sys.stderr)
            return _remote_usage()
        key = tok[2:]
        i += 1
        if i >= len(opt_tokens):
            print(f"missing value for {tok}", file=sys.stderr)
            return _remote_usage()
        if key in extras:
            print(f"duplicate option: {tok}", file=sys.stderr)
            return _remote_usage()
        extras[key] = opt_tokens[i]
        i += 1
    cfg = load_network_config()
    overwriting = name in cfg["remotes"]
    spec: dict = {"transport": "mqtt", "broker": broker, "topic": topic, **extras}
    cfg["remotes"][name] = spec
    save_network_config(cfg)
    verb = "updated" if overwriting else "added"
    print(f"{verb} remote {name} ({_format_remote_summary(spec)})")
    return 0


def cmd_unremote(args: list[str]) -> int:
    """`a8s unremote <name>` — remove a configured remote. Mirrors `unalias`'s
    shape so the surface stays uniform across registry primitives."""
    if len(args) != 1:
        print("usage: a8s unremote <name>", file=sys.stderr)
        return 2
    name = args[0]
    cfg = load_network_config()
    if name not in cfg["remotes"]:
        print(f"no remote named {name!r}", file=sys.stderr)
        return 1
    del cfg["remotes"][name]
    save_network_config(cfg)
    print(f"removed remote {name}")
    return 0


# ---------- storage services (issue #90) ----------


def _storage_usage() -> int:
    print(
        "usage: a8s storage                                          # list all\n"
        "       a8s storage <name>                                   # show one\n"
        "       a8s storage <name> <url> [--<k> <v> ...]             # add or overwrite\n"
        "       a8s unstorage <name>                                 # remove\n"
        "\n"
        "The service kind is auto-dispatched from the URL host. Any --<opt> <value>\n"
        "pair past the URL is passed verbatim to the service (e.g. --expiry_hours\n"
        "for tempfile.org). Unknown options are rejected by the service at load time.",
        file=sys.stderr,
    )
    return 2


def _format_storage_summary(spec: dict) -> str:
    kind = spec.get("service", "?")
    url = spec.get("url", "?")
    extras = " ".join(
        f"--{k}=***" if k in _SECRET_KEYS else f"--{k}={v}"
        for k, v in spec.items()
        if k not in {"service", "url"}
    )
    line = f"{kind} {url}"
    if extras:
        line += f" {extras}"
    return line


def cmd_storage(args: list[str]) -> int:
    """`a8s storage` — manage cross-cluster file services declared in
    `~/.a8s/network.json` (services map).

    Forms (mirror `a8s remote`):
      a8s storage                                 list all
      a8s storage <name>                          show one
      a8s storage <name> <url> [--<k> <v> ...]    add or overwrite
      a8s unstorage <name>                        remove (see `cmd_unstorage`)
    """
    if len(args) == 0:
        return _cmd_storage_list()
    if len(args) == 1:
        return _cmd_storage_show(args[0])
    if len(args) >= 2:
        return _cmd_storage_set(args[0], args[1], args[2:])
    return _storage_usage()


def _cmd_storage_list() -> int:
    cfg = load_network_config()
    services = cfg.get("services", {})
    if not services:
        print("(no storage services configured)")
        return 0
    name_w = max(len(n) for n in services)
    for name, spec in services.items():
        print(f"  {name.ljust(name_w)}  {_format_storage_summary(spec)}")
    return 0


def _cmd_storage_show(name: str) -> int:
    cfg = load_network_config()
    if name not in cfg["services"]:
        print(f"no storage named {name!r}", file=sys.stderr)
        return 1
    print(f"{name}: {_format_storage_summary(cfg['services'][name])}")
    return 0


def _cmd_storage_set(name: str, url: str, opt_tokens: list[str]) -> int:
    if not _REMOTE_NAME_RE.match(name):
        print(f"storage name must be alphanumeric (with -, _, .): {name!r}", file=sys.stderr)
        return 2
    extras: dict = {}
    i = 0
    while i < len(opt_tokens):
        tok = opt_tokens[i]
        if not tok.startswith("--") or len(tok) <= 2:
            print(f"expected --<opt> <value> pair, got: {tok!r}", file=sys.stderr)
            return _storage_usage()
        key = tok[2:]
        i += 1
        if i >= len(opt_tokens):
            print(f"missing value for {tok}", file=sys.stderr)
            return _storage_usage()
        if key in extras:
            print(f"duplicate option: {tok}", file=sys.stderr)
            return _storage_usage()
        extras[key] = opt_tokens[i]
        i += 1
    kind = detect_service_kind(url)
    if kind is None:
        print(
            f"no storage service matches URL {url!r} (known kinds: tempfile_org)",
            file=sys.stderr,
        )
        return 2
    cfg = load_network_config()
    overwriting = name in cfg["services"]
    spec: dict = {"service": kind, "url": url, **extras}
    cfg["services"][name] = spec
    save_network_config(cfg)
    verb = "updated" if overwriting else "added"
    print(f"{verb} storage {name} ({_format_storage_summary(spec)})")
    return 0


def cmd_unstorage(args: list[str]) -> int:
    """`a8s unstorage <name>` — remove a configured storage service. Mirrors
    `unremote`'s shape so the surface stays uniform across configurable
    cross-cluster primitives."""
    if len(args) != 1:
        print("usage: a8s unstorage <name>", file=sys.stderr)
        return 2
    name = args[0]
    cfg = load_network_config()
    if name not in cfg["services"]:
        print(f"no storage named {name!r}", file=sys.stderr)
        return 1
    del cfg["services"][name]
    save_network_config(cfg)
    print(f"removed storage {name}")
    return 0


def cmd_health() -> int:
    """`a8s health` — test connectivity of all configured remotes and storage services."""
    import tempfile
    from network import load_remotes, load_services

    errors = 0

    remotes = load_remotes()
    if not remotes:
        print("remotes: (none configured)")
    for t in remotes:
        name = getattr(t, "name", t.__class__.__name__)
        try:
            t.start(lambda *_: None)
            connected = t.is_connected() if hasattr(t, "is_connected") else True
            t.stop()
            if connected:
                print(f"remote {name}: OK")
            else:
                print(f"remote {name}: FAIL (connected but is_connected=False)")
                errors += 1
        except Exception as e:
            print(f"remote {name}: FAIL ({e})")
            errors += 1

    services = load_services()
    if not services:
        print("storage: (none configured)")
    for svc in services:
        name = getattr(svc, "name", svc.__class__.__name__)
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
        tmp.write("a8s health check")
        tmp.close()
        tmp_path = Path(tmp.name)
        try:
            url = svc.store(tmp_path)
            dl_dir = Path(tempfile.mkdtemp())
            dl_dest = dl_dir / "health-check.txt"
            ok = svc.retrieve(url, dl_dest)
            if ok and dl_dest.is_file() and dl_dest.read_text().strip() == "a8s health check":
                print(f"storage {name}: OK (upload + download verified)")
            elif ok:
                print(f"storage {name}: WARN (download succeeded but content mismatch)")
                errors += 1
            else:
                print(f"storage {name}: FAIL (retrieve returned False)")
                errors += 1
            dl_dest.unlink(missing_ok=True)
            dl_dir.rmdir()
        except Exception as e:
            print(f"storage {name}: FAIL ({e})")
            errors += 1
        finally:
            tmp_path.unlink(missing_ok=True)

    agents = load_registry()
    print(f"agents: {len(agents)} registered")
    for name, info in agents.items():
        root = Path(info.get("root", ""))
        if not root.is_dir():
            print(f"  {name}: WARN (root missing: {root})")
            errors += 1
        else:
            print(f"  {name}: OK ({root})")

    return 1 if errors else 0
