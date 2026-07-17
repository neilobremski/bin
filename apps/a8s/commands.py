"""a8s commands — every cmd_* function dispatched by cli.py.

Grouped by section:
  registry mgmt    — add, define, ls, discover, install
  aliases          — alias, unalias, aliases
  namespaces       — namespace, unnamespace, namespaces
  process control  — start, run, step, stop, kill, exit, ps
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
from datetime import datetime
from pathlib import Path

from core import (
    ENTRYPOINT,
    SKILLS_DIR,
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
    load_namespaces,
    load_registry,
    participants_from_registry,
    resolve_name,
    resolve_recipient,
    save_aliases,
    save_namespaces,
    save_registry,
)
from txlog import read_events
from ulid import is_ulid


# ---------- skill installation helpers ----------

def _link_symlink(link: Path, target: Path) -> tuple[bool, str | None]:
    """Create or refresh `link` -> `target`. Returns (ok, error_message)."""
    target = target.resolve()
    if link.is_symlink():
        if os.readlink(link) == str(target):
            return True, None
        link.unlink()
    elif link.exists():
        return False, f"{link} exists and is not a symlink; refusing to overwrite"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return True, None


def _install_skill_claude(skill_dir: Path, base: Path) -> str:
    dest = base / ".claude" / "skills" / skill_dir.name / "SKILL.md"
    ok, err = _link_symlink(dest, skill_dir / "SKILL.md")
    if not ok:
        return f"  claude: {err}"
    rel = dest.relative_to(base) if dest.is_relative_to(base) else dest
    return f"  claude: linked {rel}"


def _install_skill_cursor(skill_dir: Path, base: Path) -> str:
    dest = base / ".cursor" / "skills" / skill_dir.name / "SKILL.md"
    ok, err = _link_symlink(dest, skill_dir / "SKILL.md")
    if not ok:
        return f"  cursor: {err}"
    rel = dest.relative_to(base) if dest.is_relative_to(base) else dest
    return f"  cursor: linked {rel}"


def _install_skill_agy(skill_dir: Path, base: Path) -> str:
    if shutil.which("agy") is None:
        return "  agy: not on PATH; skipping"
    return "  agy: plugin install not yet supported; skipping"


def _install_skill_codex(skill_dir: Path, base: Path) -> str:
    codex_skills = base / ".codex" / "skills"
    target = codex_skills / skill_dir.name
    ok, err = _link_symlink(target, skill_dir)
    if not ok:
        return f"  codex: {err}"
    rel = target.relative_to(base) if target.is_relative_to(base) else target
    return f"  codex: linked {rel}"


def _install_skill_copilot(skill_dir: Path, base: Path) -> str:
    if shutil.which("copilot") is None:
        return "  copilot: not on PATH; skipping"
    dest = base / ".claude" / "skills" / skill_dir.name / "SKILL.md"
    ok, err = _link_symlink(dest, skill_dir / "SKILL.md")
    if not ok:
        return f"  copilot: {err}"
    return f"  copilot: linked {dest.relative_to(base)} (Copilot also loads .github/copilot-instructions.md when present)"


def _install_skill_opencode(skill_dir: Path, base: Path) -> str:
    if shutil.which("opencode") is None:
        return "  opencode: not on PATH; skipping"
    return _install_skill_claude(skill_dir, base).replace("  claude:", "  opencode:", 1)


def _install_skills_into(base: Path) -> int:
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
    print(f"installing {len(skill_dirs)} skill(s) into {base}:")
    for skill_dir in skill_dirs:
        print(f"\n[{skill_dir.name}]")
        print(_install_skill_claude(skill_dir, base))
        print(_install_skill_cursor(skill_dir, base))
        print(_install_skill_agy(skill_dir, base))
        print(_install_skill_codex(skill_dir, base))
        print(_install_skill_copilot(skill_dir, base))
        print(_install_skill_opencode(skill_dir, base))
    return 0


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
    namespaces = load_namespaces()
    # A prefix already bound to this exact name is the agent's own namespace
    # (#175) — re-adding the node is fine. A prefix bound to a *different* agent
    # would be shadowed by `tell <name>` (namespace beats agent), so it stands.
    for k, bound in namespaces.items():
        if k.lower() == name and str(bound).strip().lower() != name:
            print(f"namespace already exists with prefix: {k} (bound to {bound}) — pick a different agent name", file=sys.stderr)
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
    becomes empty as a result. Cascades into namespaces the same way: any
    prefix bound to <name> is unbound (no orphans). Wipes the on-disk
    per-agent dir (~/.a8s/agents/<NAME>/) — inbox, trash, log, pid file
    all gone."""
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
    namespaces = load_namespaces()
    unbound = sorted(
        p for p, target in namespaces.items()
        if str(target).lower() == name.lower()
    )
    if unbound:
        for p in unbound:
            del namespaces[p]
        save_namespaces(namespaces)
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
    if unbound:
        print(f"  unbound namespaces: {', '.join(unbound)}")
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


