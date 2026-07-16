"""A8S transaction log — structured TSV for tracing messages through the system.

Append-only file at ~/.a8s/transactions.tsv. One line per routing event.
Columns are tab-separated, fixed-order, greppable.

Designed for debugging message flow end-to-end: trace a msg_id from
sender outbox -> local routing -> file transfer -> remote publish -> remote
receive -> recipient wake.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

__all__ = ["log", "read_events"]

Event = Literal[
    "ROUTED",
    "RECEIVED_REMOTE",
    "RESOLVED_REMOTE",
    "RECEIPT_PUBLISHED",
    "DELIVERY_RECEIPT",
    "FILE_DELIVERED",
    "FILE_UPLOAD_FAILED",
    "PUBLISHED",
    "DROPPED",
    "PROXY_DELIVERED",
]

_COLUMNS = [
    "timestamp",    # ISO-8601 UTC with milliseconds
    "event",        # event type (see Event literal above)
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
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _sanitize(val: str) -> str:
    """Strip tabs/newlines from field values."""
    return val.replace("\t", " ").replace("\n", " ").replace("\r", "")


def log(
    event: Event,
    *,
    msg_id: str = "",
    sender: str = "",
    recipient: str = "",
    files: list[str] | None = None,
    remote: str = "",
    detail: str = "",
) -> None:
    """Append one transaction line. Never raises — OSError is swallowed."""
    try:
        files_str = ",".join(files) if files else ""
        detail_truncated = detail[:200]
        fields = [
            _ts(),
            event,
            msg_id,
            sender,
            recipient,
            files_str,
            remote,
            detail_truncated,
        ]
        line = "\t".join(_sanitize(f) for f in fields) + "\n"
        path = _txlog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write(HEADER + "\n")
            f.write(line)
    except OSError:
        pass


def read_events(msg_id: str) -> list[dict[str, str]]:
    """Return transaction events correlated to one message ULID."""
    path = _txlog_path()
    if not path.is_file():
        return []
    events: list[dict[str, str]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                values = line.rstrip("\n").split("\t")
                if values == _COLUMNS or len(values) != len(_COLUMNS):
                    continue
                event = dict(zip(_COLUMNS, values))
                if event["msg_id"].upper() == msg_id.upper():
                    events.append(event)
    except OSError:
        return []
    return events
