"""a8s — Agent Infinity System.

Filesystem-based message router for independent Claude Code, Gemini CLI,
and Codex CLI project directories ("participants") to communicate.

Surface (CLI):
  add <name> <dir>            — explicit agent registration (errors on dup)
  agents                      — list every registered agent + definition status
  discover <path>             — walk dir tree for marker files; print suggested
                                add/define commands (read-only, no mutation)
  define <name> [<path>]      — show or set the JSON definition that drives an
                                agent's wake invocation + prompt formatting
  start <name>                — spawn a detached daemon to handle <name>
  run <name>                  — foreground attached loop handling <name>
  step <name>                 — take handling, one route+drain pass, release
  stop <name>                 — SIGTERM the handler of <name> (graceful detach)
  kill <name>                 — graceful SIGTERM, then forced subprocess-group kill
  exit                        — SIGTERM every running handler
  ls                          — list only running agents (with handler PIDs)
  alias <alias> <member>      — add member to alias (creates if new); bare lists
  unalias <alias> [<member>]  — remove member, or remove the whole alias
  aliases                     — list every alias and its members
  prompt <name> <msg>         — queue a senderless message; alias = per-member
  tell <name> <msg>           — routed message; alias = fan-out at routing
  clear <name>                — wipe mailboxes + flag fresh; alias iterates
  install                     — install canonical skills into Claude / Gemini /
                                Codex user scope
  logs <name>... [--tail N] [-f] — read per-agent logs; merge-sort multiples

`a8s` with no command prints help. There is no auto-discovery — agents must be
explicitly registered with `a8s add` (use `a8s discover` to find candidates).

State:
  ~/.a8s/a8s.json             — registry (name -> {root, aliases, definition?}).
                                Agents without `definition` cannot wake.
  ~/.a8s/agents/<NAME>/       — per-agent internal dir:
    inbox/                    — pending messages routed in (drained by wake_once)
    trash/                    — processed messages
    log.txt                   — agent-scoped log (wake events, subprocess output,
                                routing involving this agent)
    pid                       — current handler process; written via O_CREAT|O_EXCL
                                atomic claim. `start`/`run`/`step` always win,
                                asking any prior handler to detach first. (Once
                                aliases land, multiple agents may share the same
                                handler PID — an agent is *handled by* a process,
                                not captured by it.)
  ~/.a8s/log.txt              — supervisor log: process-scoped events only
                                (loop lifecycle, registration, etc.)
  <agent-root>/.outbox/       — agent writes here; route_outboxes re-stamps `from`
                                to the enclosing participant on every read so an
                                agent can't spoof the sender by hand-writing JSON

Definitions:
  apps/a8s/definitions/{claude,gemini,codex}.json — built-in defaults selected by
                                kind; encode argv (with `$PROMPT` placeholder) for
                                each (fresh × unrestricted) variant plus prompt
                                templates. Override per-agent via `a8s define`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MARKER_FILES = {
    "CLAUDE.md": "claude",
    "GEMINI.md": "gemini",
    "CODEX.md": "codex",
}

NAME_RE = re.compile(r"[A-Za-z0-9]+")

PRINT_LOCK: threading.Lock | None = None
UNRESTRICTED: bool = False


def _a8s_dir() -> Path:
    base = Path.home() / ".a8s"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _log_path() -> Path:
    """Process-scoped log: loop start/stop, registration, things without a
    specific agent context. Per-agent activity goes in `agent_log_path(name)`."""
    return _a8s_dir() / "log.txt"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _preview(content: str, n: int = 80) -> str:
    """Single-line snippet of `content` for log readability."""
    s = (content or "").replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def agent_dir(name: str) -> Path:
    """Per-agent internal directory under ~/.a8s/. Holds inbox/, trash/,
    log.txt, and (phase 3b) the pid file."""
    return _a8s_dir() / "agents" / _safe_name(name)


def inbox_dir(name: str) -> Path:
    return agent_dir(name) / "inbox"


def trash_dir(name: str) -> Path:
    return agent_dir(name) / "trash"


def agent_log_path(name: str) -> Path:
    return agent_dir(name) / "log.txt"


def outbox_dir(root: Path) -> Path:
    """Outbox lives **inside the agent's own dir** so the agent can write to it
    even under a strict workspace sandbox (codex --full-auto). Inbox and trash
    stay isolated under ~/.a8s/agents/<NAME>/ where the agent never sees them.

    `route_outboxes()` re-stamps the `from` field to the enclosing participant's
    name on every read, so an agent can't spoof a senderless prompt by writing
    a JSON with `from: ""`.
    """
    return root / ".outbox"


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append(path: Path, ts_line: str) -> None:
    """Append `ts_line` (already timestamp-prefixed and newline-terminated) to
    `path`. Best-effort: a missing directory is created lazily; OSError swallows."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(ts_line)
    except OSError:
        pass


def _emit_supervisor(line: str) -> None:
    """Stdout + supervisor log (process-scoped events only)."""
    sys.stdout.write(line)
    sys.stdout.flush()
    ts_line = f"{_ts()} {line}"
    if not ts_line.endswith("\n"):
        ts_line += "\n"
    _append(_log_path(), ts_line)


def _emit_agent(name: str, line: str) -> None:
    """Stdout + per-agent log only. Does NOT write to the supervisor log —
    agent-scoped events live in `~/.a8s/agents/<NAME>/log.txt` and `a8s logs`
    reads them directly."""
    sys.stdout.write(line)
    sys.stdout.flush()
    ts_line = f"{_ts()} {line}"
    if not ts_line.endswith("\n"):
        ts_line += "\n"
    _append(agent_log_path(name), ts_line)


