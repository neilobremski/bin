"""a8s — Agent Infinity System.

Filesystem-based message router for independent Claude Code, Gemini CLI,
and Codex CLI project directories ("participants") to communicate.

Surface (CLI):
  step / loop / stop          — one routing pass / continuous mode / signal stop
  prompt <name|all> <msg>     — queue a senderless message to inbox(es)
  tell <name> <msg>           — direct routed message (sibling CLI ~/bin/tell)
  says <msg>                  — broadcast routed message (sibling CLI ~/bin/says)
  clear                       — wipe mailboxes + log; flag fresh on next wake
  install                     — install canonical skills into Claude / Gemini /
                                Codex user scope
  logs <name> [--tail N] [-f] — docker-logs-style filter on the supervisor log
  define <name> [<path>]      — show or set the JSON definition driving an agent's
                                wake invocation + prompt formatting

State:
  ~/.a8s/a8s.json             — participant registry (name -> kind, root, aliases,
                                optional `definition` path overriding the built-in)
  ~/.a8s/mailboxes/<NAME>/    — .inbox / .trash, isolated from the agent itself
  ~/.a8s/log.txt              — supervisor log: ISO-timestamped, captures every line
                                that goes through `out()` (including subprocess
                                output captured by `run_with_prefix`)
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
    return _a8s_dir() / "log.txt"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _preview(content: str, n: int = 80) -> str:
    """Single-line snippet of `content` for log readability."""
    s = (content or "").replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def mailbox_dir(name: str) -> Path:
    return _a8s_dir() / "mailboxes" / _safe_name(name)


def inbox_dir(name: str) -> Path:
    return mailbox_dir(name) / ".inbox"


def trash_dir(name: str) -> Path:
    return mailbox_dir(name) / ".trash"


def outbox_dir(root: Path) -> Path:
    """Outbox lives **inside the agent's own dir** so the agent can write to it
    even under a strict workspace sandbox (codex --full-auto). Inbox and trash
    stay isolated under ~/.a8s/ where the agent never sees them.

    `route_outboxes()` re-stamps the `from` field to the enclosing participant's
    name on every read, so an agent can't spoof a senderless prompt by writing
    a JSON with `from: ""`.
    """
    return root / ".outbox"


def _emit(line: str) -> None:
    """Write `line` (already terminated) to stdout and append a timestamped
    copy to the supervisor log at ~/.a8s/log.txt."""
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    log_line = f"{ts} {line}" if not line.endswith("\n") else f"{ts} {line}"
    sys.stdout.write(line)
    sys.stdout.flush()
    try:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(log_line if log_line.endswith("\n") else log_line + "\n")
    except OSError:
        pass


def out(text: str = "", end: str = "\n") -> None:
    line = text + end
    if PRINT_LOCK is not None:
        with PRINT_LOCK:
            _emit(line)
    else:
        _emit(line)


@dataclass(frozen=True)
class Participant:
    name: str
    kind: str
    root: Path
    marker: Path
    birthtime: float


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


def _number_duplicates(parts: list[Participant]) -> list[Participant]:
    by_name: dict[str, list[Participant]] = {}
    for p in parts:
        by_name.setdefault(p.name, []).append(p)
    out_list: list[Participant] = []
    for group in by_name.values():
        if len(group) == 1:
            out_list.append(group[0])
            continue
        for i, p in enumerate(sorted(group, key=lambda x: x.birthtime), start=1):
            out_list.append(Participant(f"{p.name} {i}", p.kind, p.root, p.marker, p.birthtime))
    return out_list


def discover(root: Path) -> list[Participant]:
    candidates: list[Path] = [root]
    candidates.extend(p for p in sorted(root.iterdir()) if p.is_dir())

    found: list[Participant] = []
    for d in candidates:
        for marker_name, kind in MARKER_FILES.items():
            marker = d / marker_name
            if not marker.is_file():
                continue
            name = parse_name(marker)
            if not name:
                continue
            stat = d.stat()
            birthtime = getattr(stat, "st_birthtime", stat.st_ctime)
            found.append(Participant(name, kind, d.resolve(), marker.resolve(), birthtime))
            break
    return _number_duplicates(found)


def find_participant(parts: list[Participant], query: str) -> Participant | None:
    q = query.strip().lower()
    for p in parts:
        if p.name.lower() == q:
            return p
    return None


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

def route_outboxes(participants: list[Participant]) -> int:
    by_name = {p.name.lower(): p for p in participants}
    routed = 0
    for sender in participants:
        ensure_mailboxes(sender)
        outbox = outbox_dir(sender.root)
        for f in sorted(outbox.iterdir()):
            if not (f.is_file() and f.name.endswith(".json")):
                continue
            try:
                with f.open("r", encoding="utf-8") as fp:
                    msg = json.load(fp)
            except (OSError, json.JSONDecodeError) as e:
                out(f"[{sender.name}] outbox parse error on {f.name}: {e}")
                continue
            # Defense: the outbox is writable by the agent, so it could try to
            # spoof a senderless prompt with `from: ""` or impersonate someone
            # else. Force `from` to the actual enclosing participant — outbox
            # location is the unforgeable identity.
            msg["from"] = sender.name
            recipient_name = (msg.get("to") or "").strip()
            if not recipient_name:
                # broadcast: fan out to every other participant.
                others = [p for p in participants if p.name != sender.name]
                for recipient in others:
                    ensure_mailboxes(recipient)
                    dest = unique_path(inbox_dir(recipient.name) / f.name)
                    with dest.open("w", encoding="utf-8") as out_f:
                        json.dump(msg, out_f, indent=2)
                f.unlink()
                out(f"broadcast: {sender.name} -> {len(others)}: {_preview(msg.get('content', ''))}")
                routed += len(others)
                continue
            recipient = by_name.get(recipient_name.lower())
            if recipient is None:
                out(f"[{sender.name}] unknown recipient {recipient_name!r} in {f.name}")
                continue
            ensure_mailboxes(recipient)
            dest = unique_path(inbox_dir(recipient.name) / f.name)
            with dest.open("w", encoding="utf-8") as out_f:
                json.dump(msg, out_f, indent=2)
            f.unlink()
            out(f"routed: {sender.name} -> {recipient.name}: {_preview(msg.get('content', ''))}")
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


def load_definition(name: str, kind: str) -> dict:
    """Load the JSON definition for `name`. The registry may carry an explicit
    `definition` path; otherwise fall back to the built-in default for `kind`.

    Definitions encode argv (with `$PROMPT` placeholder), message templates,
    and per-tool quirks. See apps/a8s/definitions/*.json.
    """
    reg = load_registry()
    info = reg.get(name) or {}
    custom = info.get("definition")
    candidates: list[Path] = []
    if custom:
        candidates.append(Path(custom).expanduser())
    candidates.append(default_definition_path(kind))
    for path in candidates:
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    return json.loads(f.read())
            except (OSError, json.JSONDecodeError) as e:
                raise RuntimeError(f"definition load failed for {path}: {e}") from e
    raise FileNotFoundError(
        f"no definition for {name!r} (kind={kind}); "
        f"looked in: {', '.join(str(c) for c in candidates)}"
    )


def build_prompt(msg: dict, definition: dict) -> str:
    """Format a queued message into the prompt string handed to the agent CLI.

    `definition` provides:
      - `promptMessage`           — used when sender + recipient are both set (direct tell)
      - `promptMessageBroadcast`  — used when sender is set but recipient is empty (broadcast)
    Senderless messages (queued by `a8s prompt`) deliver `content` raw.
    """
    sender = (msg.get("from") or "").strip()
    content = msg.get("content", "")
    date = msg.get("date", "")
    recipient = (msg.get("to") or "").strip()
    if not sender:
        # `a8s prompt` queued this; supervisor-direct, no template wrapping.
        header = content
    else:
        if recipient:
            tmpl = definition.get("promptMessage") or "{sender} tells you ({recipient}): {message}"
        else:
            tmpl = definition.get("promptMessageBroadcast") or "{sender} says: {message}"
        header = tmpl.format(sender=sender, recipient=recipient, message=content, date=date)
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

def registry_path() -> Path:
    base = Path.home() / ".a8s"
    base.mkdir(parents=True, exist_ok=True)
    return base / "a8s.json"


def load_registry() -> dict:
    p = registry_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_registry(reg: dict) -> None:
    registry_path().write_text(json.dumps(reg, indent=2, sort_keys=True))


def register_discovered(parts: list[Participant]) -> None:
    """Add new participants to the registry. Warn-and-skip on name conflicts."""
    reg = load_registry()
    changed = False
    for p in parts:
        existing = reg.get(p.name)
        if existing is None:
            reg[p.name] = {"kind": p.kind, "root": str(p.root), "aliases": []}
            changed = True
            out(f"[a8s] registered {p.name} -> {p.root}")
            continue
        try:
            existing_root = Path(existing.get("root", "")).resolve()
        except (OSError, RuntimeError):
            existing_root = Path(existing.get("root", ""))
        if existing_root != p.root.resolve():
            out(f"[a8s] WARNING: {p.name} already registered to {existing_root}; ignoring duplicate at {p.root}")
            continue
        if existing.get("kind") != p.kind:
            existing["kind"] = p.kind
            changed = True
    if changed:
        save_registry(reg)


def resolve_recipient(query: str) -> tuple[str, dict] | None:
    reg = load_registry()
    q = query.strip().lower()
    for name, info in reg.items():
        if name.lower() == q:
            return name, info
    for name, info in reg.items():
        for alias in info.get("aliases") or []:
            if str(alias).lower() == q:
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


def run_with_prefix(name: str, cmd: list[str], cwd: Path) -> int:
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
        )
    except FileNotFoundError:
        out(f"{prefix}command not found: {cmd[0]}")
        return 127
    assert proc.stdout is not None
    for line in proc.stdout:
        out(prefix + line.rstrip("\n"))
    proc.wait()
    if proc.returncode != 0:
        out(f"{prefix}(exit {proc.returncode})")
    return proc.returncode


def wake_once(p: Participant, msg_path: Path) -> None:
    try:
        with msg_path.open("r", encoding="utf-8") as f:
            msg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        out(f"[{p.name}] inbox parse error on {msg_path.name}: {e}")
        bad = unique_path(trash_dir(p.name) / msg_path.name)
        msg_path.rename(bad)
        return

    try:
        definition = load_definition(p.name, p.kind)
    except (FileNotFoundError, RuntimeError) as e:
        out(f"[{p.name}] {e}")
        bad = unique_path(trash_dir(p.name) / msg_path.name)
        msg_path.rename(bad)
        return
    prompt = build_prompt(msg, definition)
    trashed = unique_path(trash_dir(p.name) / msg_path.name)
    msg_path.rename(trashed)
    fresh = consume_fresh(p.root)
    flag = " (fresh)" if fresh else ""
    out(f"[{p.name}] waking from {trashed.name}{flag}: {_preview(msg.get('content', ''))}")
    cmd = build_command(definition, prompt, fresh=fresh)
    run_with_prefix(p.name, cmd, p.root)


def step(scan_dir: Path) -> None:
    parts = discover(scan_dir)
    register_discovered(parts)
    for p in parts:
        ensure_mailboxes(p)
    route_outboxes(parts)
    for p in parts:
        while True:
            msg = next_inbox_message(p)
            if msg is None:
                break
            wake_once(p, msg)


# ---------- loop / stop ----------

def pid_file_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "a8s"
    base.mkdir(parents=True, exist_ok=True)
    return base / "loop.pid"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _read_pid_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def loop_mode(scan_dir: Path, interval: float) -> int:
    global PRINT_LOCK
    PRINT_LOCK = threading.Lock()

    pid_file = pid_file_path()
    existing = _read_pid_file(pid_file)
    if existing and isinstance(existing.get("pid"), int) and _pid_alive(existing["pid"]):
        print(f"a8s loop already running (pid {existing['pid']})", file=sys.stderr)
        return 1

    pid_file.write_text(json.dumps({
        "pid": os.getpid(),
        "scan_dir": str(scan_dir),
        "started": datetime.now(timezone.utc).isoformat(),
    }))

    stop_event = threading.Event()
    busy: set[str] = set()
    busy_lock = threading.Lock()
    workers: list[threading.Thread] = []

    def handle_signal(signum, _frame):
        out(f"[a8s] received signal {signum}; finishing in-flight work")
        stop_event.set()

    prev_sigterm = signal.signal(signal.SIGTERM, handle_signal)
    prev_sigint = signal.signal(signal.SIGINT, handle_signal)

    def drain_worker(p: Participant) -> None:
        try:
            while not stop_event.is_set():
                msg = next_inbox_message(p)
                if msg is None:
                    return
                wake_once(p, msg)
        finally:
            with busy_lock:
                busy.discard(p.name)

    out(f"[a8s] loop started (pid {os.getpid()}, scan_dir {scan_dir}, interval {interval}s)")
    try:
        while not stop_event.is_set():
            try:
                participants = discover(scan_dir)
                register_discovered(participants)
                for p in participants:
                    ensure_mailboxes(p)
                route_outboxes(participants)
                for p in participants:
                    if stop_event.is_set():
                        break
                    with busy_lock:
                        if p.name in busy:
                            continue
                        if next_inbox_message(p) is None:
                            continue
                        busy.add(p.name)
                    t = threading.Thread(target=drain_worker, args=(p,), daemon=False)
                    t.start()
                    workers.append(t)
                workers[:] = [t for t in workers if t.is_alive()]
            except Exception as e:
                out(f"[a8s] iteration error: {e}")
            stop_event.wait(interval)
    finally:
        out("[a8s] waiting for in-flight workers")
        for t in workers:
            t.join()
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
        signal.signal(signal.SIGTERM, prev_sigterm)
        signal.signal(signal.SIGINT, prev_sigint)
        out("[a8s] loop exited")
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
    kind = info.get("kind", "")

    if len(args) == 1:
        custom = info.get("definition")
        if custom:
            source = Path(custom).expanduser()
        else:
            source = default_definition_path(kind)
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
    if len(args) < 2:
        print("usage: tell <name> <message>", file=sys.stderr)
        return 2
    target_query, *rest = args
    content, files = _split_content_and_files(" ".join(rest))

    sender = sender_from_cwd()
    if sender is None:
        print("tell: current directory is not inside any registered participant", file=sys.stderr)
        print("hint: run `a8s` from a parent of your participants to register them", file=sys.stderr)
        return 1
    sender_name, sender_info = sender

    target = resolve_recipient(target_query)
    if target is None:
        print(f"tell: no participant named or aliased {target_query!r}", file=sys.stderr)
        return 1
    target_name, _target_info = target

    _write_outbox(sender_name, Path(sender_info["root"]), target_name, content, files)
    out(f"tell -> {target_name}: {_preview(content)}")
    return 0


def cmd_says(args: list[str]) -> int:
    if not args:
        print("usage: says <message>", file=sys.stderr)
        return 2
    content, files = _split_content_and_files(" ".join(args))

    sender = sender_from_cwd()
    if sender is None:
        print("says: current directory is not inside any registered participant", file=sys.stderr)
        print("hint: run `a8s` from a parent of your participants to register them", file=sys.stderr)
        return 1
    sender_name, sender_info = sender

    _write_outbox(sender_name, Path(sender_info["root"]), "", content, files)
    out(f"says ({sender_name}): {_preview(content)}")
    return 0


def cmd_clear(scan_dir: Path) -> int:
    parts = discover(scan_dir)
    if not parts:
        print("no participants found")
        return 0
    cleared = 0
    for p in parts:
        ensure_mailboxes(p)
        for d in (inbox_dir(p.name), trash_dir(p.name), outbox_dir(p.root)):
            for f in d.iterdir():
                if f.is_file():
                    f.unlink()
                    cleared += 1
        # Defensive: wipe legacy <root>/.inbox / .trash dirs from before the
        # mailbox-isolation change. (`.outbox` is current, handled above.)
        for sub in (".inbox", ".trash"):
            legacy = p.root / sub
            if legacy.is_dir():
                for f in legacy.iterdir():
                    if f.is_file():
                        f.unlink()
                        cleared += 1
    mark_fresh([p.root for p in parts])
    log = _log_path()
    log_size = log.stat().st_size if log.is_file() else 0
    log.write_text("")
    print(f"cleared {cleared} message(s) across {len(parts)} participant(s)")
    if log_size:
        print(f"truncated supervisor log ({log_size} bytes)")
    print("next wake for each will start a new conversation")
    return 0


def cmd_logs(args: list[str]) -> int:
    if not args:
        print("usage: a8s logs <name> [--tail N] [-f|--follow]", file=sys.stderr)
        return 2
    name = args[0]
    tail_n: int | None = None
    follow = False
    i = 1
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
        else:
            print(f"unknown logs arg: {a!r}", file=sys.stderr)
            return 2

    log = _log_path()
    if not log.is_file():
        print(f"no log yet at {log}", file=sys.stderr)
        return 1

    pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)

    with log.open("r", encoding="utf-8", errors="replace") as f:
        existing = [ln for ln in f if pattern.search(ln)]
    if tail_n is not None:
        existing = existing[-tail_n:]
    for ln in existing:
        sys.stdout.write(ln)
    sys.stdout.flush()

    if not follow:
        return 0

    # Follow: re-open and seek to end, then poll for new lines.
    try:
        with log.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)  # SEEK_END
            while True:
                line = f.readline()
                if not line:
                    import time
                    time.sleep(0.25)
                    continue
                if pattern.search(line):
                    sys.stdout.write(line)
                    sys.stdout.flush()
    except KeyboardInterrupt:
        return 0


def cmd_stop() -> int:
    pid_file = pid_file_path()
    info = _read_pid_file(pid_file)
    if not info:
        print("no a8s loop is running", file=sys.stderr)
        return 1
    pid = info.get("pid")
    if not isinstance(pid, int):
        print("pid file missing or malformed pid", file=sys.stderr)
        return 1
    if not _pid_alive(pid):
        print(f"pid {pid} not running; removing stale pid file")
        pid_file.unlink(missing_ok=True)
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print(f"could not signal pid {pid}: {e}", file=sys.stderr)
        return 1
    print(f"sent SIGTERM to pid {pid}")
    return 0


# ---------- presentation ----------

def render_participants(parts: list[Participant]) -> str:
    if not parts:
        return "  (no participants found)"
    width = max(len(p.name) for p in parts)
    lines = []
    for p in sorted(parts, key=lambda x: x.name.lower()):
        lines.append(f"  {p.name.ljust(width)}  [{p.kind}]  {p.root}")
    return "\n".join(lines)


COMMANDS: list[tuple[str, str, str]] = [
    ("step",    "",                  "Run one routing pass (deliver outboxes, drain inboxes)."),
    ("loop",    "",                  "Run continuously until Ctrl+C or `a8s stop`."),
    ("stop",    "",                  "Signal a running `a8s loop` to exit."),
    ("prompt",  "<name|all> <message>",  'Queue a senderless message in <name>\'s inbox (or all). Wakes via the next step/loop pass. Use "all" to broadcast a prompt.'),
    ("tell",    "<name> <message>",  "Direct routed message to <name>. Sender = participant enclosing CWD."),
    ("says",    "<message>",         "Broadcast routed message to every other participant. Sender = participant enclosing CWD."),
    ("clear",   "",                  "Wipe all mailboxes and flag every participant for fresh conversation on next wake."),
    ("install", "",                  "Install canonical skills from apps/a8s/skills/ into each supported tool's user scope."),
    ("logs",    "<name> [--tail N] [-f]",  "Show recent supervisor-log lines mentioning <name> (like `docker logs`). -f follows."),
    ("define",  "<name> [<path>]",   "Show or set <name>'s definition JSON. Without <path>, prints the effective definition. With <path>, sets it in the registry."),
]

REPL_EXTRAS: list[tuple[str, str, str]] = [
    ("(empty)", "",  "Same as `step`."),
    ("list",    "",  "Re-discover and list participants. Alias: ls."),
    ("help",    "",  "Show this help."),
    ("quit",    "",  "Leave the REPL. Alias: exit."),
]

KNOWN_COMMANDS = {name for name, _, _ in COMMANDS}


def _format_commands(rows: list[tuple[str, str, str]], indent: int = 2) -> str:
    headers = [(n + " " + a).strip() for n, a, _ in rows]
    width = max(len(h) for h in headers)
    return "\n".join(
        f"{' ' * indent}{header.ljust(width)}    {help_text}"
        for header, (_, _, help_text) in zip(headers, rows)
    )


CLI_EPILOG = "Commands (omit for an interactive step REPL):\n" + _format_commands(COMMANDS)
REPL_HELP = (
    "Commands:\n"
    + _format_commands(COMMANDS)
    + "\n\nREPL extras:\n"
    + _format_commands(REPL_EXTRAS)
)


def dispatch(cmd: str, args: list[str], scan_dir: Path, interval: float) -> int:
    if cmd == "step":
        step(scan_dir)
        return 0
    if cmd == "loop":
        return loop_mode(scan_dir, interval)
    if cmd == "stop":
        return cmd_stop()
    if cmd == "prompt":
        return cmd_prompt(scan_dir, args)
    if cmd == "tell":
        return cmd_tell(args)
    if cmd == "says":
        return cmd_says(args)
    if cmd == "clear":
        return cmd_clear(scan_dir)
    if cmd == "install":
        return cmd_install()
    if cmd == "logs":
        return cmd_logs(args)
    if cmd == "define":
        return cmd_define(args)
    raise ValueError(f"unknown command: {cmd!r}")


# ---------- REPL ----------

def repl(scan_dir: Path, interval: float) -> int:
    print(f"a8s — scanning {scan_dir}")
    parts = discover(scan_dir)
    register_discovered(parts)
    print(render_participants(parts))
    while True:
        try:
            line = input("a8s> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            step(scan_dir)
            continue
        try:
            tokens = shlex.split(line)
        except ValueError as e:
            print(f"parse error: {e}")
            continue
        cmd, *args = tokens
        cmd = cmd.lower()

        if cmd in ("quit", "exit"):
            return 0
        if cmd == "help":
            print(REPL_HELP)
            continue
        if cmd in ("list", "ls"):
            print(render_participants(discover(scan_dir)))
            continue
        if cmd in KNOWN_COMMANDS:
            try:
                dispatch(cmd, args, scan_dir, interval)
            except SystemExit:
                raise
            except Exception as e:
                print(f"error: {e}")
            continue
        print(f"unknown command: {cmd!r} (try 'help')")


# ---------- CLI ----------

def _queue_prompt(p: Participant, content: str) -> Path:
    """Drop a senderless message JSON directly into <p>/.inbox/.

    The empty `from` is the signal to `build_prompt` to deliver the
    raw content without a `tells you` / `says` wrapper. The next
    inbox-drain (via `step` or `loop`) wakes the participant.
    """
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


def cmd_prompt(scan_dir: Path, args: list[str]) -> int:
    if len(args) < 2:
        print("usage: a8s prompt <name|all> <message>", file=sys.stderr)
        return 2
    name, *rest = args
    prompt = " ".join(rest)
    parts = discover(scan_dir)
    register_discovered(parts)

    if name.strip().lower() == "all":
        if not parts:
            print("no participants found", file=sys.stderr)
            return 1
        for p in parts:
            _queue_prompt(p, prompt)
        out(f"queued prompt to {len(parts)} participant(s): {_preview(prompt)}")
        return 0

    target = find_participant(parts, name)
    if target is None:
        print(f"no participant named {name!r}", file=sys.stderr)
        return 1
    _queue_prompt(target, prompt)
    out(f"queued prompt to {target.name}: {_preview(prompt)}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="a8s",
        description="Agent Infinity System — route messages between Claude / Gemini / Codex projects.",
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dir", default=".", help="scan root (default: cwd)")
    parser.add_argument("--interval", type=float, default=1.0, help="loop poll interval seconds (default: 1.0)")
    parser.add_argument("--unrestricted", action="store_true",
                        help="drop per-tool gating where possible: claude switches to "
                             "--dangerously-skip-permissions, codex to "
                             "--dangerously-bypass-approvals-and-sandbox. Gemini is already "
                             "fully permissive in headless mode (Policy Engine doesn't apply).")
    parser.add_argument("command", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    scan_dir = Path(args.dir).resolve()
    if not scan_dir.is_dir():
        print(f"not a directory: {scan_dir}", file=sys.stderr)
        return 2

    global UNRESTRICTED
    UNRESTRICTED = bool(args.unrestricted)
    if UNRESTRICTED:
        print("[a8s] UNRESTRICTED: claude --dangerously-skip-permissions, codex --dangerously-bypass-approvals-and-sandbox", file=sys.stderr)

    if args.command is None:
        return repl(scan_dir, args.interval)
    if args.command in KNOWN_COMMANDS:
        return dispatch(args.command, args.rest, scan_dir, args.interval)

    print(f"unknown command: {args.command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
