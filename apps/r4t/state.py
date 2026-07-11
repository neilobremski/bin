"""Out-of-repo team state under ~/.config/r4t/teams/<node>/ (honors
XDG_CONFIG_HOME; relocate wholesale with R4T_HOME, mirroring how a8s
honors A8S_HOME).

    teams/<node>/
    ├── agents/<name>/history.md   rolling conversation memory (messages only), ~8KB cap
    ├── agents/<name>/.lock        PID lockfile — one turn per agent at a time
    ├── agents/<name>/.turn.json   in-flight turn: task/hop/sender;
    │                              a leftover file with no live lock = crashed turn
    ├── agents/<name>/meta.json    last inbound / last completed turn bookkeeping
    ├── agents/<name>/staging/     per-turn $TELL_OUTBOX_DIR — envelopes the agent
    │                              sent this turn, released by dispatch afterwards
    ├── tasks/<id>.json            task ledger (see tasks.py)
    ├── pending/                   messages deferred on concurrency/throttle limits
    ├── dead-letter/               suppressed/cut/excess messages, x-death-style records
    ├── suppression.json           content-keyed pair suppression window
    ├── buckets.json               per-agent reply-privilege token buckets
    ├── active.json                idle-recovery watch list (agent → ttl)
    ├── rotation.json              per-rig round-robin index for harness pools
    ├── last-turn-start            cadence stamp for the team throttle
    ├── log/<date>.md              full I/O transcript, append-only
    └── velocity.csv               one row per harness turn

Never inside the repo: the working tree is only touched by the harness
subprocesses themselves.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from ulid import new as new_ulid

HISTORY_MAX_BYTES = 8192
HISTORY_ENTRY_RE = re.compile(r"(?m)^(?=## )")
VELOCITY_HEADER = "timestamp,agent,rig,task,hop,duration_seconds,exit_code\n"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def r4t_home() -> Path:
    raw = os.environ.get("R4T_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "r4t"


def teams_dir() -> Path:
    return r4t_home() / "teams"


def team_dir(node: str) -> Path:
    return teams_dir() / node.strip().lower()


def agent_dir(node: str, name: str) -> Path:
    return team_dir(node) / "agents" / name.strip().lower()


def known_teams() -> list[str]:
    root = teams_dir()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{new_ulid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_json(path: Path, payload: dict) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2))


# ---------- history ----------

def history_path(node: str, name: str) -> Path:
    return agent_dir(node, name) / "history.md"


def read_history(node: str, name: str) -> str:
    path = history_path(node, name)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _truncate_history(text: str, max_bytes: int) -> str:
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    entries = [e for e in HISTORY_ENTRY_RE.split(text) if e.strip()]
    while len(entries) > 1 and len("".join(entries).encode("utf-8")) > max_bytes:
        entries.pop(0)
    return "".join(entries)


def append_history(
    node: str, name: str, entry: str, *, max_bytes: int = HISTORY_MAX_BYTES
) -> None:
    current = read_history(node, name).rstrip()
    combined = (current + "\n\n" if current else "") + entry.strip() + "\n"
    _atomic_write_text(history_path(node, name), _truncate_history(combined, max_bytes))


# ---------- locks ----------

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class AgentLock:
    def __init__(self, node: str, name: str) -> None:
        self.path = agent_dir(node, name) / ".lock"
        self.acquired = False

    def acquire(self, rig: str) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"pid": os.getpid(), "rig": rig, "started": utc_now()}
        )
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = read_lock(self.path)
                if holder is not None and _pid_alive(int(holder.get("pid", 0) or 0)):
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    return False
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            self.acquired = True
            return True
        return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self.acquired = False


class ProcessLock:
    """Exclusive PID lock for short cross-process state transactions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.acquired = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "started": utc_now()})
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = read_lock(self.path)
                if holder is not None and _pid_alive(int(holder.get("pid", 0) or 0)):
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    return False
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            self.acquired = True
            return True
        return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self.acquired = False


def admission_lock(node: str) -> ProcessLock:
    return ProcessLock(team_dir(node) / ".admission.lock")


def task_lock(node: str, task_id: str) -> ProcessLock:
    return ProcessLock(team_dir(node) / "tasks" / f".{task_id}.lock")