def out(text: str = "", end: str = "\n") -> None:
    """Process-scoped output (loop lifecycle, registration, etc.). For
    agent-scoped lines use `out_agent(name, ...)`."""
    line = text + end
    if PRINT_LOCK is not None:
        with PRINT_LOCK:
            _emit_supervisor(line)
    else:
        _emit_supervisor(line)


def out_agent(name: str, text: str = "", end: str = "\n") -> None:
    """Agent-scoped output. Lands in `~/.a8s/agents/<NAME>/log.txt`."""
    line = text + end
    if PRINT_LOCK is not None:
        with PRINT_LOCK:
            _emit_agent(name, line)
    else:
        _emit_agent(name, line)


@dataclass(frozen=True)
class Participant:
    name: str
    root: Path


# ---------- discovery ----------

def parse_name(marker_path: Path) -> str | None:
    try:
        with marker_path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.lstrip()
                if not line.startswith("#"):
                    continue
                rest = line.lstrip("#").strip()
                m = NAME_RE.match(rest)
                return m.group(0) if m else None
    except OSError:
        return None
    return None


def find_participant(parts: list[Participant], query: str) -> Participant | None:
    q = query.strip().lower()
    for p in parts:
        if p.name.lower() == q:
            return p
    return None


def participants_from_registry() -> list[Participant]:
    """Build Participants from the registry — the single source of truth for
    which agents exist. No filesystem walk; explicit `a8s add` is required."""
    reg = load_registry()
    parts: list[Participant] = []
    for name, info in reg.items():
        root_str = info.get("root", "")
        if not root_str:
            continue
        try:
            root = Path(root_str).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        parts.append(Participant(name=name, root=root))
    return parts


# ---------- discover (suggestions only) ----------

def _scan_for_markers(root: Path) -> list[tuple[str, str, Path]]:
    """Walk `root` (and its immediate children) for marker files. Returns
    `(name, kind, dir)` triples. Read-only; used by `a8s discover`."""
    candidates: list[Path] = [root]
    try:
        candidates.extend(p for p in sorted(root.iterdir()) if p.is_dir())
    except OSError:
        pass
    found: list[tuple[str, str, Path]] = []
    for d in candidates:
        for marker_name, kind in MARKER_FILES.items():
            marker = d / marker_name
            if not marker.is_file():
                continue
            name = parse_name(marker)
            if not name:
                continue
            found.append((name, kind, d.resolve()))
            break
    return found


# ---------- mailboxes ----------

def ensure_mailboxes(p: Participant) -> None:
    """Create mailbox dirs for `p`. Inbox and trash live under ~/.a8s/ (hidden
    from the agent); outbox lives in the agent's own root (so the agent can
    actually write to it under a workspace sandbox)."""
    for d in (inbox_dir(p.name), trash_dir(p.name)):
        d.mkdir(parents=True, exist_ok=True)
    outbox_dir(p.root).mkdir(parents=True, exist_ok=True)


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    i = 1
    while True:
        candidate = p.with_name(f"{p.stem}.{i}{p.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


# ---------- routing ----------

def route_outboxes(senders: list[Participant], all_agents: list[Participant] | None = None) -> int:
    """Route each sender's outbox to recipients found in `all_agents`.

    Mailbox routing is process-agnostic: a per-agent daemon may write into any
    other agent's inbox even though it isn't handling them. Only `wake_once`
    requires the handler attachment. `all_agents` is the recipient lookup pool
    (defaults to senders for self-contained calls)."""
    if all_agents is None:
        all_agents = senders
    by_name = {p.name.lower(): p for p in all_agents}
    routed = 0
    for sender in senders:
        ensure_mailboxes(sender)
        outbox = outbox_dir(sender.root)
        for f in sorted(outbox.iterdir()):
            if not (f.is_file() and f.name.endswith(".json")):
                continue
            try:
                with f.open("r", encoding="utf-8") as fp:
                    msg = json.load(fp)
            except (OSError, json.JSONDecodeError) as e:
                out_agent(sender.name, f"[{sender.name}] outbox parse error on {f.name}: {e}")
                continue
            # Defense: the outbox is writable by the agent, so it could try to
            # spoof a senderless prompt with `from: ""` or impersonate someone
            # else. Force `from` to the actual enclosing participant — outbox
            # location is the unforgeable identity.
            msg["from"] = sender.name
            recipient_name = (msg.get("to") or "").strip()
            preview = _preview(msg.get("content", ""))
            if not recipient_name:
                out_agent(sender.name, f"[{sender.name}] empty 'to' in {f.name}; rejecting (use an alias for groups)")
                bad = unique_path(trash_dir(sender.name) / f.name)
                f.rename(bad)
                continue
            try:
                kind, member_names = resolve_name(recipient_name)
            except KeyError:
                out_agent(sender.name, f"[{sender.name}] unknown recipient {recipient_name!r} in {f.name}")
                continue
            except ValueError as e:
                out_agent(sender.name, f"[{sender.name}] {e} in {f.name}")
                continue
            recipients: list[Participant] = []
            for member in member_names:
                rp = by_name.get(member.lower())
                if rp is not None and rp.name != sender.name:
                    # Skip self-copy: an alias that includes the sender doesn't
                    # echo the message back to them.
                    recipients.append(rp)
            if kind == "alias":
                msg["alias"] = recipient_name
                msg["others_count"] = max(0, len(recipients) - 1)
                out_agent(sender.name, f"routed: {sender.name} -> {recipient_name} (alias of {len(recipients)}): {preview}")
                for recipient in recipients:
                    ensure_mailboxes(recipient)
                    copy = dict(msg)
                    copy["to"] = recipient.name
                    dest = unique_path(inbox_dir(recipient.name) / f.name)
                    with dest.open("w", encoding="utf-8") as out_f:
                        json.dump(copy, out_f, indent=2)
                    out_agent(recipient.name, f"received from {sender.name} (via {recipient_name} alias): {preview}")
                f.unlink()
                routed += len(recipients)
            else:
                # Single agent recipient.
                if not recipients:
                    out_agent(sender.name, f"[{sender.name}] {recipient_name!r} resolved to no agents in {f.name}")
                    continue
                recipient = recipients[0]
                ensure_mailboxes(recipient)
                dest = unique_path(inbox_dir(recipient.name) / f.name)
                with dest.open("w", encoding="utf-8") as out_f:
                    json.dump(msg, out_f, indent=2)
                f.unlink()
                out_agent(sender.name, f"routed: {sender.name} -> {recipient.name}: {preview}")
                out_agent(recipient.name, f"received from {sender.name}: {preview}")
                routed += 1
    return routed


def next_inbox_message(p: Participant) -> Path | None:
    inbox = inbox_dir(p.name)
    if not inbox.is_dir():
        return None
    files = sorted(f for f in inbox.iterdir() if f.is_file() and f.name.endswith(".json"))
    return files[0] if files else None


DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"


def default_definition_path(kind: str) -> Path:
    return DEFINITIONS_DIR / f"{kind}.json"


def load_definition(name: str) -> dict:
    """Load the JSON definition for `name` from the path stored in the registry.
    Errors loudly if no definition is set — agents are not runnable until
    `a8s define <name> <path>` has been called.

    Definitions encode argv (with `$PROMPT` placeholder), message templates,
    and per-tool quirks. See apps/a8s/definitions/*.json for built-in shapes.
    """
    reg = load_registry()
    info = reg.get(name) or {}
    custom = info.get("definition")
    if not custom:
        raise FileNotFoundError(
            f"{name!r} has no definition; run `a8s define {name} <path>`"
        )
    path = Path(custom).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"definition file missing: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.loads(f.read())
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"definition load failed for {path}: {e}") from e