def _print_table(headers: list[str], rows: list[tuple[str, ...]]) -> None:
    """Docker-style aligned table: left-justified columns, three-space gutters,
    no padding on the trailing column so lines don't carry dead whitespace."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells: tuple[str, ...]) -> str:
        last = len(cells) - 1
        return "   ".join(
            cell.ljust(widths[i]) if i < last else cell
            for i, cell in enumerate(cells)
        )

    print(fmt(tuple(headers)))
    for row in rows:
        print(fmt(row))


def _pid_uptime(name: str) -> str:
    """Coarse uptime from the pid file's mtime — a cheap stat, no bookkeeping.
    The pid file is written when a process claims the node, so its age tracks
    how long the node has been running under that handler."""
    try:
        mtime = pid_path(name).stat().st_mtime
    except OSError:
        return "?"
    secs = max(0, int(time.time() - mtime))
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def cmd_ls(args: list[str] | None = None) -> int:
    """`a8s ls` — list every registered node, running or not (docker/ollama
    style). Columns: NAME, STATUS, KIND, ROOT, plus NAMESPACES when any prefix
    is bound. `-q` prints just names, one per line, for scripting.

    STATUS is `running (pid N)` or `stopped`; KIND is the definition basename
    (default fallback applies when the registry has no `definition` field)."""
    args = args or []
    quiet = "-q" in args
    reg = load_registry()
    if not reg:
        if not quiet:
            print("(no nodes registered — use `a8s add <name> <dir>`)")
        return 0

    names = sorted(reg, key=str.lower)
    if quiet:
        for name in names:
            print(name)
        return 0

    bindings: dict[str, list[str]] = {}
    for prefix, agent in load_namespaces().items():
        bindings.setdefault(agent.lower(), []).append(f"{prefix}:")

    rows: list[tuple[str, ...]] = []
    for name in names:
        info = reg[name]
        pid = _read_handler_pid(name)
        status = f"running (pid {pid})" if pid is not None else "stopped"
        defn = info.get("definition") or str(default_definition_path("default"))
        kind = Path(defn).stem
        root = info.get("root", "?")
        ns = " ".join(sorted(bindings.get(name.lower(), [])))
        rows.append((name, status, kind, root, ns))

    if any(row[4] for row in rows):
        _print_table(["NAME", "STATUS", "KIND", "ROOT", "NAMESPACES"], rows)
    else:
        _print_table(["NAME", "STATUS", "KIND", "ROOT"], [r[:4] for r in rows])
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


def cmd_install(args: list[str]) -> int:
    """Install bundled skills into an agent directory (default CWD) or --global home."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="a8s install",
        description=(
            "Install a8s skills into an agent directory (default: CWD). "
            "Creates .claude/skills/, .cursor/skills/, and .codex/skills/ "
            "symlinks under the target. Use --global to install into user home instead."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  cd ~/projects/my-agent && a8s install\n"
            "  a8s install /path/to/agent\n"
            "  a8s install --global\n"
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="agent root to install into (default: current directory)",
    )
    parser.add_argument(
        "--global",
        dest="global_install",
        action="store_true",
        help="install into user home (~/.claude/skills, ~/.cursor/skills, ~/.codex/skills)",
    )
    try:
        parsed = parser.parse_args(args)
        if parsed.global_install and parsed.path is not None:
            parser.error("path argument conflicts with --global")
    except SystemExit as e:
        return int(e.code if e.code is not None else 0)

    if parsed.global_install:
        base = Path.home()
    else:
        base = Path(parsed.path or ".").expanduser().resolve()
        if not base.is_dir():
            print(f"not a directory: {base}", file=sys.stderr)
            return 1

    return _install_skills_into(base)