def read_lock(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def live_locks(node: str, *, prune: bool = True) -> list[dict]:
    """Scan agents/*/.lock; return live ones as dicts with an `agent` key.
    Dead-PID locks are removed when `prune` (they're stale by definition)."""
    agents_root = team_dir(node) / "agents"
    if not agents_root.is_dir():
        return []
    out: list[dict] = []
    for entry in sorted(agents_root.iterdir()):
        lock_path = entry / ".lock"
        if not lock_path.is_file():
            continue
        data = read_lock(lock_path)
        pid = int((data or {}).get("pid", 0) or 0)
        if data is None or not _pid_alive(pid):
            if prune:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
            continue
        data["agent"] = entry.name
        out.append(data)
    return out


def count_rig_locks(node: str, rig: str) -> int:
    key = rig.lower()
    return sum(1 for lock in live_locks(node) if str(lock.get("rig", "")).lower() == key)


def prune_stale_locks(node: str) -> int:
    agents_root = team_dir(node) / "agents"
    if not agents_root.is_dir():
        return 0
    before = sum(1 for e in agents_root.iterdir() if (e / ".lock").is_file())
    after = len(live_locks(node, prune=True))
    return max(0, before - after)


# ---------- pending (concurrency/throttle-deferred messages) ----------

def pending_dir(node: str) -> Path:
    return team_dir(node) / "pending"


def park_pending(node: str, envelope: dict) -> Path:
    envelope = dict(envelope)
    envelope.setdefault("id", new_ulid())
    envelope.setdefault("queued_at", utc_now())
    path = pending_dir(node) / f"{envelope['id']}.json"
    atomic_write_json(path, envelope)
    return path


def list_pending(node: str) -> list[Path]:
    root = pending_dir(node)
    if not root.is_dir():
        return []
    return sorted(f for f in root.iterdir() if f.is_file() and f.name.endswith(".json"))


# ---------- transcript log + velocity ----------

def append_log(node: str, text: str) -> None:
    log_dir = team_dir(node) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with (log_dir / f"{day}.md").open("a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n\n")


def _csv_field(value: object) -> str:
    text = str(value)
    if any(c in text for c in ",\"\n"):
        return '"' + text.replace('"', '""') + '"'
    return text


def record_velocity(
    node: str,
    *,
    agent: str,
    rig: str,
    task: str,
    hop: int,
    duration_seconds: float,
    exit_code: int,
) -> None:
    path = team_dir(node) / "velocity.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.is_file()
    row = ",".join(
        _csv_field(v)
        for v in (
            utc_now(),
            agent,
            rig,
            task,
            hop,
            f"{duration_seconds:.2f}",
            exit_code,
        )
    )
    with path.open("a", encoding="utf-8") as f:
        if fresh:
            f.write(VELOCITY_HEADER)
        f.write(row + "\n")


# ---------- per-turn state (staging outbox + crash evidence) ----------

def turn_path(node: str, name: str) -> Path:
    return agent_dir(node, name) / ".turn.json"


def write_turn(node: str, name: str, payload: dict) -> Path:
    path = turn_path(node, name)
    atomic_write_json(path, payload)
    return path


def read_turn(node: str, name: str) -> dict | None:
    path = turn_path(node, name)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def clear_turn(node: str, name: str) -> None:
    try:
        turn_path(node, name).unlink()
    except OSError:
        pass


def staging_dir(node: str, name: str) -> Path:
    return agent_dir(node, name) / "staging"


def prepare_staging(node: str, name: str) -> Path:
    """Fresh per-turn staging outbox. Dispatch points the harness
    subprocess's $TELL_OUTBOX_DIR here, so the unmodified `tell` writes the
    agent's envelopes into a dir only this turn owns — attribution for
    free. Leftovers from a crashed turn are wiped, not released."""
    d = staging_dir(node, name)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def staged_envelopes(node: str, name: str) -> list[Path]:
    d = staging_dir(node, name)
    if not d.is_dir():
        return []
    return sorted(f for f in d.iterdir() if f.is_file() and f.name.endswith(".json"))


# ---------- dead letters ----------

def dead_letter_dir(node: str) -> Path:
    return team_dir(node) / "dead-letter"


def record_dead_letter(
    node: str,
    *,
    reason: str,
    sender: str,
    to: str,
    task: str,
    content: str,
    count: int = 1,
) -> Path:
    record_id = new_ulid()
    path = dead_letter_dir(node) / f"{record_id}.json"
    atomic_write_json(
        path,
        {
            "id": record_id,
            "time": utc_now(),
            "reason": reason,
            "count": count,
            "from": sender,
            "to": to,
            "task": task,
            "content": content[:2000],
        },
    )
    return path


def list_dead_letters(node: str) -> list[dict]:
    root = dead_letter_dir(node)
    if not root.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


# ---------- content-keyed pair suppression ----------

def suppression_path(node: str) -> Path:
    return team_dir(node) / "suppression.json"


def suppression_check(node: str, key: str, window_seconds: float) -> tuple[bool, int]:
    """Record `key` and report whether it repeated within the window.
    Returns (suppressed, occurrence_count). Entries outside the window are
    pruned on every call, so the store stays small."""
    path = suppression_path(node)
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            pass
    now = time.time()
    data = {
        k: v
        for k, v in data.items()
        if isinstance(v, dict) and now - float(v.get("last", 0)) <= window_seconds
    }
    entry = data.get(key)
    if entry is not None:
        entry["count"] = int(entry.get("count", 1)) + 1
        entry["last"] = now
        atomic_write_json(path, data)
        return True, entry["count"]
    data[key] = {"count": 1, "first": now, "last": now}
    atomic_write_json(path, data)
    return False, 1


# ---------- reply-privilege token buckets ----------

def buckets_path(node: str) -> Path:
    return team_dir(node) / "buckets.json"


def read_buckets(node: str) -> dict:
    path = buckets_path(node)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def bucket_level(node: str, name: str, bucket_max: float) -> float:
    raw = read_buckets(node).get(name.lower())
    try:
        return min(float(raw), bucket_max)
    except (TypeError, ValueError):
        return bucket_max


def _bucket_set(node: str, name: str, level: float) -> None:
    data = read_buckets(node)
    data[name.lower()] = round(level, 4)
    atomic_write_json(buckets_path(node), data)


def bucket_drain(node: str, name: str, amount: float, bucket_max: float) -> float:
    level = max(0.0, bucket_level(node, name, bucket_max) - amount)
    _bucket_set(node, name, level)
    return level


def bucket_earn(node: str, name: str, ratio: float, bucket_max: float) -> float:
    level = min(bucket_max, bucket_level(node, name, bucket_max) + ratio)
    _bucket_set(node, name, level)
    return level


def bucket_muted(level: float, bucket_max: float) -> bool:
    return level < bucket_max / 2.0


# ---------- per-agent failure breaker ----------

def breaker_open(
    node: str, name: str, cap: int, cooldown_seconds: float
) -> tuple[bool, int]:
    """systemd-StartLimitBurst-style breaker: `cap` consecutive failed turns
    (nonzero exit or timeout, tracked in meta.json by dispatch) opens it.
    While open, turns are blocked until `cooldown_seconds` have passed since
    the last failure — then one probe turn is let through (half-open); a
    clean turn resets the count and closes it. Returns (blocked, count)."""
    meta = read_meta(node, name)
    count = int(meta.get("consecutive_failures", 0) or 0)
    if cap <= 0 or count < cap:
        return False, count
    raw = str(meta.get("last_failure_at", ""))
    try:
        last = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False, count
    return (time.time() - last) < cooldown_seconds, count


def clear_failures(node: str, name: str) -> None:
    update_meta(node, name, consecutive_failures=0)


# ---------- per-agent meta (idle recovery bookkeeping) ----------

def meta_path(node: str, name: str) -> Path:
    return agent_dir(node, name) / "meta.json"


def read_meta(node: str, name: str) -> dict:
    path = meta_path(node, name)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def update_meta(node: str, name: str, **fields) -> dict:
    meta = read_meta(node, name)
    meta.update(fields)
    atomic_write_json(meta_path(node, name), meta)
    return meta


# ---------- harness pool rotation ----------

def take_rotation(node: str, rig: str, pool_size: int) -> int:
    """Return the round-robin index for this rig's next turn and persist
    the advance. Single-variant rigs always get 0 without touching disk."""
    if pool_size <= 1:
        return 0
    path = team_dir(node) / "rotation.json"
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            pass
    try:
        index = int(data.get(rig, 0)) % pool_size
    except (TypeError, ValueError):
        index = 0
    data[rig] = index + 1
    atomic_write_json(path, data)
    return index


# ---------- team throttle cadence ----------

def last_turn_start_path(node: str) -> Path:
    return team_dir(node) / "last-turn-start"


def stamp_last_turn_start(node: str) -> None:
    _atomic_write_text(last_turn_start_path(node), utc_now() + "\n")


def read_last_turn_start(node: str) -> float | None:
    path = last_turn_start_path(node)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (OSError, ValueError):
        return None


# ---------- active list (idle-driven crash recovery) ----------

def active_path(node: str) -> Path:
    return team_dir(node) / "active.json"


def load_active(node: str) -> dict:
    path = active_path(node)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_active(node: str, data: dict) -> None:
    atomic_write_json(active_path(node), data)


def refresh_active(node: str, name: str, ttl: int) -> None:
    data = load_active(node)
    entry = data.get(name.lower())
    if not isinstance(entry, dict):
        entry = {}
    entry["ttl"] = ttl
    entry["refreshed_at"] = utc_now()
    data[name.lower()] = entry
    save_active(node, data)


def mark_nudged(node: str, name: str) -> None:
    data = load_active(node)
    entry = data.get(name.lower())
    if isinstance(entry, dict):
        entry["last_nudge_at"] = utc_now()
        save_active(node, data)