def build_prompt(msg: dict, definition: dict) -> str:
    """Format a queued message into the prompt string handed to the agent CLI.

    `definition` provides:
      - `promptMessage`       — direct tell (one-to-one)
      - `promptMessageAlias`  — message delivered via an alias; sees alias name
                                and others-count but not the other recipients
    Senderless messages (queued by `a8s prompt`) deliver `content` raw.
    """
    sender = (msg.get("from") or "").strip()
    content = msg.get("content", "")
    date = msg.get("date", "")
    recipient = (msg.get("to") or "").strip()
    alias = (msg.get("alias") or "").strip()
    others_count = msg.get("others_count", 0)
    if not sender:
        # `a8s prompt` queued this; supervisor-direct, no template wrapping.
        header = content
    else:
        if alias:
            tmpl = definition.get("promptMessageAlias") or (
                "{sender} tells you ({recipient}) and {others_count} others on the {alias} alias: {message}"
            )
        else:
            tmpl = definition.get("promptMessage") or "{sender} tells you ({recipient}): {message}"
        header = tmpl.format(
            sender=sender,
            recipient=recipient,
            message=content,
            date=date,
            alias=alias,
            others_count=others_count,
        )
        if date and "{date}" not in tmpl:
            header = f"[{date}] {header}"
    parts = [header]
    files = msg.get("files") or []
    if files:
        parts.append("")
        for entry in files:
            path = entry.get("path") or entry.get("filename")
            if path:
                parts.append(f"FILE: {path}")
    return "\n".join(parts)


def _expand_argv(argv: list[str], prompt: str) -> list[str]:
    """Expand `$PROMPT` placeholder in argv. Other env-var-style placeholders
    are reserved for future verbs."""
    return [a.replace("$PROMPT", prompt) for a in argv]


def build_command(definition: dict, prompt: str, fresh: bool = False) -> list[str]:
    """Pick the right `invoke*` argv from `definition` based on (fresh, UNRESTRICTED)
    and expand `$PROMPT`.

    Phase 1 schema:
      invoke                    — default mode, continues prior session
      invokeFresh               — default mode, starts fresh
      invokeUnrestricted        — `--unrestricted`, continues
      invokeUnrestrictedFresh   — `--unrestricted`, fresh
    """
    if UNRESTRICTED:
        key = "invokeUnrestrictedFresh" if fresh else "invokeUnrestricted"
    else:
        key = "invokeFresh" if fresh else "invoke"
    argv = definition.get(key)
    if not argv:
        # Fallback: try the non-fresh variant of the same mode.
        argv = definition.get("invokeUnrestricted" if UNRESTRICTED else "invoke")
    if not argv:
        raise ValueError(f"definition missing {key!r} (and fallback)")
    return _expand_argv(list(argv), prompt)


def _cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "a8s"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _fresh_file() -> Path:
    return _cache_dir() / "fresh.json"


def _load_fresh() -> set[str]:
    f = _fresh_file()
    if not f.is_file():
        return set()
    try:
        data = json.loads(f.read_text())
        return set(data) if isinstance(data, list) else set()
    except (OSError, json.JSONDecodeError):
        return set()


def _save_fresh(s: set[str]) -> None:
    _fresh_file().write_text(json.dumps(sorted(s), indent=2))