DEFAULT_CLIENT_BIN = Path("/usr/local/bin")
DEFAULT_CLIENT_LIB = Path("/usr/local/lib/a8s")
_A8S_SOURCE = Path(__file__).resolve().parent


def _install_ignore(_dir: str, names: list[str]) -> set[str]:
    skip = {"__pycache__", ".pytest_cache", "tests"}
    return {n for n in names if n in skip or n.endswith(".pyc")}


def _install_client_wrapper(bin_path: Path, a8s_entry: Path) -> None:
    entry = a8s_entry.resolve()
    wrapper = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'exec python3 "{entry}" tell "$@"\n'
    )
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    if bin_path.is_symlink() or bin_path.exists():
        bin_path.unlink()
    bin_path.write_text(wrapper, encoding="utf-8")
    bin_path.chmod(0o755)


def _chmod_install_tree(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        Path(dirpath).chmod(0o755)
        for name in filenames:
            (Path(dirpath) / name).chmod(0o644)


def _install_a8s_tree(lib_dir: Path) -> Path:
    if lib_dir.exists():
        shutil.rmtree(lib_dir)
    shutil.copytree(_A8S_SOURCE, lib_dir, ignore=_install_ignore)
    _chmod_install_tree(lib_dir)
    entry = lib_dir / "a8s.py"
    if not entry.is_file():
        raise FileNotFoundError(f"missing {entry}")
    return entry


def cmd_install_client(args: list[str]) -> int:
    """Install a8s tree + tell wrapper to /usr/local for unprivileged agent users."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="a8s install-client",
        description=(
            "Install a copy of apps/a8s for agent users. "
            "Copies the a8s package to --lib-dir (excluding tests) "
            "and writes a tell wrapper to --bin-dir/tell. Re-running overwrites "
            "any previous install."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  sudo a8s install-client\n"
            "  sudo a8s install-client /opt/a8s\n"
            "  a8s install-client --bin-dir /tmp/bin --lib-dir /tmp/lib/a8s\n"
        ),
    )
    parser.add_argument(
        "dest",
        nargs="?",
        type=Path,
        help=f"install root (default: {DEFAULT_CLIENT_LIB})",
    )
    parser.add_argument(
        "--bin-dir",
        type=Path,
        default=DEFAULT_CLIENT_BIN,
        help=f"directory for tell wrapper (default: {DEFAULT_CLIENT_BIN})",
    )
    parser.add_argument(
        "--lib-dir",
        type=Path,
        default=None,
        help=f"install root (same as positional dest; default: {DEFAULT_CLIENT_LIB})",
    )
    try:
        parsed = parser.parse_args(args)
        if parsed.dest is not None and parsed.lib_dir is not None:
            parser.error("dest argument conflicts with --lib-dir")
    except SystemExit as e:
        return int(e.code if e.code is not None else 0)

    bin_dir = parsed.bin_dir.expanduser().resolve()
    lib_dir = (parsed.dest or parsed.lib_dir or DEFAULT_CLIENT_LIB).expanduser().resolve()
    system_install = (
        str(bin_dir).startswith("/usr/local")
        or str(lib_dir).startswith("/usr/local")
    )
    if system_install and os.geteuid() != 0:
        print(
            "a8s install-client: must run as root for /usr/local "
            "(sudo a8s install-client)",
            file=sys.stderr,
        )
        return 1

    if not _A8S_SOURCE.is_dir():
        print(f"a8s install-client: source missing: {_A8S_SOURCE}", file=sys.stderr)
        return 1

    try:
        a8s_entry = _install_a8s_tree(lib_dir)
        _install_client_wrapper(bin_dir / "tell", a8s_entry)
    except OSError as e:
        print(f"a8s install-client: {e}", file=sys.stderr)
        return 1

    print(f"installed tell -> {bin_dir / 'tell'}")
    print(f"installed a8s -> {a8s_entry}")
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
    for k in load_namespaces():
        if k.lower() == alias_name:
            print(f"namespace already exists with prefix: {k} — pick a different alias", file=sys.stderr)
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


# ---------- namespace commands (issue #148) ----------

def cmd_namespace(args: list[str]) -> int:
    """`a8s namespace` — bind address prefixes to node agents.

    Forms (mirror `a8s alias`):
      a8s namespace                      list all
      a8s namespace <prefix>             show one binding
      a8s namespace <prefix> <agent>     bind or rebind

    A bound prefix routes every `<prefix>:<sub-address>` recipient to the
    single bound agent; the full address stays in the message's `to` so the
    node's `$RECIPIENT` carries it verbatim and the node can self-route
    internally. The target must be a registered agent, not an alias —
    namespace delegation is single-delivery by design, the opposite of
    alias fan-out. Prefixes share the agent/alias name grammar (lowercase
    canonical form). A prefix may match the name of the agent it binds to (a
    node owning its own namespace, #175) but must not collide with an alias or
    with any other agent."""
    if len(args) == 0:
        return cmd_namespaces()
    if len(args) == 1:
        return _cmd_namespace_show(args[0])
    if len(args) != 2:
        print("usage: a8s namespace <prefix> <agent>   # bind or rebind", file=sys.stderr)
        print("       a8s namespace <prefix>           # show one", file=sys.stderr)
        print("       a8s namespace                    # list", file=sys.stderr)
        return 2
    raw_prefix, raw_target = args
    try:
        prefix = canonical_name(raw_prefix)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        target = canonical_name(raw_target)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    agents = load_registry()
    aliases = load_aliases()
    # A prefix may match the name of the agent it binds to — that's a node
    # owning its own namespace (#175), so cross-wall traffic is attributed to
    # `s1l`, not `s1l-node`. It must not match any *other* agent's name, which
    # `tell <prefix>` would silently shadow (namespace beats agent in resolve).
    for k in agents:
        if k.lower() == prefix and k.lower() != target:
            print(f"agent already exists with name: {k} — pick a different prefix", file=sys.stderr)
            return 1
    for k in aliases:
        if k.lower() == prefix:
            print(f"alias already exists with name: {k} — pick a different prefix", file=sys.stderr)
            return 1
    if any(k.lower() == target for k in aliases):
        print(f"namespace target must be an agent, not an alias: {raw_target!r}", file=sys.stderr)
        return 1
    target_resolved: str | None = None
    for k in agents:
        if k.lower() == target:
            target_resolved = k
            break
    if target_resolved is None:
        print(f"unknown agent {raw_target!r}", file=sys.stderr)
        return 1
    namespaces = load_namespaces()
    previous = namespaces.get(prefix)
    namespaces[prefix] = target_resolved
    save_namespaces(namespaces)
    if previous is not None and str(previous).lower() != target_resolved.lower():
        print(f"rebound {prefix}: -> {target_resolved} (was {previous})")
    else:
        print(f"bound {prefix}: -> {target_resolved}")
    return 0


def cmd_unnamespace(args: list[str]) -> int:
    """`a8s unnamespace <prefix>` — remove a namespace binding. Mirrors
    `unalias`'s shape so the surface stays uniform across registry
    primitives."""
    if len(args) != 1:
        print("usage: a8s unnamespace <prefix>", file=sys.stderr)
        return 2
    target = args[0].strip().lower()
    namespaces = load_namespaces()
    canonical = next((k for k in namespaces if k.lower() == target), None)
    if canonical is None:
        print(f"no namespace named {args[0]!r}", file=sys.stderr)
        return 1
    del namespaces[canonical]
    save_namespaces(namespaces)
    print(f"removed namespace {canonical}")
    return 0


def _namespace_binding_tail(target: str) -> str:
    known = {n.lower() for n in load_registry()}
    return "" if str(target).lower() in known else f"  [unknown agent {target!r}]"


def _cmd_namespace_show(name: str) -> int:
    """`a8s namespace <prefix>` — show one binding. Mirrors `alias <name>`."""
    try:
        target = canonical_name(name)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    namespaces = load_namespaces()
    canonical = next((k for k in namespaces if k.lower() == target), None)
    if canonical is None:
        print(f"no namespace named {name!r}", file=sys.stderr)
        return 1
    bound = namespaces[canonical]
    print(f"{canonical}: -> {bound}{_namespace_binding_tail(bound)}")
    return 0


def cmd_namespaces() -> int:
    """`a8s namespaces` — list every namespace prefix and its bound agent."""
    namespaces = load_namespaces()
    if not namespaces:
        print("(no namespaces — use `a8s namespace <prefix> <agent>` to bind one)")
        return 0
    width = max(len(p) for p in namespaces)
    for prefix in sorted(namespaces, key=str.lower):
        bound = namespaces[prefix]
        print(f"  {prefix.ljust(width)}  -> {bound}{_namespace_binding_tail(bound)}")
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


def cmd_ps(args: list[str] | None = None) -> int:
    """`a8s ps` — list only running node processes (docker/ollama style).
    Columns: NAME, PID, UPTIME, ROOT. `-q` prints just names, one per line."""
    args = args or []
    quiet = "-q" in args
    reg = load_registry()
    running: list[tuple[str, int, str]] = []
    for name in sorted(reg, key=str.lower):
        pid = _read_handler_pid(name)
        if pid is None:
            continue
        running.append((name, pid, reg[name].get("root", "?")))

    if not running:
        if not quiet:
            print("no nodes running (try: a8s ls)")
        return 0

    if quiet:
        for name, _, _ in running:
            print(name)
        return 0

    rows = [(name, str(pid), _pid_uptime(name), root) for name, pid, root in running]
    _print_table(["NAME", "PID", "UPTIME", "ROOT"], rows)
    return 0


# ---------- messaging commands ----------

def cmd_tell(args: list[str]) -> int:
    """`a8s tell <name> <msg>` — write a single outbox message; `name` may be
    an agent or alias. Fan-out to alias members happens at routing time and
    preserves the original `to` (alias name) — strict opacity, mailing-list
    style: the recipient knows it came via the list, not who else got it."""
    from tell import tell_main

    return tell_main(args)


def cmd_tells(args: list[str]) -> int:
    """`a8s tells [--timeout SEC]` — block until the next message lands in this
    node's inbox, print each new envelope, and exit 0; exit 1 on timeout. The
    receive-side complement of `tell`, resolved from the same `TELL_OUTBOX_DIR`."""
    from tells import tells_main

    return tells_main(args)


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


# ---------- config ----------

def cmd_config(args: list[str]) -> int:
    """`a8s config` — read or write `~/.a8s/settings.json`; list all knobs."""
    import settings as sm

    if not args:
        machine = {r[0]: r for r in sm.list_settings()}
        for group_label, knobs in sm.list_catalog():
            print(f"\n{group_label}")
            for knob in knobs:
                if knob.writable:
                    _key, _stored, effective, default, source = machine[knob.key]
                    print(f"  {knob.key}: {effective}  ({source}; default {default})")
                    if knob.note:
                        print(f"    {knob.note}")
                else:
                    default = knob.default if knob.default is not None else "—"
                    print(f"  {knob.key}: {default}")
                    if knob.note:
                        print(f"    {knob.note}")
        print()
        return 0

    sub = args[0]
    if sub == "get":
        if len(args) != 2:
            print("usage: a8s config get <key>", file=sys.stderr)
            return 2
        key = args[1]
        if sm.is_writable(key):
            print(sm.get_setting(key))
            return 0
        knob = sm.knob_by_key(key)
        if knob is not None:
            val = knob.default if knob.default is not None else ""
            print(val)
            if knob.note:
                print(f"({knob.note})", file=sys.stderr)
            return 0
        print(f"unknown setting {key!r}", file=sys.stderr)
        return 1

    if sub == "set":
        if len(args) != 3:
            print("usage: a8s config set <key> <value>", file=sys.stderr)
            return 2
        try:
            sm.set_setting(args[1], args[2])
        except KeyError:
            print(f"unknown setting {args[1]!r}", file=sys.stderr)
            return 1
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(f"{args[1]}={sm.get_setting(args[1])}")
        return 0

    if sub == "unset":
        if len(args) != 2:
            print("usage: a8s config unset <key>", file=sys.stderr)
            return 2
        try:
            removed = sm.unset_setting(args[1])
        except KeyError:
            print(f"unknown setting {args[1]!r}", file=sys.stderr)
            return 1
        if not removed:
            print(f"{args[1]}: not set in settings.json")
        else:
            print(f"{args[1]} unset (effective {sm.get_setting(args[1])})")
        return 0

    print(
        "usage: a8s config [get <key> | set <key> <value> | unset <key>]",
        file=sys.stderr,
    )
    return 2


# ---------- convo ----------

def cmd_convo(args: list[str]) -> int:
    """`a8s convo <name> [--limit N] [-f|--follow] [--heading-out T] [--heading-in T]` —
    markdown history of messages to or from an agent."""
    from convo import (
        DEFAULT_HEADING_IN,
        DEFAULT_HEADING_OUT,
        follow_conversation,
        format_conversation,
    )

    if not args:
        print(
            "usage: a8s convo <name> [--limit N] [-f|--follow] "
            "[--heading-out TEMPLATE] [--heading-in TEMPLATE]",
            file=sys.stderr,
        )
        return 2

    name: str | None = None
    limit = 10
    follow = False
    heading_out = DEFAULT_HEADING_OUT
    heading_in = DEFAULT_HEADING_IN
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-f", "--follow"):
            follow = True
            i += 1
            continue
        if a == "--limit":
            if i + 1 >= len(args):
                print("--limit requires a number", file=sys.stderr)
                return 2
            try:
                limit = int(args[i + 1])
            except ValueError:
                print("--limit requires a number", file=sys.stderr)
                return 2
            i += 2
            continue
        if a == "--heading-out":
            if i + 1 >= len(args):
                print("--heading-out requires a template", file=sys.stderr)
                return 2
            heading_out = args[i + 1]
            i += 2
            continue
        if a == "--heading-in":
            if i + 1 >= len(args):
                print("--heading-in requires a template", file=sys.stderr)
                return 2
            heading_in = args[i + 1]
            i += 2
            continue
        if a.startswith("-"):
            print(f"unknown convo arg: {a!r}", file=sys.stderr)
            return 2
        if name is not None:
            print(f"unexpected argument: {a!r}", file=sys.stderr)
            return 2
        name = a
        i += 1

    if name is None:
        print(
            "usage: a8s convo <name> [--limit N] [-f|--follow] "
            "[--heading-out TEMPLATE] [--heading-in TEMPLATE]",
            file=sys.stderr,
        )
        return 2

    match = resolve_recipient(name)
    if match is None:
        print(f"no agent named {name!r}", file=sys.stderr)
        return 1
    agent_name = match[0]

    if follow:
        try:
            follow_conversation(
                agent_name,
                limit=limit,
                heading_out=heading_out,
                heading_in=heading_in,
            )
        except KeyboardInterrupt:
            pass
        return 0

    text = format_conversation(
        agent_name,
        limit=limit,
        heading_out=heading_out,
        heading_in=heading_in,
    )
    if text:
        print(text)
    return 0


# ---------- trace / logs ----------

def cmd_trace(args: list[str]) -> int:
    if len(args) != 1 or not is_ulid(args[0]):
        print("usage: a8s trace <ULID>", file=sys.stderr)
        return 2
    msg_id = args[0].upper()
    events = read_events(msg_id)
    if not events:
        print(f"no transaction events for {msg_id}", file=sys.stderr)
        return 1
    print(f"trace {msg_id}")
    for event in events:
        fields = [event["timestamp"], event["event"]]
        for key in ("from", "to", "remote", "files", "detail"):
            if event[key]:
                fields.append(f"{key}={event[key]}")
        print("  " + " ".join(fields))
    return 0


# ---------- logs ----------

def _parse_log_line_ts(line: str) -> datetime | None:
    if not line:
        return None
    head = line.split(" ", 1)[0]
    if head.endswith("Z"):
        head = head[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(head)
    except ValueError:
        return None


def _read_agent_log(path: Path) -> list[str]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def _merge_log_lines(paths: list[Path]) -> list[str]:
    tagged: list[tuple[tuple, str]] = []
    for fi, path in enumerate(paths):
        for li, line in enumerate(_read_agent_log(path)):
            ts = _parse_log_line_ts(line)
            key = (0, ts, fi, li) if ts is not None else (1, fi, li)
            tagged.append((key, line))
    tagged.sort(key=lambda item: item[0])
    return [line for _key, line in tagged]


def _dump_logs(paths: list[Path], tail_n: int | None) -> None:
    existing = [p for p in paths if p.is_file()]
    if not existing:
        return
    lines = _read_agent_log(existing[0]) if len(existing) == 1 else _merge_log_lines(existing)
    if tail_n is not None:
        lines = lines[-tail_n:]
    for line in lines:
        sys.stdout.write(line)
    sys.stdout.flush()


def cmd_logs(args: list[str]) -> int:
    """Read each named agent's log.txt. One agent: append order (file order).
    Multiple agents: merge by leading ISO timestamp. -f follows; multi-agent
    follow uses a short ordering buffer."""
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

    # Initial dump: one file in append order; multiple files merge by timestamp.
    _dump_logs(paths, tail_n)

    if not follow:
        return 0

    handles: list[tuple[int, Path, "os.IOBase"]] = []
    try:
        for fi, p in enumerate(paths):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch(exist_ok=True)
            f = p.open("r", encoding="utf-8", errors="replace")
            f.seek(0, 2)
            handles.append((fi, p, f))
        if len(handles) == 1:
            try:
                while True:
                    ln = handles[0][2].readline()
                    if not ln:
                        time.sleep(0.25)
                        continue
                    sys.stdout.write(ln)
                    sys.stdout.flush()
            except KeyboardInterrupt:
                return 0
        buf: list[tuple[tuple, str]] = []
        seq = 0
        last_emit = time.time()
        try:
            while True:
                progress = False
                for fi, _path, f in handles:
                    while True:
                        ln = f.readline()
                        if not ln:
                            break
                        ts = _parse_log_line_ts(ln)
                        key = (0, ts, fi, seq) if ts is not None else (1, fi, seq)
                        buf.append((key, ln))
                        seq += 1
                        progress = True
                now = time.time()
                if buf and (not progress or now - last_emit >= 1.0):
                    buf.sort(key=lambda item: item[0])
                    for _key, ln in buf:
                        sys.stdout.write(ln)
                    sys.stdout.flush()
                    buf.clear()
                    last_emit = now
                if not progress:
                    time.sleep(0.25)
        except KeyboardInterrupt:
            if buf:
                buf.sort(key=lambda item: item[0])
                for _key, ln in buf:
                    sys.stdout.write(ln)
                sys.stdout.flush()
            return 0
    finally:
        for _fi, _p, f in handles:
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
