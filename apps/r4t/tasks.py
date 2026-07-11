"""Task envelope + ledger — the governance core.

Every message r4t releases carries a machine-stamped header line:

    [r4t task=<ulid> hop=<n> auto]

The header is stamped and stripped by r4t only — agents never see or copy
it. Incoming: parse + strip, adopt task/hop. Missing header → new task
ULID, hop 0. The `auto` flag is the message-class mark (RFC 3834's
Auto-Submitted analog): a header WITHOUT it was written by a deliberate
hand, which resets the task's turn budget (docs/governance.md §9).

Turn budget is weighted by rig: each dispatch by a rig whose
`max_turns_per_task` is M consumes 1/M of the task's budget (which starts
at 1.0). A task run entirely by one rig therefore gets exactly
`max_turns_per_task` turns; mixed-rig chains pro-rate. Exhaustion closes
the task through one forced-synthesis leader turn — there is no human
approval gate.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from state import atomic_write_json, team_dir, utc_now
from ulid import new as new_ulid

HEADER_RE = re.compile(
    r"^\s*\[r4t\s+task=([0-9A-Za-z]{26})\s+hop=(\d+)(\s+auto)?\]\s*",
    re.IGNORECASE,
)
DEFAULT_BUDGET = 1.0
_EPSILON = 1e-9

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"


def new_task_id() -> str:
    return new_ulid()


def parse_header(message: str) -> tuple[str | None, int, bool, str]:
    """Return (task_id, hop, auto, body-with-header-stripped)."""
    match = HEADER_RE.match(message or "")
    if not match:
        return None, 0, False, (message or "").strip()
    task_id = match.group(1).upper()
    hop = int(match.group(2))
    auto = match.group(3) is not None
    return task_id, hop, auto, message[match.end():].strip()


def format_header(task_id: str, hop: int, *, auto: bool = False) -> str:
    return f"[r4t task={task_id} hop={hop}{' auto' if auto else ''}]"


def normalize_content(text: str) -> str:
    """Suppression-key normalization: strip the r4t header, lowercase,
    collapse whitespace."""
    _, _, _, body = parse_header(text)
    return " ".join(body.lower().split())


def pair_key(sender: str, to: str, content: str, *, kind: str = "pair") -> str:
    digest = hashlib.sha256(
        f"{sender.strip().lower()}|{to.strip().lower()}|{normalize_content(content)}".encode()
    ).hexdigest()[:16]
    return f"{kind}:{digest}"


# ---------- ledger ----------

def tasks_dir(node: str) -> Path:
    return team_dir(node) / "tasks"


def task_path(node: str, task_id: str) -> Path:
    return tasks_dir(node) / f"{task_id}.json"


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
        "cut_notified": False,
        "synthesized": False,
        "nudges": {},
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


def reset_budget(task: dict) -> bool:
    """The deliberate-decision rule: a human-origin message in the chain
    re-licenses the task. Returns True when anything actually changed."""
    changed = (
        float(task.get("used", 0.0)) > 0.0
        or task.get("status") != STATUS_OPEN
        or bool(task.get("synthesized"))
    )
    task["used"] = 0.0
    task["status"] = STATUS_OPEN
    task["synthesized"] = False
    task.pop("synthesis_state", None)
    return changed


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


# ---------- expiry (idle maintenance) ----------

def last_activity(task: dict) -> float:
    """Unix timestamp of the ledger's last write (0.0 when unparsable)."""
    from datetime import datetime

    raw = task.get("updated_at") or task.get("created_at") or ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def expire_tasks(node: str, older_than_seconds: float) -> list[str]:
    """Delete task ledgers idle longer than `older_than_seconds`."""
    from datetime import datetime, timezone

    cutoff = datetime.now(timezone.utc).timestamp() - older_than_seconds
    removed: list[str] = []
    for task in list_tasks(node):
        if last_activity(task) >= cutoff:
            continue
        try:
            task_path(node, task["id"]).unlink()
        except OSError:
            continue
        removed.append(task["id"])
    return removed