def mark_fresh(roots: list[Path]) -> None:
    s = _load_fresh()
    for r in roots:
        s.add(str(r))
    _save_fresh(s)


def consume_fresh(root: Path) -> bool:
    s = _load_fresh()
    key = str(root)
    if key not in s:
        return False
    s.discard(key)
    _save_fresh(s)
    return True


# ---------- registry ----------

# Schema (~/.a8s/a8s.json):
#   {
#     "agents":  {"<NAME>": {"root": "...", "definition": "...?"}},
#     "aliases": {"<ALIAS>": ["<NAME-or-ALIAS>", ...]}
#   }
# Agent and alias namespaces are disjoint (cmd_alias rejects collisions).

def registry_path() -> Path:
    base = Path.home() / ".a8s"
    base.mkdir(parents=True, exist_ok=True)
    return base / "a8s.json"


def _load_raw_registry() -> dict:
    p = registry_path()
    if not p.is_file():
        return {"agents": {}, "aliases": {}}
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"agents": {}, "aliases": {}}
    if not isinstance(data, dict):
        return {"agents": {}, "aliases": {}}
    agents = data.get("agents")
    aliases = data.get("aliases")
    if not isinstance(agents, dict):
        agents = {}
    if not isinstance(aliases, dict):
        aliases = {}
    return {"agents": agents, "aliases": aliases}


def _save_raw_registry(data: dict) -> None:
    payload = {
        "agents": data.get("agents") or {},
        "aliases": data.get("aliases") or {},
    }
    registry_path().write_text(json.dumps(payload, indent=2, sort_keys=True))


def load_registry() -> dict:
    """Return just the agents section (compatibility shape for callers that
    iterate `name -> info`). Use `load_aliases()` for the alias map."""
    return _load_raw_registry()["agents"]


def save_registry(agents: dict) -> None:
    """Write the agents section, preserving the existing aliases section."""
    raw = _load_raw_registry()
    raw["agents"] = agents
    _save_raw_registry(raw)


def load_aliases() -> dict:
    return _load_raw_registry()["aliases"]


def save_aliases(aliases: dict) -> None:
    raw = _load_raw_registry()
    raw["aliases"] = aliases
    _save_raw_registry(raw)


def resolve_recipient(query: str) -> tuple[str, dict] | None:
    """Look up an agent by exact (case-insensitive) name. Aliases are NOT
    resolved here — that's `resolve_name()`'s job for fan-out."""
    reg = load_registry()
    q = query.strip().lower()
    for name, info in reg.items():
        if name.lower() == q:
            return name, info
    return None


def sender_from_cwd() -> tuple[str, dict] | None:
    reg = load_registry()
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        for name, info in reg.items():
            try:
                root = Path(info.get("root", "")).resolve()
            except (OSError, RuntimeError):
                continue
            if root == parent:
                return name, info
    return None


def resolve_name(query: str) -> tuple[str, list[str]]:
    """Expand `query` into a flat list of agent names.

    Returns (kind, agent_names) where kind ∈ {"agent", "alias"}. For an alias,
    members are walked recursively; cycles raise ValueError. Unknown names
    raise KeyError.
    """
    raw = _load_raw_registry()
    agents = raw["agents"]
    aliases = raw["aliases"]
    agent_lookup = {n.lower(): n for n in agents}
    alias_lookup = {n.lower(): n for n in aliases}
    q = query.strip().lower()
    if q in agent_lookup:
        return "agent", [agent_lookup[q]]
    if q in alias_lookup:
        seen: set[str] = set()
        out_names: list[str] = []

        def walk(name: str) -> None:
            key = name.lower()
            if key in seen:
                raise ValueError(f"alias cycle detected at {name!r}")
            seen.add(key)
            if key in agent_lookup:
                resolved = agent_lookup[key]
                if resolved not in out_names:
                    out_names.append(resolved)
                return
            if key in alias_lookup:
                for member in aliases[alias_lookup[key]]:
                    walk(str(member))
                return
            raise KeyError(f"alias {alias_lookup[q]!r} references unknown name {name!r}")

        walk(alias_lookup[q])
        return "alias", out_names
    raise KeyError(query)


def run_with_prefix(name: str, cmd: list[str], cwd: Path) -> int:
    """Run the wake subprocess in its own session so SIGKILL can target the
    whole process group (LLM CLI + any helpers it spawns). Tracks the live
    process in `_CURRENT_WAKE_PROC` so the second-signal handler can find it."""
    global _CURRENT_WAKE_PROC
    prefix = f"{name}> "
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
    except FileNotFoundError:
        out_agent(name, f"{prefix}command not found: {cmd[0]}")
        return 127
    _CURRENT_WAKE_PROC = proc
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            out_agent(name, prefix + line.rstrip("\n"))
        proc.wait()
        if proc.returncode != 0:
            out_agent(name, f"{prefix}(exit {proc.returncode})")
        return proc.returncode
    finally:
        _CURRENT_WAKE_PROC = None


