"""Out-of-repo team state under ~/.config/r4t/teams/<node>/ (honors
XDG_CONFIG_HOME; relocate wholesale with R4T_HOME, mirroring how a8s
honors A8S_HOME).

    teams/<node>/
    ├── agents/<name>/history.md   rolling conversation memory (messages only), ~8KB cap
    ├── agents/<name>/queue/       durable inbound queue — one envelope per file,
    │                              ULID-named; a turn drains the whole queue at once
    ├── agents/<name>/.lock        PID lockfile — one turn per agent at a time
    ├── agents/<name>/.turn.json   in-flight turn: thread/hop/sender;
    │                              a leftover file with no live lock = crashed turn
    ├── agents/<name>/meta.json    last inbound / last completed turn bookkeeping
    ├── agents/<name>/staging/     per-turn $TELL_OUTBOX_DIR — envelopes the agent
    │                              sent this turn, released by dispatch afterwards
    ├── tasks/<id>.json            thread ledger (see tasks.py)
    ├── dead-letter/               undeliverable mail (unknown recipient, malformed)
    ├── buckets.json               per-member + team spend budgets (turns, not tokens)
    ├── rotation.json              per-rig round-robin index for harness pools
    ├── last-turn-start            cadence stamp for the team throttle
    ├── log/<date>.md              full I/O transcript, append-only
    └── velocity.csv               one row per harness turn

Never inside the repo: the working tree is only touched by the harness
subprocesses themselves.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from ulid import new as new_ulid

_queue_seq = itertools.count()

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


def root_path(node: str) -> Path:
    return team_dir(node) / "root"


def stamp_root(node: str, root: Path) -> None:
    path = root_path(node)
    text = str(root)
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip() == text:
            return
    except OSError:
        pass
    _atomic_write_text(path, text + "\n")


def read_root(node: str) -> Path | None:
    try:
        text = root_path(node).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text) if text else None


def node_for_root(cwd: Path) -> str | None:
    """The team whose stamped repo root is cwd or an ancestor of it."""
    by_root = {}
    for node in known_teams():
        root = read_root(node)
        if root is not None:
            by_root[root] = node
    cwd = cwd.resolve()
    for candidate in (cwd, *cwd.parents):
        if candidate in by_root:
            return by_root[candidate]
    return None


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


# ---------- durable member queue (batch invoke; nothing is ever dropped) ----------

def queue_dir(node: str, name: str) -> Path:
    return agent_dir(node, name) / "queue"


def _normalize_body(text: str) -> str:
    return " ".join((text or "").lower().split())


def list_queue(node: str, name: str) -> list[Path]:
    d = queue_dir(node, name)
    if not d.is_dir():
        return []
    return sorted(f for f in d.iterdir() if f.is_file() and f.name.endswith(".json"))


def read_queue(node: str, name: str) -> list[dict]:
    out: list[dict] = []
    for path in list_queue(node, name):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def queue_depth(node: str, name: str) -> int:
    return len(list_queue(node, name))


def enqueue(node: str, name: str, envelope: dict) -> Path:
    """Append an inbound envelope to a member's durable queue. Duplicate
    collapse (the only suppression left): if the NEWEST queued entry has the
    same sender and identical normalized body, bump its `repeats` count and
    re-stamp instead of adding a file — collapsing loses no information."""
    d = queue_dir(node, name)
    d.mkdir(parents=True, exist_ok=True)
    existing = list_queue(node, name)
    if existing:
        newest = existing[-1]
        try:
            prev = json.loads(newest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = None
        if (
            isinstance(prev, dict)
            and str(prev.get("from", "")).strip().lower()
            == str(envelope.get("from", "")).strip().lower()
            and _normalize_body(str(prev.get("body", "")))
            == _normalize_body(str(envelope.get("body", "")))
        ):
            prev["repeats"] = int(prev.get("repeats", 1) or 1) + 1
            prev["queued_at"] = utc_now()
            atomic_write_json(newest, prev)
            return newest
    env = dict(envelope)
    env.setdefault("id", new_ulid())
    env.setdefault("repeats", 1)
    env.setdefault("queued_at", utc_now())
    # The FILENAME orders the queue, so it must be monotonic in arrival order —
    # ULIDs are not, within a millisecond. A wall-clock nanosecond stamp plus a
    # process-local counter is: two enqueues in one process never collide, and
    # separate wake processes are genuinely ordered by wall time.
    path = d / f"{time.time_ns():020d}-{next(_queue_seq):06d}.json"
    atomic_write_json(path, env)
    return path


def claim_queue(node: str, name: str) -> list[dict]:
    """Read and remove every currently-queued envelope in arrival order.
    Called under the agent lock at turn start, so no two turns claim the same
    batch; envelopes arriving mid-turn are written after this snapshot and
    ride the next turn."""
    entries: list[dict] = []
    for path in list_queue(node, name):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            continue
        if isinstance(data, dict):
            entries.append(data)
        path.unlink(missing_ok=True)
    return entries


def members_with_queue(node: str) -> list[str]:
    root = team_dir(node) / "agents"
    if not root.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(root.iterdir()):
        q = entry / "queue"
        if q.is_dir() and any(
            f.is_file() and f.name.endswith(".json") for f in q.iterdir()
        ):
            out.append(entry.name)
    return out


# ---------- seat (the roster human's mailbox on the node) ----------

def seat_dir(node: str, name: str) -> Path:
    return team_dir(node) / "seat" / name.strip().lower()


def seat_inbox_dir(node: str, name: str) -> Path:
    return seat_dir(node, name) / "inbox"


def seat_read_dir(node: str, name: str) -> Path:
    return seat_dir(node, name) / "read"


def park_seat_message(node: str, name: str, sender: str, content: str) -> Path:
    envelope = {
        "id": new_ulid(),
        "from": sender,
        "to": name.strip().lower(),
        "content": content,
        "parked_at": utc_now(),
    }
    path = seat_inbox_dir(node, name) / f"{envelope['id']}.json"
    atomic_write_json(path, envelope)
    return path


def list_seat_messages(node: str, name: str, *, read: bool = False) -> list[Path]:
    root = seat_read_dir(node, name) if read else seat_inbox_dir(node, name)
    if not root.is_dir():
        return []
    return sorted(f for f in root.iterdir() if f.is_file() and f.name.endswith(".json"))


def mark_seat_read(node: str, name: str, path: Path) -> Path:
    dest_dir = seat_read_dir(node, name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    path.rename(dest)
    return dest


def seat_presence_path(node: str, name: str) -> Path:
    return seat_dir(node, name) / "presence"


def touch_seat_presence(node: str, name: str) -> None:
    p = seat_presence_path(node, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(p, str(os.getpid()))


def clear_seat_presence(node: str, name: str) -> None:
    try:
        seat_presence_path(node, name).unlink()
    except OSError:
        pass


def seat_attached(node: str, name: str) -> bool:
    try:
        pid = int(seat_presence_path(node, name).read_text().strip())
    except (OSError, ValueError):
        return False
    return pid > 0 and _pid_alive(pid)


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


# ---------- spend budgets (a turn costs 1 unit; empty = resting) ----------
#
# A bifurcated token bucket, refilled lazily by elapsed wall-clock time: each
# member has its own bucket, and the whole cell shares one. A turn costs 1
# member unit AND 1 cell unit, regardless of how many queued messages it
# consumes — batching is rewarded by construction. An empty bucket means the
# member is not runnable ("resting"); the queue simply holds. Nothing is muted,
# nothing is dropped.

CELL_BUDGET_KEY = "__cell__"


def fmt_budget(value: float) -> str:
    """Budget level as a clean number: 8.0 -> "8", 7.5 -> "7.5"."""
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


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


def budget_level(
    node: str, key: str, budget_max: float, earn_per_hour: float, *, now: float | None = None
) -> float:
    """Current balance: the stored level plus whatever has been earned by
    elapsed wall-clock time since it was last written, capped at `budget_max`.
    An unseen key starts full."""
    now = time.time() if now is None else now
    entry = read_buckets(node).get(key.lower())
    if not isinstance(entry, dict):
        return float(budget_max)
    try:
        level = float(entry.get("level", budget_max))
        at = float(entry.get("at", now))
    except (TypeError, ValueError):
        return float(budget_max)
    earned = max(0.0, now - at) / 3600.0 * earn_per_hour
    return min(float(budget_max), level + earned)


def budget_charge(
    node: str,
    key: str,
    budget_max: float,
    earn_per_hour: float,
    amount: float = 1.0,
    *,
    now: float | None = None,
) -> float:
    now = time.time() if now is None else now
    level = budget_level(node, key, budget_max, earn_per_hour, now=now)
    new_level = max(0.0, level - amount)
    data = read_buckets(node)
    data[key.lower()] = {"level": round(new_level, 4), "at": now}
    atomic_write_json(buckets_path(node), data)
    return new_level


def budget_seconds_until(
    node: str,
    key: str,
    budget_max: float,
    earn_per_hour: float,
    target: float = 1.0,
    *,
    now: float | None = None,
) -> float:
    """Seconds until the bucket refills to `target` (0.0 when already there,
    inf when it never will because nothing is earned)."""
    level = budget_level(node, key, budget_max, earn_per_hour, now=now)
    if level >= target:
        return 0.0
    if earn_per_hour <= 0:
        return float("inf")
    return (target - level) / earn_per_hour * 3600.0


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


