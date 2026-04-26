"""a8s — Agent Infinity System.

Discovery, step REPL, outbox->inbox routing, subprocess waking, loop/stop,
clear (with one-shot fresh flag), and a participant registry under
~/.a8s/a8s.json that backs the `tell` CLI.

Per-tool skill installation for Gemini/Codex is not yet implemented; the
Claude install path piggybacks on ~/bin/install.sh via a symlink in
~/bin/docs/.
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


def out(text: str = "", end: str = "\n") -> None:
    line = text + end
    if PRINT_LOCK is not None:
        with PRINT_LOCK:
            sys.stdout.write(line)
            sys.stdout.flush()
    else:
        sys.stdout.write(line)
        sys.stdout.flush()


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
    for sub in (".inbox", ".outbox", ".trash"):
        (p.root / sub).mkdir(exist_ok=True)


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
        outbox = sender.root / ".outbox"
        for f in sorted(outbox.iterdir()):
            if not (f.is_file() and f.name.endswith(".json")):
                continue
            try:
                with f.open("r", encoding="utf-8") as fp:
                    msg = json.load(fp)
            except (OSError, json.JSONDecodeError) as e:
                out(f"[{sender.name}] outbox parse error on {f.name}: {e}")
                continue
            recipient_name = (msg.get("to") or "").strip()
            if not recipient_name:
                # broadcast: fan out to every other participant.
                others = [p for p in participants if p.name != sender.name]
                for recipient in others:
                    ensure_mailboxes(recipient)
                    dest = unique_path(recipient.root / ".inbox" / f.name)
                    shutil.copyfile(f, dest)
                f.unlink()
                out(f"broadcast: {sender.name} -> {len(others)} ({f.name})")
                routed += len(others)
                continue
            recipient = by_name.get(recipient_name.lower())
            if recipient is None:
                out(f"[{sender.name}] unknown recipient {recipient_name!r} in {f.name}")
                continue
            ensure_mailboxes(recipient)
            dest = unique_path(recipient.root / ".inbox" / f.name)
            f.rename(dest)
            out(f"routed: {sender.name} -> {recipient.name} ({dest.name})")
            routed += 1
    return routed


def next_inbox_message(p: Participant) -> Path | None:
    inbox = p.root / ".inbox"
    if not inbox.is_dir():
        return None
    files = sorted(f for f in inbox.iterdir() if f.is_file() and f.name.endswith(".json"))
    return files[0] if files else None


def build_prompt(msg: dict) -> str:
    sender = msg.get("from", "")
    content = msg.get("content", "")
    date = msg.get("date", "")
    recipient = (msg.get("to") or "").strip()
    verb = "told you" if recipient else "says"
    header = f"[{date}] {sender} {verb}: {content}" if date else f"{sender} {verb}: {content}"
    parts = [header]
    files = msg.get("files") or []
    if files:
        parts.append("")
        for entry in files:
            path = entry.get("path") or entry.get("filename")
            if path:
                parts.append(f"FILE: {path}")
    return "\n".join(parts)


CLAUDE_DEFAULT_ALLOWED_TOOLS = (
    "Bash(tell:*) Bash(says:*) Read Edit Write Glob Grep WebFetch WebSearch TodoWrite"
)


def build_command(kind: str, prompt: str, fresh: bool = False) -> list[str]:
    """Build the subprocess command for waking a participant.

    Default mode bakes in just enough permission to make headless tool use
    work for `/tell` and routine file ops:
      - Claude: `--permission-mode dontAsk --allowedTools <list>` so denials
        are silent (headless prompts would hang). Per-project `.claude/settings.json`
        permissions.allow rules layer additively on top.
      - Gemini: `--yolo` (full auto-approval) because the Policy Engine
        doesn't apply in headless `-p` mode (tracked upstream as
        google-gemini/gemini-cli#20469). To revisit when fixed.
      - Codex: `--full-auto` (workspace-write sandbox, auto-approval).

    `--unrestricted` drops the gating where a stronger mode exists:
      - Claude: `--dangerously-skip-permissions`
      - Codex:  `--dangerously-bypass-approvals-and-sandbox`
      - Gemini: no change (already unrestricted by necessity).
    """
    if kind == "claude":
        cmd = ["claude"]
        if UNRESTRICTED:
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd += ["--permission-mode", "dontAsk",
                    "--allowedTools", CLAUDE_DEFAULT_ALLOWED_TOOLS]
        if not fresh:
            cmd.append("--continue")
        cmd += ["-p", prompt]
        return cmd
    if kind == "gemini":
        cmd = ["gemini", "--yolo"]
        if not fresh:
            cmd += ["--resume", "latest"]
        cmd += ["--prompt", prompt]
        return cmd
    if kind == "codex":
        cmd = ["codex", "exec"]
        if not fresh:
            cmd += ["resume", "--last"]
        cmd.append("--dangerously-bypass-approvals-and-sandbox" if UNRESTRICTED else "--full-auto")
        cmd.append("--skip-git-repo-check")
        cmd.append(prompt)
        return cmd
    raise ValueError(f"unknown kind: {kind}")


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
        bad = unique_path(p.root / ".trash" / msg_path.name)
        msg_path.rename(bad)
        return

    prompt = build_prompt(msg)
    trashed = unique_path(p.root / ".trash" / msg_path.name)
    msg_path.rename(trashed)
    fresh = consume_fresh(p.root)
    out(f"[{p.name}] waking from {trashed.name}" + (" (fresh)" if fresh else ""))
    cmd = build_command(p.kind, prompt, fresh=fresh)
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
    outbox = sender_root / ".outbox"
    outbox.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    msg = {
        "date": now.isoformat().replace("+00:00", "Z"),
        "from": sender_name,
        "to": to,
        "content": content,
        "files": files,
    }
    safe_sender = re.sub(r"[^A-Za-z0-9_-]", "_", sender_name)
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
    preview = (content[:60] + "…") if len(content) > 60 else content
    print(f"tell -> {target_name}: {preview}")
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
    preview = (content[:60] + "…") if len(content) > 60 else content
    print(f"says: {preview}")
    return 0


def cmd_clear(scan_dir: Path) -> int:
    parts = discover(scan_dir)
    if not parts:
        print("no participants found")
        return 0
    cleared = 0
    for p in parts:
        ensure_mailboxes(p)
        for sub in (".inbox", ".outbox", ".trash"):
            for f in (p.root / sub).iterdir():
                if f.is_file():
                    f.unlink()
                    cleared += 1
    mark_fresh([p.root for p in parts])
    print(f"cleared {cleared} message(s) across {len(parts)} participant(s)")
    print("next wake for each will start a new conversation")
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
    ("prompt",  "<name> <message>",  'Wake <name> directly with this prompt (raw — no "from" wrapper).'),
    ("tell",    "<name> <message>",  "Direct routed message to <name>. Sender = participant enclosing CWD."),
    ("says",    "<message>",         "Broadcast routed message to every other participant. Sender = participant enclosing CWD."),
    ("clear",   "",                  "Wipe all mailboxes and flag every participant for fresh conversation on next wake."),
    ("install", "",                  "Install canonical skills from apps/a8s/skills/ into each supported tool's user scope."),
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

def cmd_prompt(scan_dir: Path, args: list[str]) -> int:
    if len(args) < 2:
        print("usage: a8s prompt <name> <message>", file=sys.stderr)
        return 2
    name, *rest = args
    prompt = " ".join(rest)
    parts = discover(scan_dir)
    register_discovered(parts)
    target = find_participant(parts, name)
    if target is None:
        print(f"no participant named {name!r}", file=sys.stderr)
        return 1
    fresh = consume_fresh(target.root)
    out(f"[{target.name}] direct prompt" + (" (fresh)" if fresh else ""))
    cmd = build_command(target.kind, prompt, fresh=fresh)
    return run_with_prefix(target.name, cmd, target.root)


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