def wake_once(p: Participant, msg_path: Path) -> None:
    try:
        with msg_path.open("r", encoding="utf-8") as f:
            msg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        out_agent(p.name, f"[{p.name}] inbox parse error on {msg_path.name}: {e}")
        bad = unique_path(trash_dir(p.name) / msg_path.name)
        msg_path.rename(bad)
        return

    try:
        definition = load_definition(p.name)
    except (FileNotFoundError, RuntimeError) as e:
        out_agent(p.name, f"[{p.name}] {e}")
        bad = unique_path(trash_dir(p.name) / msg_path.name)
        msg_path.rename(bad)
        return
    prompt = build_prompt(msg, definition)
    trashed = unique_path(trash_dir(p.name) / msg_path.name)
    msg_path.rename(trashed)
    fresh = consume_fresh(p.root)
    flag = " (fresh)" if fresh else ""
    out_agent(p.name, f"[{p.name}] waking from {trashed.name}{flag}: {_preview(msg.get('content', ''))}")
    cmd = build_command(definition, prompt, fresh=fresh)
    run_with_prefix(p.name, cmd, p.root)


# ---------- per-agent attachment ----------

def pid_path(name: str) -> Path:
    return agent_dir(name) / "pid"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _read_handler_pid(name: str) -> int | None:
    """Return the live PID currently handling <name>, or None. Cleans up stale
    pid files."""
    p = pid_path(name)
    if not p.is_file():
        return None
    try:
        pid = int(p.read_text().strip())
    except (OSError, ValueError):
        try:
            p.unlink()
        except OSError:
            pass
        return None
    if _pid_alive(pid):
        return pid
    try:
        p.unlink()
    except OSError:
        pass
    return None


def _try_atomic_claim(name: str, pid: int) -> bool:
    """Attempt to write `pid` into `pid_path(name)` using O_CREAT|O_EXCL.
    Returns True iff this process now holds the handler attachment."""
    p = pid_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, str(pid).encode())
    finally:
        os.close(fd)
    return True


# Wait up to this long for an existing handler to release after SIGTERM.
ACQUIRE_TIMEOUT_S = 30.0
ACQUIRE_POLL_S = 0.1


def acquire(name: str) -> None:
    """Attach this process as the handler of <name>. If another live process
    is currently handling it, send SIGTERM (graceful detach) and wait for
    release. Always wins (per locked design). Raises TimeoutError if the prior
    handler doesn't release within ACQUIRE_TIMEOUT_S."""
    import time as _time
    me = os.getpid()
    while True:
        if _try_atomic_claim(name, me):
            return
        existing = _read_handler_pid(name)
        if existing is None:
            continue  # stale or freed; retry
        if existing == me:
            return
        sys.stderr.write(f"[a8s] detach in progress: {name} from PID {existing}\n")
        sys.stderr.flush()
        try:
            os.kill(existing, signal.SIGTERM)
        except ProcessLookupError:
            try:
                pid_path(name).unlink()
            except OSError:
                pass
            continue
        deadline = _time.time() + ACQUIRE_TIMEOUT_S
        while _time.time() < deadline:
            if not pid_path(name).is_file():
                break
            if not _pid_alive(existing):
                try:
                    pid_path(name).unlink()
                except OSError:
                    pass
                break
            _time.sleep(ACQUIRE_POLL_S)
        else:
            raise TimeoutError(
                f"PID {existing} did not release {name} within {ACQUIRE_TIMEOUT_S}s — "
                f"try `a8s kill {name}`"
            )


def release(name: str) -> None:
    """Unlink the pid file iff it points at our pid. Safe to call repeatedly."""
    p = pid_path(name)
    try:
        if not p.is_file():
            return
        pid = int(p.read_text().strip())
        if pid == os.getpid():
            p.unlink()
    except (OSError, ValueError):
        pass


# ---------- attached loop (the daemon body for one or more agents) ----------

# Shared state for the signal handler. Set when an attached loop is running.
_STOP_EVENT: threading.Event | None = None
_SIGNAL_COUNT = 0
_CURRENT_WAKE_PROC: subprocess.Popen | None = None


def _kill_wake_subprocess_group() -> None:
    """SIGTERM-then-SIGKILL the current wake's subprocess group. Targets the
    whole process tree so the LLM CLI dies along with our wake wrapper."""
    proc = _CURRENT_WAKE_PROC
    if proc is None or proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        pass
    import time as _time
    _time.sleep(0.5)
    if proc.poll() is None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass


def _make_signal_handler(label: str):
    def handle(signum, _frame):
        global _SIGNAL_COUNT
        _SIGNAL_COUNT += 1
        if _SIGNAL_COUNT == 1:
            sys.stderr.write(
                f"[a8s] {label}: received signal {signum}; detaching after current wake\n"
            )
            sys.stderr.flush()
            if _STOP_EVENT is not None:
                _STOP_EVENT.set()
        else:
            sys.stderr.write(
                f"[a8s] {label}: second signal — killing wake subprocess group\n"
            )
            sys.stderr.flush()
            _kill_wake_subprocess_group()
    return handle


