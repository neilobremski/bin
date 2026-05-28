"""A8S transaction log — structured TSV for tracing messages through the system.

Append-only file at ~/.a8s/transactions.tsv. One line per routing event.
Columns are tab-separated, fixed-order, greppable.

Designed for debugging message flow end-to-end: trace a msg_id from
sender outbox → local routing → file transfer → remote publish → remote
receive → recipient wake.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

_COLUMNS = [
    "timestamp",    # ISO-8601 UTC
    "event",        # event type (ROUTED, RECEIVED, FILE_DELIVERED, etc.)
    "msg_id",       # envelope ULID
    "from",         # sender participant name
    "to",           # recipient participant name (or alias)
    "files",        # comma-separated filenames (or empty)
    "remote",       # remote id involved (or empty)
    "detail",       # short free-text (preview, error, etc.)
]

HEADER = "\t".join(_COLUMNS)


def _txlog_path() -> Path:
    override = os.environ.get("A8S_HOME")
    base = Path(override) if override else Path.home() / ".a8s"
    return base / "transactions.tsv"


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sanitize(val: str) -> str:
    """Strip tabs/newlines from field values."""
    return val.replace("\t", " ").replace("\n", " ").replace("\r", "")


def log(
    event: str,
    *,
    msg_id: str = "",
    sender: str = "",
    recipient: str = "",
    files: list[str] | None = None,
    remote: str = "",
    detail: str = "",
) -> None:
    """Append one transaction line."""
    files_str = ",".join(files) if files else ""
    fields = [
        _ts(),
        event,
        msg_id,
        sender,
        recipient,
        files_str,
        remote,
        _sanitize(detail)[:200],
    ]
    line = "\t".join(fields) + "\n"
    path = _txlog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", encoding="utf-8") as f:
            f.write(HEADER + "\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
