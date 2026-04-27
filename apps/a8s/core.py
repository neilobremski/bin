"""a8s core — paths, logging, Participant, leaf-level helpers.

This module has no a8s sibling imports. Everything else (registry, mailbox,
definitions, daemon, commands, cli) imports from here.

Mutable module-level state:
  PRINT_LOCK   — None at module load. `daemon.attached_loop` sets it to a
                 threading.Lock so concurrent log writes serialize. `out` and
                 `out_agent` reference it through this module so updates are
                 visible across imports (`import core; core.PRINT_LOCK = ...`).
"""
from __future__ import annotations

import os
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# ---------- constants ----------

MARKER_FILES = {
    "CLAUDE.md": "claude",
    "GEMINI.md": "gemini",
    "CODEX.md": "codex",
}

NAME_RE = re.compile(r"[A-Za-z0-9]+")

# Path constants — computed once at module load.
SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = SCRIPT_DIR / "skills"
BIN_ROOT = SCRIPT_DIR.parent.parent
DEFINITIONS_DIR = SCRIPT_DIR / "definitions"
# Explicit path for `cmd_start`'s re-exec. After the modular split, `__file__`
# resolved inside any module would point at that module — not the entry script.
ENTRYPOINT = SCRIPT_DIR / "a8s.py"

# Mutable: `daemon.attached_loop` sets this to a threading.Lock so log writes
# from concurrent paths serialize. Read by `out` and `out_agent` below.
PRINT_LOCK: threading.Lock | None = None


# ---------- ~/.a8s/ paths ----------

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


def agent_dir(name: str) -> Path:
    """Per-agent internal directory under ~/.a8s/. Holds inbox/, trash/,
    log.txt, and the pid file."""
    return _a8s_dir() / "agents" / _safe_name(name)


def inbox_dir(name: str) -> Path:
    return agent_dir(name) / "inbox"


def trash_dir(name: str) -> Path:
    return agent_dir(name) / "trash"


def agent_log_path(name: str) -> Path:
    return agent_dir(name) / "log.txt"


def pid_path(name: str) -> Path:
    return agent_dir(name) / "pid"


def outbox_dir(root: Path) -> Path:
    """Outbox lives **inside the agent's own dir** so the agent can write to it
    even under a strict workspace sandbox (codex --full-auto). Inbox and trash
    stay isolated under ~/.a8s/agents/<NAME>/ where the agent never sees them.

    `route_outboxes()` re-stamps the `from` field to the enclosing participant's
    name on every read, so an agent can't spoof a senderless prompt by writing
    a JSON with `from: ""`.
    """
    return root / ".outbox"


def registry_path() -> Path:
    base = Path.home() / ".a8s"
    base.mkdir(parents=True, exist_ok=True)
    return base / "a8s.json"


# ---------- general helpers ----------

def _preview(content: str, n: int = 80) -> str:
    """Single-line snippet of `content` for log readability."""
    s = (content or "").replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def unique_path(p: Path) -> Path:
    """Return `p` if it doesn't exist; otherwise `p.stem.<N><suffix>` where N
    is the smallest positive integer that doesn't collide."""
    if not p.exists():
        return p
    i = 1
    while True:
        candidate = p.with_name(f"{p.stem}.{i}{p.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# ---------- logging ----------

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


# ---------- types ----------

@dataclass(frozen=True)
class Participant:
    name: str
    root: Path