def attached_loop(names: list[str], interval: float, *, single_pass: bool = False) -> int:
    """Body of `a8s run` / `a8s start` / `a8s step`. ONE process handles every
    name in `names` (multiple agents share the same handler PID, recorded in
    each agent's `~/.a8s/agents/<NAME>/pid`).

    Per iteration:
      - reload registry (so newly-added agents become routable recipients)
      - check each handled agent's pid file is still ours (drop if taken over)
      - route each handled agent's outbox to recipients
      - drain each handled agent's inbox

    On 1st signal: detach all currently-handled agents (graceful — finish the
    in-flight wake first). On 2nd signal: SIGTERM-then-SIGKILL the wake
    subprocess group.

    Take-over collateral: SIGTERM is process-level, so when one of our handled
    agents is targeted by another `a8s start`, this whole handler detaches
    everything. Other agents in our set become orphaned. The user's footgun;
    document and move on (documented in #52)."""
    global _STOP_EVENT, _SIGNAL_COUNT, PRINT_LOCK
    PRINT_LOCK = threading.Lock()
    _STOP_EVENT = threading.Event()
    _SIGNAL_COUNT = 0

    if not names:
        print("attached_loop: empty names list", file=sys.stderr)
        return 2

    # Acquire each pid file. If any fails (timeout), release whatever we got.
    acquired: list[str] = []
    try:
        for name in names:
            acquire(name)
            acquired.append(name)
    except TimeoutError as e:
        print(str(e), file=sys.stderr)
        for n in acquired:
            release(n)
        return 1

    label = names[0] if len(names) == 1 else f"[{', '.join(names)}]"
    handler = _make_signal_handler(label)
    prev_sigterm = signal.signal(signal.SIGTERM, handler)
    prev_sigint = signal.signal(signal.SIGINT, handler)

    pid = os.getpid()
    for n in names:
        out_agent(n, f"[a8s] {n}: attached (PID {pid}{', shared' if len(names) > 1 else ''})")
    try:
        while not _STOP_EVENT.is_set():
            try:
                all_agents = participants_from_registry()
                # Filter to agents we still hold and that still exist.
                handled: list[Participant] = []
                for name in list(names):
                    p = next((q for q in all_agents if q.name == name), None)
                    if p is None:
                        out_agent(name, f"[a8s] {name}: removed from registry; dropping")
                        names.remove(name)
                        continue
                    holder = _read_handler_pid(name)
                    if holder is not None and holder != pid:
                        out_agent(name, f"[a8s] {name}: detaching (taken over by PID {holder})")
                        names.remove(name)
                        continue
                    handled.append(p)
                if not handled:
                    out_agent(label, f"[a8s] {label}: nothing left to handle; exiting")
                    break
                for p in handled:
                    ensure_mailboxes(p)
                route_outboxes(handled, all_agents=all_agents)
                for p in handled:
                    while not _STOP_EVENT.is_set():
                        msg = next_inbox_message(p)
                        if msg is None:
                            break
                        wake_once(p, msg)
            except Exception as e:
                out_agent(label, f"[a8s] {label}: iteration error: {e}")
            if single_pass:
                break
            _STOP_EVENT.wait(interval)
    finally:
        # Release every pid file we still hold.
        for n in acquired:
            holder = _read_handler_pid(n)
            if holder is None or holder == pid:
                release(n)
                out_agent(n, f"[a8s] {n}: detached")
        signal.signal(signal.SIGTERM, prev_sigterm)
        signal.signal(signal.SIGINT, prev_sigint)
        _STOP_EVENT = None
    return 0


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = SCRIPT_DIR / "skills"
BIN_ROOT = SCRIPT_DIR.parent.parent


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


