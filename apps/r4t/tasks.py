"""Task envelope + ledger — the governance core.

Every message r4t can influence carries a machine-parsable header line:

    [r4t task=<ulid> hop=<n>]

Incoming: parse + strip, adopt task/hop. Missing header (including on
intra-namespace messages — defense in depth) → new task ULID, hop 0.
Outgoing: agents are given the exact next-hop header verbatim and told to
copy it at the start of every `tell` for the task.

Turn budget is weighted by tier: each dispatch by a tier whose
`max_turns_per_task` is M consumes 1/M of the task's budget (which starts
at 1.0). A task run entirely by one tier therefore gets exactly
`max_turns_per_task` turns; mixed-tier chains pro-rate. `task approve <id>
--turns N` extends the budget by N turns of the tier that hit the limit.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from state import atomic_write_json, team_dir, utc_now
from ulid import new as new_ulid
import json

HEADER_RE = re.compile(
    r"^\s*\[r4t\s+task=([0-9A-Za-z]{26})\s+hop=(\d+)\]\s*", re.IGNORECASE
)
DEFAULT_BUDGET = 1.0
DEFAULT_APPROVE_TURNS = 5
_EPSILON = 1e-9

STATUS_OPEN = "open"
STATUS_PARKED = "parked"


def new_task_id() -> str:
    return new_ulid()


def parse_header(message: str) -> tuple[str | None, int, str]:
    """Return (task_id, hop, body-with-header-stripped)."""
    match = HEADER_RE.match(message or "")
    if not match:
        return None, 0, (message or "").strip()
    task_id = match.group(1).upper()
    hop = int(match.group(2))
    return task_id, hop, message[match.end():].strip()


def format_header(task_id: str, hop: int) -> str:
    return f"[r4t task={task_id} hop={hop}]"


# ---------- ledger ----------

def tasks_dir(node: str) -> Path:
    return team_dir(node) / "tasks"


def task_path(node: str, task_id: str) -> Path:
    return tasks_dir(node) / f"{task_id}.json"


def parked_dir(node: str, task_id: str) -> Path:
    return tasks_dir(node) / task_id / "parked"


def load_task(node: str, task_id: str) -> dict | None:
    path = task_path(node, task_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_task(node: str, task: dict) -> None:
    task["updated_at"] = utc_now()
    atomic_write_json(task_path(node, task["id"]), task)


def new_task(task_id: str, creator: str) -> dict:
    now = utc_now()
    return {
        "id": task_id,
        "creator": creator,
        "created_at": now,
        "updated_at": now,
        "status": STATUS_OPEN,
        "used": 0.0,
        "budget": DEFAULT_BUDGET,
        "turns": 0,
        "parked_tier_max": None,
        "park_notified": False,
        "cut_notified": False,
    }


def ensure_task(node: str, task_id: str, creator: str) -> dict:
    task = load_task(node, task_id)
    if task is None:
        task = new_task(task_id, creator)
        save_task(node, task)
    return task


def charge_turn(task: dict, max_turns_per_task: int) -> bool:
    """Consume one weighted turn. Returns False (nothing consumed) when the
    charge would exceed the budget."""
    cost = 1.0 / max(1, max_turns_per_task)
    used = float(task.get("used", 0.0))
    budget = float(task.get("budget", DEFAULT_BUDGET))
    if used + cost > budget + _EPSILON:
        return False
    task["used"] = used + cost
    task["turns"] = int(task.get("turns", 0)) + 1
    return True


def approve(node: str, task_id: str, turns: int) -> dict:
    task = load_task(node, task_id)
    if task is None:
        raise KeyError(task_id)
    per_turn_max = int(task.get("parked_tier_max") or 0)
    if per_turn_max <= 0:
        per_turn_max = 25
    task["budget"] = float(task.get("budget", DEFAULT_BUDGET)) + turns / per_turn_max
    task["status"] = STATUS_OPEN
    task["park_notified"] = False
    save_task(node, task)
    return task


def list_tasks(node: str) -> list[dict]:
    root = tasks_dir(node)
    if not root.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(root.glob("*.json")):
        task = load_task(node, path.stem)
        if task is not None:
            out.append(task)
    return out


# ---------- parked messages (budget) ----------

def park_message(node: str, task_id: str, envelope: dict) -> Path:
    envelope = dict(envelope)
    envelope.setdefault("id", new_ulid())
    envelope.setdefault("queued_at", utc_now())
    path = parked_dir(node, task_id) / f"{envelope['id']}.json"
    atomic_write_json(path, envelope)
    return path


def parked_messages(node: str, task_id: str) -> list[Path]:
    root = parked_dir(node, task_id)
    if not root.is_dir():
        return []
    return sorted(f for f in root.iterdir() if f.is_file() and f.name.endswith(".json"))


def parked_count(node: str, task_id: str) -> int:
    return len(parked_messages(node, task_id))


# ---------- expiry (idle maintenance) ----------

def expire_tasks(node: str, older_than_seconds: float) -> list[str]:
    """Delete task ledgers (and their parked messages) idle longer than
    `older_than_seconds`. Returns the removed task ids."""
    from datetime import datetime, timezone

    cutoff = datetime.now(timezone.utc).timestamp() - older_than_seconds
    removed: list[str] = []
    for task in list_tasks(node):
        raw = task.get("updated_at") or task.get("created_at") or ""
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except ValueError:
            stamp = 0.0
        if stamp >= cutoff:
            continue
        task_id = task["id"]
        try:
            task_path(node, task_id).unlink()
        except OSError:
            continue
        shutil.rmtree(tasks_dir(node) / task_id, ignore_errors=True)
        removed.append(task_id)
    return removed
