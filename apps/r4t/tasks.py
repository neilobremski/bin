"""Thread ledger — the conversation label that survives a batch turn.

Every message r4t releases carries a machine-stamped header line:

    [r4t task=<ulid> hop=<n> auto]

The header is stamped and stripped by r4t only — agents never see or copy
it. Incoming: parse + strip, adopt the thread id + hop. Missing header →
new thread ULID, hop 0. The `auto` flag is the message-class mark (RFC
3834's Auto-Submitted analog): a header WITHOUT it was written by a
deliberate hand.

A thread is a conversation label, not a budget. It exists so a reply can
be attributed to the exchange it answers, so the originator can be tracked
(answer-the-originator closure), and so a thread that goes quiet without
its originator hearing back can wake the leader. It never gates delivery:
every inbound message enqueues regardless of a thread's status. `task=` on
the wire is a name kept for compatibility; in prose it is a "thread".

Hop counts are stamped for telemetry (and the Phase 2 tree) but never cut
a message.
"""
from __future__ import annotations

import re
from pathlib import Path

from state import atomic_write_json, team_dir, utc_now
from ulid import new as new_ulid

HEADER_RE = re.compile(
    r"^\s*\[r4t\s+task=([0-9A-Za-z]{26})\s+hop=(\d+)(\s+auto)?\]\s*",
    re.IGNORECASE,
)

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"


def new_task_id() -> str:
    return new_ulid()


def parse_header(message: str) -> tuple[str | None, int, bool, str]:
    """Return (thread_id, hop, auto, body-with-header-stripped)."""
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
    """Collapse-key normalization: strip the r4t header, lowercase, collapse
    whitespace."""
    _, _, _, body = parse_header(text)
    return " ".join(body.lower().split())


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
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
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
        "answered": False,
    }


def ensure_task(node: str, task_id: str, creator: str) -> dict:
    task = load_task(node, task_id)
    if task is None:
        task = new_task(task_id, creator)
        save_task(node, task)
    return task


def close_task(node: str, task_id: str) -> None:
    """Mark a thread closed: its originator has had a substantive reply."""
    task = load_task(node, task_id)
    if task is None or task.get("status") == STATUS_CLOSED:
        return
    task["status"] = STATUS_CLOSED
    task["answered"] = True
    save_task(node, task)


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
    """Delete thread ledgers idle longer than `older_than_seconds`."""
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