def _install_skill_gemini(skill_dir: Path) -> str:
    if shutil.which("gemini") is None:
        return "  gemini: not on PATH; skipping"
    skill_name = skill_dir.name
    try:
        listed = subprocess.run(
            ["gemini", "skills", "list", "--all"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"  gemini: could not list skills ({e}); skipping"
    if re.search(rf"\b{re.escape(skill_name)}\b", listed.stdout):
        return f"  gemini: '{skill_name}' already linked"
    res = subprocess.run(
        ["gemini", "skills", "link", str(skill_dir), "--scope", "user", "--consent"],
        capture_output=True, text=True, timeout=60,
    )
    if res.returncode != 0:
        msg = (res.stderr.strip() or res.stdout.strip()).splitlines()[-1] if (res.stderr or res.stdout) else "unknown error"
        return f"  gemini: link failed ({msg})"
    return f"  gemini: linked '{skill_name}' at user scope"


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


def cmd_add(args: list[str]) -> int:
    """`a8s add <name> <dir>` — explicitly register a new agent. Errors on
    duplicate name or non-directory path. New entries have no definition;
    `a8s define <name> <path>` is required before the agent can wake."""
    if len(args) != 2:
        print("usage: a8s add <name> <dir>", file=sys.stderr)
        return 2
    name, dir_str = args
    if not NAME_RE.fullmatch(name):
        print(f"name must be alphanumeric: {name!r}", file=sys.stderr)
        return 2
    root = Path(dir_str).expanduser()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1
    root = root.resolve()
    reg = load_registry()
    for k in reg:
        if k.lower() == name.lower():
            print(f"agent already exists with name: {k}", file=sys.stderr)
            return 1
    aliases = load_aliases()
    for k in aliases:
        if k.lower() == name.lower():
            print(f"alias already exists with name: {k} — pick a different agent name", file=sys.stderr)
            return 1
    reg[name] = {"root": str(root)}
    save_registry(reg)
    print(f"added {name} -> {root}")
    print(f"next: a8s define {name} <path-to-definition.json>")
    return 0


def cmd_alias(args: list[str]) -> int:
    """`a8s alias <alias> <member>` — add member to alias, creating the alias
    if new. Members may be agent names OR existing alias names (nesting OK,
    cycles rejected at resolve time). The alias name must not collide with
    an existing agent name."""
    if len(args) == 0:
        return cmd_aliases()
    if len(args) != 2:
        print("usage: a8s alias <alias> <member>     # add or create", file=sys.stderr)
        print("       a8s alias                      # list", file=sys.stderr)
        return 2
    alias_name, member = args
    if not NAME_RE.fullmatch(alias_name):
        print(f"alias name must be alphanumeric: {alias_name!r}", file=sys.stderr)
        return 2
    agents = load_registry()
    aliases = load_aliases()
    for k in agents:
        if k.lower() == alias_name.lower():
            print(f"agent already exists with name: {k} — pick a different alias", file=sys.stderr)
            return 1
    member_resolved: str | None = None
    for k in agents:
        if k.lower() == member.lower():
            member_resolved = k
            break
    if member_resolved is None:
        for k in aliases:
            if k.lower() == member.lower():
                member_resolved = k
                break
    if member_resolved is None:
        print(f"unknown member {member!r} (not an agent or alias)", file=sys.stderr)
        return 1
    if member_resolved.lower() == alias_name.lower():
        print(f"cannot add alias {alias_name!r} to itself", file=sys.stderr)
        return 1
    canonical_alias = alias_name
    for k in aliases:
        if k.lower() == alias_name.lower():
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
    alias if no member given."""
    if not args or len(args) > 2:
        print("usage: a8s unalias <alias> [<member>]", file=sys.stderr)
        return 2
    aliases = load_aliases()
    canonical: str | None = None
    for k in aliases:
        if k.lower() == args[0].lower():
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
    members = aliases[canonical]
    new_members = [m for m in members if m.lower() != member.lower()]
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


def cmd_agents() -> int:
    """`a8s agents` — list every registered agent with status (defined/undefined)."""
    reg = load_registry()
    if not reg:
        print("(no agents registered — use `a8s add <name> <dir>`)")
        return 0
    width = max(len(name) for name in reg)
    for name in sorted(reg, key=str.lower):
        info = reg[name]
        root = info.get("root", "?")
        if info.get("definition"):
            status = f"defined ({info['definition']})"
        else:
            status = "UNDEFINED — run `a8s define`"
        print(f"  {name.ljust(width)}  {root}  [{status}]")
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


def cmd_define(args: list[str]) -> int:
    """`a8s define <name>`           — show <name>'s effective definition + source.
    `a8s define <name> <path>`       — set <name>'s definition file path in the registry.
    """
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
        print(_install_skill_gemini(skill_dir))
        print(_install_skill_codex(skill_dir))
    return 0


def _split_content_and_files(raw: str) -> tuple[str, list[dict]]:
    lines = raw.splitlines()
    files: list[dict] = []
    while lines and lines[-1].strip().startswith("FILE:"):
        path = lines.pop().strip()[len("FILE:"):].strip()
        if path:
            files.insert(0, {"filename": Path(path).name, "path": path})
    return "\n".join(lines).rstrip(), files


def _write_outbox(sender_name: str, sender_root: Path, to: str, content: str, files: list[dict]) -> Path:
    outbox = outbox_dir(sender_root)
    outbox.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    msg = {
        "date": now.isoformat().replace("+00:00", "Z"),
        "from": sender_name,
        "to": to,
        "content": content,
        "files": files,
    }
    safe_sender = _safe_name(sender_name)
    fname = f"{now.strftime('%Y%m%dT%H%M%S%f')}_{safe_sender}.json"
    dest = unique_path(outbox / fname)
    with dest.open("w", encoding="utf-8") as f:
        json.dump(msg, f, indent=2)
    return dest


def cmd_tell(args: list[str]) -> int:
    """`a8s tell <name> <msg>` — write a single outbox message; `name` may be
    an agent or alias. Fan-out to alias members happens at routing time so the
    `to` field stays the original alias name (recipients can see it via
    `promptMessageAlias`)."""
    if len(args) < 2:
        print("usage: tell <name> <message>", file=sys.stderr)
        return 2
    target_query, *rest = args
    content, files = _split_content_and_files(" ".join(rest))

    sender = sender_from_cwd()
    if sender is None:
        print("tell: current directory is not inside any registered agent", file=sys.stderr)
        print("hint: register the enclosing dir with `a8s add <name> <dir>`", file=sys.stderr)
        return 1
    sender_name, sender_info = sender

    try:
        kind, members = resolve_name(target_query)
    except KeyError:
        print(f"tell: no agent or alias named {target_query!r}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"tell: {e}", file=sys.stderr)
        return 1
    if not members:
        print(f"tell: {target_query!r} resolves to no agents", file=sys.stderr)
        return 1
    # Resolve the canonical name (preserves user's chosen casing) for the `to`
    # field, regardless of agent vs alias.
    if kind == "agent":
        canonical = members[0]
    else:
        aliases = load_aliases()
        canonical = next((k for k in aliases if k.lower() == target_query.lower()), target_query)

    _write_outbox(sender_name, Path(sender_info["root"]), canonical, content, files)
    if kind == "alias":
        out_agent(sender_name, f"tell -> {canonical} (alias of {len(members)}): {_preview(content)}")
    else:
        out_agent(sender_name, f"tell -> {canonical}: {_preview(content)}")
    return 0


def cmd_clear(args: list[str]) -> int:
    """`a8s clear <name>` wipes the agent's (or alias members') mailboxes and
    flags fresh on next wake. Aliases fan out — every member is cleared."""
    if not args:
        print("usage: a8s clear <name>", file=sys.stderr)
        print("       <name> can be an agent or alias; alias members are all cleared.", file=sys.stderr)
        return 2
    name = args[0]
    try:
        _kind, members = resolve_name(name)
    except KeyError:
        print(f"clear: no agent or alias named {name!r}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"clear: {e}", file=sys.stderr)
        return 1
    parts = participants_from_registry()
    cleared_agents = 0
    cleared_msgs = 0
    for member in members:
        target = find_participant(parts, member)
        if target is None:
            continue
        ensure_mailboxes(target)
        for d in (inbox_dir(target.name), trash_dir(target.name), outbox_dir(target.root)):
            for f in d.iterdir():
                if f.is_file():
                    f.unlink()
                    cleared_msgs += 1
        mark_fresh([target.root])
        cleared_agents += 1
    print(f"cleared {cleared_msgs} message(s) across {cleared_agents} agent(s)")
    print("next wake for each will start a new conversation")
    return 0


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
    import time
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
    """`a8s run <name>` — foreground attached loop. <name> may be an agent or
    an alias; aliases produce ONE process that handles every member (each
    member's pid file points at this PID). Ctrl+C: graceful detach. 2nd
    Ctrl+C: kills the wake subprocess group."""
    if len(args) != 1:
        print("usage: a8s run <name>", file=sys.stderr)
        return 2
    members = _expand_to_agents(args[0])
    if members is None:
        return 1
    return attached_loop(members, interval)


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
    cmd = [sys.executable, str(Path(__file__).resolve()), "run", name]
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


def cmd_kill(args: list[str]) -> int:
    """`a8s kill <name>` — etiquette-then-force per unique handler PID."""
    if len(args) != 1:
        print("usage: a8s kill <name>", file=sys.stderr)
        return 2
    members = _expand_to_agents(args[0])
    if members is None:
        return 1
    seen_pids: dict[int, str] = {}
    for name in members:
        pid = _read_handler_pid(name)
        if pid is not None and pid not in seen_pids:
            seen_pids[pid] = name
    if not seen_pids:
        for name in members:
            print(f"{name}: not running", file=sys.stderr)
        return 1
    import time as _time
    for pid, label in seen_pids.items():
        print(f"{label}: SIGTERM PID {pid} (graceful)")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        _time.sleep(0.5)
        if _pid_alive(pid):
            print(f"{label}: still alive — second SIGTERM (forces subprocess group kill)")
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            _time.sleep(0.5)
        if _pid_alive(pid):
            print(f"{label}: still alive — SIGKILL")
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # Clean up any pid files still pointing at this dead PID.
        for name in members:
            p = pid_path(name)
            if p.is_file():
                try:
                    if int(p.read_text().strip()) == pid:
                        p.unlink()
                except (OSError, ValueError):
                    pass
    return 0


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


# ---------- presentation ----------

COMMANDS: list[tuple[str, str, str]] = [
    ("add",      "<name> <dir>",         "Register a new agent. Errors on duplicate name. Run `a8s define` next."),
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
    ("clear",    "<name>",               "Wipe mailboxes and flag fresh. <name> may be an agent or alias (clears each member)."),
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


# ---------- CLI ----------

def _queue_prompt(p: Participant, content: str) -> Path:
    """Drop a senderless message JSON directly into <p>/inbox/.

    The empty `from` is the signal to `build_prompt` to deliver the raw
    content (no `tells you` template wrapping). The next inbox-drain wakes
    the agent."""
    ensure_mailboxes(p)
    now = datetime.now(timezone.utc)
    msg = {
        "date": now.isoformat().replace("+00:00", "Z"),
        "from": "",
        "to": p.name,
        "content": content,
        "files": [],
    }
    fname = f"{now.strftime('%Y%m%dT%H%M%S%f')}_PROMPT.json"
    dest = unique_path(inbox_dir(p.name) / fname)
    with dest.open("w", encoding="utf-8") as f:
        json.dump(msg, f, indent=2)
    return dest


def cmd_prompt(args: list[str]) -> int:
    """`a8s prompt <name> <message>` — queue a senderless prompt. <name> may
    be an agent or alias; aliases queue one copy per member. The literal
    `all` target is gone — create an `all` alias if you want broadcast."""
    if len(args) < 2:
        print("usage: a8s prompt <name> <message>", file=sys.stderr)
        return 2
    name, *rest = args
    prompt = " ".join(rest)
    parts = participants_from_registry()

    members = _expand_to_agents(name)
    if members is None:
        return 1
    queued = 0
    for member in members:
        target = find_participant(parts, member)
        if target is None:
            print(f"prompt: registry inconsistency — {member!r} not found", file=sys.stderr)
            continue
        _queue_prompt(target, prompt)
        out_agent(target.name, f"queued prompt to {target.name}: {_preview(prompt)}")
        queued += 1
    if queued > 1:
        out(f"queued prompt to {queued} agent(s)")
    return 0 if queued > 0 else 1

    target = find_participant(parts, name)
    if target is None:
        print(f"no agent named {name!r}", file=sys.stderr)
        return 1
    _queue_prompt(target, prompt)
    out_agent(target.name, f"queued prompt to {target.name}: {_preview(prompt)}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="a8s",
        description="Agent Infinity System — route messages between Claude / Gemini / Codex projects.",
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--interval", type=float, default=1.0, help="loop poll interval seconds (default: 1.0)")
    parser.add_argument("--unrestricted", action="store_true",
                        help="drop per-tool gating where possible: claude switches to "
                             "--dangerously-skip-permissions, codex to "
                             "--dangerously-bypass-approvals-and-sandbox. Gemini is already "
                             "fully permissive in headless mode (Policy Engine doesn't apply).")
    parser.add_argument("command", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    global UNRESTRICTED
    UNRESTRICTED = bool(args.unrestricted)
    if UNRESTRICTED:
        print("[a8s] UNRESTRICTED: claude --dangerously-skip-permissions, codex --dangerously-bypass-approvals-and-sandbox", file=sys.stderr)

    if args.command in KNOWN_COMMANDS:
        return dispatch(args.command, args.rest, args.interval)

    print(f"unknown command: {args.command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
